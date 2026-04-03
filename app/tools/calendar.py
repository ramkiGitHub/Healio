"""
app/tools/calendar.py
=====================
Appointment scheduling tool — mock implementation for MVP, with a clear
interface for swapping in a Google Calendar integration.

Why this file exists
--------------------
Indian clinic appointment scheduling currently lacks a universal API.
For the MVP, Healio uses a mock calendar that returns realistic-looking
availability windows — no credentials, no setup, no API calls.

When the clinic is ready to connect a real calendar (Google Calendar,
Practo, etc.), the only change required is:
1. Implement a new class with the same three public methods.
2. Set ``CALENDAR_PROVIDER=google`` (or the new provider name) in ``.env``.
3. ``get_calendar_tool()`` will automatically return the new implementation.

Slot design
-----------
Slots are 30-minute windows, Monday–Saturday, 9:00 AM – 5:30 PM.
This matches the operating hours of most Indian primary-care clinics.
Sundays are excluded. Lunch break (1:00–2:00 PM) is excluded.

The mock implementation generates deterministic slots based on the
requested date — the same date always returns the same available slots,
making it safe and predictable for tests.

Google Calendar integration notes (Post-MVP)
--------------------------------------------
To add Google Calendar:
1. Create ``GoogleCalendarTool(BaseCalendarTool)`` in this file.
2. Use ``google-auth`` + ``googleapiclient`` to call the Calendar API.
3. ``get_available_slots()`` → ``calendar.freebusy().query()``
4. ``book_slot()`` → ``calendar.events().insert()``
5. ``cancel_slot()`` → ``calendar.events().delete()``
Set ``google_calendar_credentials_path`` and ``google_calendar_id``
in ``.env``.

Usage
-----
    from app.tools.calendar import get_calendar_tool

    tool = get_calendar_tool()
    slots = tool.get_available_slots(date_str="2026-04-10")
    booked = tool.book_slot(
        date_str="2026-04-10",
        time_str="10:00",
        patient_id="P001",
        reason="Follow-up for diabetes",
    )
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta
from functools import lru_cache

from app.config import settings
from app.constants import CalendarProvider
from app.exceptions import CalendarToolError
from app.logging_config import get_logger

log = get_logger(__name__)

# Clinic operating window
_CLINIC_OPEN: time = time(9, 0)
_CLINIC_CLOSE: time = time(17, 30)   # Last slot starts at 17:30; ends 18:00
_SLOT_DURATION_MINUTES: int = 30
_LUNCH_START: time = time(13, 0)
_LUNCH_END: time = time(14, 0)


# ── Data model ────────────────────────────────────────────────────────────────

class AppointmentSlot:
    """Represents a single bookable appointment slot.

    Attributes:
        date_str: ISO 8601 date string (e.g. ``"2026-04-10"``).
        time_str: 24-hour time string (e.g. ``"10:00"``).
        is_available: True if the slot is not yet booked.
        booking_ref: Opaque reference ID returned after booking.
                     ``None`` until the slot is booked.

    Example:
        >>> slot = AppointmentSlot(date_str="2026-04-10", time_str="10:00")
        >>> slot.is_available
        True
        >>> slot.label
        '10:00 AM on Friday, 10 Apr 2026'
    """

    def __init__(
        self,
        date_str: str,
        time_str: str,
        is_available: bool = True,
        booking_ref: str | None = None,
    ) -> None:
        self.date_str = date_str
        self.time_str = time_str
        self.is_available = is_available
        self.booking_ref = booking_ref

    @property
    def label(self) -> str:
        """Human-readable label for use in Telegram/WhatsApp messages.

        Returns:
            e.g. ``"10:00 AM on Friday, 10 Apr 2026"``
        """
        try:
            dt = datetime.strptime(f"{self.date_str} {self.time_str}", "%Y-%m-%d %H:%M")
            return dt.strftime("%-I:%M %p on %A, %-d %b %Y")
        except ValueError:
            return f"{self.time_str} on {self.date_str}"

    def to_dict(self) -> dict:
        """Serialise to a plain dict for LangGraph state storage.

        Returns:
            Dict with keys: ``date_str``, ``time_str``, ``is_available``,
            ``booking_ref``, ``label``.
        """
        return {
            "date_str": self.date_str,
            "time_str": self.time_str,
            "is_available": self.is_available,
            "booking_ref": self.booking_ref,
            "label": self.label,
        }


# ── Abstract base (interface contract) ───────────────────────────────────────

class BaseCalendarTool:
    """Abstract base class defining the interface for all calendar implementations.

    Subclass this to add a new calendar backend. The schedule_node in
    ``app/graph/nodes.py`` only calls the three methods declared here.

    All methods operate on ``date_str`` values in ``YYYY-MM-DD`` format
    and ``time_str`` values in ``HH:MM`` 24-hour format.
    """

    def get_available_slots(self, date_str: str) -> list[AppointmentSlot]:
        """Return a list of available (unbooked) slots for the given date.

        Args:
            date_str: ISO 8601 date string (``"YYYY-MM-DD"``).

        Returns:
            List of ``AppointmentSlot`` objects with ``is_available=True``.
            Returns an empty list for Sundays and invalid dates.

        Raises:
            CalendarToolError: On unrecoverable backend errors.
        """
        raise NotImplementedError

    def book_slot(
        self,
        date_str: str,
        time_str: str,
        patient_id: str,
        reason: str,
    ) -> AppointmentSlot:
        """Book a specific slot for a patient.

        Args:
            date_str: ISO 8601 date string (``"YYYY-MM-DD"``).
            time_str: 24-hour time string (``"HH:MM"``).
            patient_id: Patient identifier for the booking record.
            reason: Reason for the appointment (free text).

        Returns:
            The booked ``AppointmentSlot`` with ``is_available=False`` and
            a ``booking_ref`` populated.

        Raises:
            CalendarToolError: If the slot is not available or booking fails.
        """
        raise NotImplementedError

    def cancel_slot(self, booking_ref: str) -> bool:
        """Cancel a previously booked appointment.

        Args:
            booking_ref: The opaque reference ID returned by ``book_slot()``.

        Returns:
            True if the cancellation succeeded.

        Raises:
            CalendarToolError: If the booking reference is not found.
        """
        raise NotImplementedError


# ── Mock implementation ───────────────────────────────────────────────────────

class MockCalendarTool(BaseCalendarTool):
    """Deterministic mock calendar tool for MVP development and testing.

    Generates realistic-looking availability slots for any valid weekday.
    Uses a hash of the date to deterministically mark some slots as
    "already booked" — so the same date always shows the same availability.
    50% of slots are pre-marked as unavailable to simulate a busy clinic.

    Bookings are stored in an in-memory dict (``_bookings``) keyed by a
    ``booking_ref`` UUID. In-process bookings are visible within the same
    server process — they are not persisted across restarts.

    Attributes:
        _bookings: Dict of ``{booking_ref: AppointmentSlot}`` for in-session
                   tracking of mock bookings.

    Usage:
        tool = get_calendar_tool()
        slots = tool.get_available_slots("2026-04-10")
        booked = tool.book_slot("2026-04-10", "10:00", "P001", "Follow-up")
    """

    def __init__(self) -> None:
        self._bookings: dict[str, AppointmentSlot] = {}

    def get_available_slots(self, date_str: str) -> list[AppointmentSlot]:
        """Return deterministic available slots for a given date.

        Generates all 30-minute slots within clinic hours (9:00–17:30),
        excluding lunch (13:00–14:00) and Sundays.
        Uses a SHA-256 hash of the date to pseudo-randomly mark ~50% of
        slots as unavailable, simulating a realistic busy clinic schedule.

        Args:
            date_str: ISO 8601 date string (``"YYYY-MM-DD"``).

        Returns:
            List of ``AppointmentSlot`` objects where ``is_available=True``.
            Returns empty list for Sundays (``weekday() == 6``) and
            invalid date strings.

        Raises:
            CalendarToolError: If ``date_str`` cannot be parsed.

        Example:
            >>> tool = MockCalendarTool()
            >>> slots = tool.get_available_slots("2026-04-09")  # Thursday
            >>> all(s.is_available for s in slots)
            True
        """
        try:
            target_date = date.fromisoformat(date_str)
        except ValueError as exc:
            raise CalendarToolError(
                detail=f"Invalid date format '{date_str}'. Use YYYY-MM-DD.",
                provider="mock",
            ) from exc

        # No Sunday appointments
        if target_date.weekday() == 6:
            log.info("calendar_no_slots_sunday", date=date_str)
            return []

        all_slots = self._generate_all_slots(date_str)

        # Exclude slots already booked in the in-memory registry
        booked_times_for_date = {
            s.time_str for s in self._bookings.values() if s.date_str == date_str
        }
        available = [
            s for s in all_slots
            if s.is_available and s.time_str not in booked_times_for_date
        ]

        log.info(
            "calendar_slots_queried",
            date=date_str,
            available_count=len(available),
            total_count=len(all_slots),
        )
        return available

    def book_slot(
        self,
        date_str: str,
        time_str: str,
        patient_id: str,
        reason: str,
    ) -> AppointmentSlot:
        """Book a slot and store the booking in the in-memory registry.

        Validates that the slot exists and is still available before booking.
        Generates a deterministic ``booking_ref`` from ``date_str``,
        ``time_str``, and ``patient_id`` for reproducibility in tests.

        Args:
            date_str: ISO 8601 date string (``"YYYY-MM-DD"``).
            time_str: 24-hour time string(``"HH:MM"``).
            patient_id: Patient identifier for the booking record.
            reason: Reason for the appointment.

        Returns:
            A booked ``AppointmentSlot`` with ``is_available=False`` and
            ``booking_ref`` populated.

        Raises:
            CalendarToolError: If the requested slot is not available or
                               the date/time format is invalid.

        Example:
            >>> tool = MockCalendarTool()
            >>> slot = tool.book_slot("2026-04-09", "09:00", "P001", "Follow-up")
            >>> slot.booking_ref is not None
            True
        """
        available = self.get_available_slots(date_str)
        available_times = {s.time_str for s in available}

        if time_str not in available_times:
            raise CalendarToolError(
                detail=(
                    f"Slot {time_str} on {date_str} is not available. "
                    f"Available times: {sorted(available_times)}"
                ),
                provider="mock",
            )

        booking_ref = self._generate_booking_ref(date_str, time_str, patient_id)
        slot = AppointmentSlot(
            date_str=date_str,
            time_str=time_str,
            is_available=False,
            booking_ref=booking_ref,
        )
        self._bookings[booking_ref] = slot

        log.info(
            "calendar_slot_booked",
            date=date_str,
            time=time_str,
            patient_id=patient_id,
            booking_ref=booking_ref,
            reason=reason[:80],
        )
        return slot

    def cancel_slot(self, booking_ref: str) -> bool:
        """Cancel a previously booked appointment.

        Removes the booking from the in-memory registry.

        Args:
            booking_ref: The reference ID returned by ``book_slot()``.

        Returns:
            True if the cancellation succeeded.

        Raises:
            CalendarToolError: If the booking reference is not found.

        Example:
            >>> tool = MockCalendarTool()
            >>> slot = tool.book_slot("2026-04-09", "09:00", "P001", "Test")
            >>> tool.cancel_slot(slot.booking_ref)
            True
        """
        if booking_ref not in self._bookings:
            raise CalendarToolError(
                detail=f"Booking reference '{booking_ref}' not found.",
                provider="mock",
            )
        del self._bookings[booking_ref]
        log.info("calendar_slot_cancelled", booking_ref=booking_ref)
        return True

    # ── Private helpers ───────────────────────────────────────────────────────

    def _generate_all_slots(self, date_str: str) -> list[AppointmentSlot]:
        """Generate all 30-minute slots for a clinic day with mock availability.

        Lunch (13:00–14:00) is excluded. ~50% of slots are pre-marked
        unavailable using a deterministic hash of the date, simulating a
        busy schedule.

        Args:
            date_str: ISO 8601 date string.

        Returns:
            Full list of ``AppointmentSlot`` objects for the day.
        """
        slots: list[AppointmentSlot] = []
        current = datetime.combine(date.fromisoformat(date_str), _CLINIC_OPEN)
        clinic_close = datetime.combine(date.fromisoformat(date_str), _CLINIC_CLOSE)

        slot_index = 0
        while current + timedelta(minutes=_SLOT_DURATION_MINUTES) <= clinic_close + timedelta(minutes=1):
            slot_time = current.time()

            # Exclude lunch break
            if not (_LUNCH_START <= slot_time < _LUNCH_END):
                is_available = self._is_slot_available_mock(date_str, current.strftime("%H:%M"), slot_index)
                slots.append(AppointmentSlot(
                    date_str=date_str,
                    time_str=current.strftime("%H:%M"),
                    is_available=is_available,
                ))

            current += timedelta(minutes=_SLOT_DURATION_MINUTES)
            slot_index += 1

        return slots

    @staticmethod
    def _is_slot_available_mock(date_str: str, time_str: str, slot_index: int) -> bool:
        """Deterministically decide if a slot is "available" in the mock.

        Uses SHA-256 hash of ``date_str + time_str`` so the same date
        always produces the same availability pattern. Approximately 50%
        of slots are marked available.

        Args:
            date_str: Date string.
            time_str: Time string.
            slot_index: 0-based position of the slot in the day.

        Returns:
            True if the slot should appear available.
        """
        key = f"{date_str}:{time_str}"
        hash_int = int(hashlib.sha256(key.encode()).hexdigest(), 16)
        return (hash_int % 2) == 0

    @staticmethod
    def _generate_booking_ref(date_str: str, time_str: str, patient_id: str) -> str:
        """Generate a deterministic booking reference string.

        Args:
            date_str: Appointment date.
            time_str: Appointment time.
            patient_id: Patient identifier.

        Returns:
            An 8-character hex booking reference e.g. ``"1a2b3c4d"``.
        """
        key = f"{date_str}:{time_str}:{patient_id}"
        return hashlib.sha256(key.encode()).hexdigest()[:8]


# ── Factory ───────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_calendar_tool() -> BaseCalendarTool:
    """Return the active calendar tool based on the ``CALENDAR_PROVIDER`` setting.

    Uses ``lru_cache`` so only one instance is created per process.
    Currently only ``CalendarProvider.MOCK`` is active.

    Returns:
        A ``MockCalendarTool`` (MVP) or a future ``GoogleCalendarTool``.

    Raises:
        CalendarToolError: If an unsupported provider is configured.

    Example:
        >>> tool = get_calendar_tool()
        >>> isinstance(tool, MockCalendarTool)
        True
    """
    provider = settings.calendar_provider
    log.info("calendar_tool_selected", provider=provider)

    if provider == CalendarProvider.MOCK:
        return MockCalendarTool()

    # GOOGLE CALENDAR PLACEHOLDER
    # To activate:
    # 1. pip install google-auth google-auth-httplib2 google-api-python-client
    # 2. Add GoogleCalendarTool(BaseCalendarTool) class above
    # 3. Set CALENDAR_PROVIDER=google, GOOGLE_CALENDAR_CREDENTIALS_PATH,
    #    and GOOGLE_CALENDAR_ID in .env
    # if provider == CalendarProvider.GOOGLE:
    #     return GoogleCalendarTool(
    #         credentials_path=settings.google_calendar_credentials_path,
    #         calendar_id=settings.google_calendar_id,
    #     )

    raise CalendarToolError(
        detail=f"Unsupported calendar provider: '{provider}'. "
               "Check CALENDAR_PROVIDER in your .env file.",
        provider=str(provider),
    )
