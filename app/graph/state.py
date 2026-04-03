"""
app/graph/state.py
==================
LangGraph state definition for the Healio conversation pipeline.

Why this file exists
--------------------
LangGraph is a stateful graph framework — every node in the graph reads from
and writes to a shared ``State`` object. Defining the state in a single typed
file makes it easy to understand exactly what data flows through the pipeline
at any point.

Think of ``HealioState`` as the "working memory" for a single patient
conversation. LangGraph persists this state between turns using the SQLite
checkpointer, giving Healio true multi-turn memory.

How state updates work in LangGraph
-------------------------------------
- Most fields use ``operator.add`` as their reducer — meaning new values are
  **appended** to the existing list (e.g., ``messages`` grows each turn).
- Fields marked without a reducer are **overwritten** on each update.
- Each node returns a dict containing only the fields it wants to update.
  Fields it does not return are left unchanged.

How to extend
-------------
Add a new field to ``HealioState`` when a new piece of data needs to flow
through the graph. Always add a docstring explaining what the field holds
and which node is responsible for populating it.
"""

import operator
from typing import Annotated

from langchain_core.messages import BaseMessage
from typing_extensions import TypedDict

from app.constants import IntentType, SeverityLevel


class PatientProfile(TypedDict, total=False):
    """Patient profile data retrieved from the EHR tool.

    Populated by the Profile Lookup Node using the Mock EHR tool.
    All fields are optional (``total=False``) because a profile may not
    exist for a patient who contacts the clinic for the first time.

    Attributes:
        patient_id: Unique patient identifier (matches the channel sender ID).
        name: Patient's full name.
        age: Patient's age in years.
        conditions: List of known medical conditions (e.g., ["diabetes", "hypertension"]).
        allergies: List of known drug/food allergies (e.g., ["penicillin"]).
        medications: List of current medications (e.g., ["metformin 500mg"]).
        last_visit: Date of the patient's last clinic visit (ISO format string).
        blood_group: Patient's blood group (e.g., "O+").
    """

    patient_id: str
    name: str
    age: int
    conditions: list[str]
    allergies: list[str]
    medications: list[str]
    last_visit: str
    blood_group: str


class AppointmentContext(TypedDict, total=False):
    """Working state for an in-progress appointment booking conversation.

    Populated progressively by the Schedule Node as the patient provides
    details across multiple conversation turns.

    Attributes:
        preferred_date: Patient-requested appointment date (e.g., "tomorrow", "Monday").
        preferred_time: Patient-requested time slot (e.g., "morning", "2pm").
        reason: Reason for the appointment (e.g., "follow-up", "fever").
        confirmed_slot: ISO datetime string of the booked slot, set after confirmation.
        booking_step: Current step in the booking dialogue flow.
                      Values: "collect_date" | "collect_time" | "collect_reason" |
                      "confirm" | "booked"
    """

    preferred_date: str
    preferred_time: str
    reason: str
    confirmed_slot: str
    booking_step: str


class HealioState(TypedDict):
    """The complete state object flowing through the Healio LangGraph pipeline.

    Every node in the graph reads from and writes to this TypedDict.
    LangGraph persists the full state between conversation turns using
    the SQLite checkpointer, keyed by ``session_id``.

    Attributes:
        session_id: Unique identifier for this conversation session.
                    Format: ``{channel}:{sender_id}`` (e.g. ``telegram:123456``).
                    Used as the LangGraph thread ID for memory checkpointing.
                    **Set at graph entry; never modified by nodes.**

        patient_id: Patient identifier, derived from the channel sender ID.
                    Used to look up the patient profile in the EHR store.
                    **Set at graph entry; never modified by nodes.**

        messages: Full conversation history as LangChain ``BaseMessage`` objects.
                  Grows each turn — HumanMessage (patient) and AIMessage (Healio)
                  are appended. The ``operator.add`` reducer ensures messages
                  accumulate rather than being overwritten.
                  **Appended to by nodes that generate a response.**

        patient_profile: Patient profile fetched from the EHR tool.
                          Empty dict until the Profile Lookup Node populates it.
                          **Set by ProfileLookupNode.**

        intent: Classified intent of the latest patient message.
                One of the ``IntentType`` enum values.
                **Set by RouterNode on each turn.**

        severity: Clinical severity of the patient's reported symptoms.
                  One of the ``SeverityLevel`` enum values.
                  **Set by RouterNode on each turn.**

        appointment_context: Working state for an in-progress appointment booking.
                              Accumulates over multiple turns via the Schedule Node.
                              Empty dict initially.
                              **Set/updated by ScheduleNode.**

        flags: Miscellaneous boolean flags for cross-node signalling.
               Current flags:
               - ``human_loop_triggered`` (bool): True after the HITL alert fires.
               - ``profile_loaded`` (bool): True after EHR profile is fetched.
               - ``allergy_flagged`` (bool): True if a medication/allergy conflict
                 was detected.
               **Set by any node that needs to signal state to downstream nodes.**

        error_message: Human-readable error message to surface to the patient
                       when an unrecoverable error occurs in a node.
                       None when everything is working normally.
                       **Set by nodes on error; cleared after the error response
                       is sent.**
    """

    # ── Immutable session context ──────────────────────────────────────────────
    session_id: str
    patient_id: str

    # ── Conversation history (append-only) ────────────────────────────────────
    messages: Annotated[list[BaseMessage], operator.add]

    # ── Patient data ──────────────────────────────────────────────────────────
    patient_profile: PatientProfile

    # ── Routing context (set each turn by RouterNode) ─────────────────────────
    intent: IntentType
    severity: SeverityLevel

    # ── Appointment booking dialogue state ────────────────────────────────────
    appointment_context: AppointmentContext

    # ── Cross-node signalling flags ────────────────────────────────────────────
    flags: dict[str, bool]

    # ── Error surfacing ────────────────────────────────────────────────────────
    error_message: str | None

    # ── BioBERT NER output (set each turn by RouterNode) ──────────────────────
    # Each element is a dict with keys: text, label, score.
    # Stored as plain dicts (not MedicalEntity dataclass) to remain
    # JSON-serialisable for the SQLite LangGraph checkpointer.
    biobert_entities: list[dict]


def create_initial_state(session_id: str, patient_id: str, first_message: str) -> HealioState:
    """Create a fresh HealioState for the first turn of a new conversation.

    Called by ``run_graph()`` in ``app/graph/graph.py`` when a session_id
    has no existing checkpoint in the SQLite store.

    Args:
        session_id: Unique session identifier (``{channel}:{sender_id}``).
        patient_id: Patient identifier from the messaging channel.
        first_message: The patient's first message text.

    Returns:
        A ``HealioState`` with all fields set to their initial values,
        ready to be fed into the LangGraph pipeline.

    Example:
        >>> state = create_initial_state(
        ...     session_id="telegram:123",
        ...     patient_id="123",
        ...     first_message="Hello, I need help",
        ... )
        >>> state["intent"]
        <IntentType.UNKNOWN: 'unknown'>
    """
    from langchain_core.messages import HumanMessage

    return HealioState(
        session_id=session_id,
        patient_id=patient_id,
        messages=[HumanMessage(content=first_message)],
        biobert_entities=[],
        patient_profile=PatientProfile(),
        intent=IntentType.UNKNOWN,
        severity=SeverityLevel.ROUTINE,
        appointment_context=AppointmentContext(),
        flags={
            "human_loop_triggered": False,
            "profile_loaded": False,
            "allergy_flagged": False,
        },
        error_message=None,
    )
