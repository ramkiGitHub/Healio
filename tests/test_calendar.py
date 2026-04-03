"""Tests for app/tools/calendar.py — MockCalendarTool and helpers.

Covers:
- AppointmentSlot dataclass methods
- MockCalendarTool.get_available_slots() — valid weekday, Sunday (empty), past
- MockCalendarTool.book_slot() — success path, double-booking, unavailable slot
- MockCalendarTool.cancel_slot() — success, unknown booking ref
- _build_calendar_context() helper in nodes.py
- _check_allergy_conflict() helper in nodes.py
- get_calendar_tool() singleton / lru_cache behaviour
"""

from __future__ import annotations

import pytest

from app.exceptions import CalendarToolError
from app.tools.calendar import (
    AppointmentSlot,
    MockCalendarTool,
    get_calendar_tool,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def tool() -> MockCalendarTool:
    """Fresh MockCalendarTool instance (not the cached singleton)."""
    return MockCalendarTool()


# ── AppointmentSlot ───────────────────────────────────────────────────────────


class TestAppointmentSlot:
    def test_to_dict_contains_required_keys(self) -> None:
        slot = AppointmentSlot(
            date_str="2026-05-05",
            time_str="09:00",
            is_available=True,
            booking_ref=None,
        )
        d = slot.to_dict()
        assert set(d) >= {"date_str", "time_str", "is_available", "booking_ref", "label"}

    def test_label_format(self) -> None:
        slot = AppointmentSlot(
            date_str="2026-05-05",
            time_str="09:00",
            is_available=True,
            booking_ref=None,
        )
        # Label should mention the time and date in some human-readable form
        assert "09:00" in slot.label or "9:00" in slot.label

    def test_to_dict_booking_ref_none(self) -> None:
        slot = AppointmentSlot(
            date_str="2026-05-05",
            time_str="10:00",
            is_available=True,
            booking_ref=None,
        )
        assert slot.to_dict()["booking_ref"] is None

    def test_to_dict_with_booking_ref(self) -> None:
        slot = AppointmentSlot(
            date_str="2026-05-05",
            time_str="10:00",
            is_available=False,
            booking_ref="abc12345",
        )
        assert slot.to_dict()["booking_ref"] == "abc12345"


# ── MockCalendarTool.get_available_slots ──────────────────────────────────────


class TestGetAvailableSlots:
    def test_monday_returns_slots(self, tool: MockCalendarTool) -> None:
        # 2026-05-04 is a Monday
        slots = tool.get_available_slots("2026-05-04")
        assert len(slots) > 0

    def test_sunday_returns_empty(self, tool: MockCalendarTool) -> None:
        # 2026-05-03 is a Sunday
        slots = tool.get_available_slots("2026-05-03")
        assert slots == []

    def test_slots_are_available(self, tool: MockCalendarTool) -> None:
        slots = tool.get_available_slots("2026-05-04")
        assert all(s.is_available for s in slots)

    def test_no_lunch_slot(self, tool: MockCalendarTool) -> None:
        # 13:00 slot should never appear (lunch break)
        slots = tool.get_available_slots("2026-05-04")
        times = {s.time_str for s in slots}
        assert "13:00" not in times

    def test_slot_times_in_business_hours(self, tool: MockCalendarTool) -> None:
        slots = tool.get_available_slots("2026-05-04")
        for s in slots:
            hour = int(s.time_str.split(":")[0])
            assert 9 <= hour < 18

    def test_deterministic_output(self, tool: MockCalendarTool) -> None:
        """Same date always returns same set of slot times."""
        first = [s.time_str for s in tool.get_available_slots("2026-06-01")]
        second = [s.time_str for s in tool.get_available_slots("2026-06-01")]
        assert first == second

    def test_invalid_date_raises(self, tool: MockCalendarTool) -> None:
        with pytest.raises((CalendarToolError, ValueError)):
            tool.get_available_slots("not-a-date")

    def test_returns_list(self, tool: MockCalendarTool) -> None:
        result = tool.get_available_slots("2026-05-04")
        assert isinstance(result, list)

    def test_all_slots_have_correct_date(self, tool: MockCalendarTool) -> None:
        slots = tool.get_available_slots("2026-05-05")
        assert all(s.date_str == "2026-05-05" for s in slots)

    def test_saturday_returns_slots(self, tool: MockCalendarTool) -> None:
        # 2026-05-02 is a Saturday
        slots = tool.get_available_slots("2026-05-02")
        assert len(slots) > 0


# ── MockCalendarTool.book_slot ────────────────────────────────────────────────


class TestBookSlot:
    def _get_bookable_slot(self, tool: MockCalendarTool, date_str: str) -> str:
        """Return the time_str of the first available slot on date_str."""
        slots = tool.get_available_slots(date_str)
        assert slots, f"No available slots on {date_str} to test with"
        return slots[0].time_str

    def test_book_slot_returns_slot(self, tool: MockCalendarTool) -> None:
        date_str = "2026-05-04"
        time_str = self._get_bookable_slot(tool, date_str)
        slot = tool.book_slot(date_str, time_str, "P001", "Checkup")
        assert slot.date_str == date_str
        assert slot.time_str == time_str
        assert slot.booking_ref is not None
        assert not slot.is_available

    def test_book_slot_stores_booking(self, tool: MockCalendarTool) -> None:
        date_str = "2026-05-04"
        time_str = self._get_bookable_slot(tool, date_str)
        slot = tool.book_slot(date_str, time_str, "P001", "Checkup")
        # Slot should now be unavailable
        remaining = tool.get_available_slots(date_str)
        booked_times = {s.time_str for s in remaining}
        assert time_str not in booked_times

    def test_book_unavailable_slot_raises(self, tool: MockCalendarTool) -> None:
        date_str = "2026-05-04"
        time_str = self._get_bookable_slot(tool, date_str)
        # Book once — succeeds
        tool.book_slot(date_str, time_str, "P001", "Checkup")
        # Book same slot again — should raise
        with pytest.raises(CalendarToolError):
            tool.book_slot(date_str, time_str, "P002", "Follow-up")

    def test_book_nonexistent_time_raises(self, tool: MockCalendarTool) -> None:
        with pytest.raises(CalendarToolError):
            tool.book_slot("2026-05-04", "03:00", "P001", "Midnight visit")

    def test_book_sunday_raises(self, tool: MockCalendarTool) -> None:
        # 2026-05-03 is a Sunday — no slots exist
        with pytest.raises(CalendarToolError):
            tool.book_slot("2026-05-03", "10:00", "P001", "Weekend")

    def test_booking_ref_is_8_char_hex(self, tool: MockCalendarTool) -> None:
        date_str = "2026-05-04"
        time_str = self._get_bookable_slot(tool, date_str)
        slot = tool.book_slot(date_str, time_str, "P001", "Test")
        ref = slot.booking_ref
        assert ref is not None
        assert len(ref) == 8
        int(ref, 16)  # must be valid hex


# ── MockCalendarTool.cancel_slot ──────────────────────────────────────────────


class TestCancelSlot:
    def _book_a_slot(self, tool: MockCalendarTool) -> AppointmentSlot:
        date_str = "2026-05-04"
        slots = tool.get_available_slots(date_str)
        assert slots
        return tool.book_slot(date_str, slots[0].time_str, "P001", "Checkup")

    def test_cancel_slot_returns_true(self, tool: MockCalendarTool) -> None:
        slot = self._book_a_slot(tool)
        assert tool.cancel_slot(slot.booking_ref) is True  # type: ignore[arg-type]

    def test_cancel_frees_slot(self, tool: MockCalendarTool) -> None:
        slot = self._book_a_slot(tool)
        tool.cancel_slot(slot.booking_ref)  # type: ignore[arg-type]
        # After cancellation the slot should appear available again
        available = tool.get_available_slots(slot.date_str)
        available_times = {s.time_str for s in available}
        assert slot.time_str in available_times

    def test_cancel_unknown_ref_raises(self, tool: MockCalendarTool) -> None:
        with pytest.raises(CalendarToolError):
            tool.cancel_slot("00000000")


# ── get_calendar_tool singleton ───────────────────────────────────────────────


class TestGetCalendarTool:
    def test_returns_mock_calendar_tool(self) -> None:
        t = get_calendar_tool()
        assert isinstance(t, MockCalendarTool)

    def test_singleton_same_instance(self) -> None:
        t1 = get_calendar_tool()
        t2 = get_calendar_tool()
        assert t1 is t2


# ── _check_allergy_conflict helper ────────────────────────────────────────────


class TestCheckAllergyConflict:
    """Tests for the private helper exposed via nodes.py."""

    def _fn(self, message: str, allergies: list[str]) -> list[str]:
        from app.graph.nodes import _check_allergy_conflict  # noqa: PLC0415

        return _check_allergy_conflict(message, allergies)

    def test_detects_match(self) -> None:
        result = self._fn("can I take penicillin?", ["Penicillin"])
        assert "Penicillin" in result

    def test_case_insensitive(self) -> None:
        result = self._fn("PENICILLIN dosage?", ["penicillin"])
        assert "penicillin" in result

    def test_no_match_returns_empty(self) -> None:
        result = self._fn("headache and fever", ["Penicillin", "Aspirin"])
        assert result == []

    def test_multiple_matches(self) -> None:
        result = self._fn("aspirin and ibuprofen together?", ["Aspirin", "Ibuprofen"])
        assert len(result) == 2

    def test_empty_message_returns_empty(self) -> None:
        assert self._fn("", ["Penicillin"]) == []

    def test_empty_allergies_returns_empty(self) -> None:
        assert self._fn("take penicillin", []) == []

    def test_partial_substring_match(self) -> None:
        # "amoxicillin" contains "mox" — but only "amoxicillin" full word matters
        result = self._fn("amoxicillin 500mg", ["amoxicillin"])
        assert "amoxicillin" in result

    def test_no_false_positive_on_unrelated(self) -> None:
        result = self._fn("i have asthma", ["Penicillin", "Aspirin"])
        assert result == []


# ── _build_calendar_context helper ───────────────────────────────────────────


class TestBuildCalendarContext:
    """Smoke-test _build_calendar_context from nodes.py."""

    def test_returns_string(self) -> None:
        from app.graph.nodes import _build_calendar_context  # noqa: PLC0415

        ctx = _build_calendar_context()
        assert isinstance(ctx, str)
        assert len(ctx) > 0

    def test_contains_slot_info(self) -> None:
        from app.graph.nodes import _build_calendar_context  # noqa: PLC0415

        ctx = _build_calendar_context()
        # Should mention a time like "09:00" or "available" or "slot"
        assert any(k in ctx.lower() for k in ("09:", "available", "slot", "monday", "tuesday"))
