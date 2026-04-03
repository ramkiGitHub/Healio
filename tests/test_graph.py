"""
tests/test_graph.py
===================
Tests for the LangGraph pipeline: routing logic, node behaviour,
state transitions, and severity classification.

These tests use mocked LLM calls to run fast and avoid API costs
during CI. Integration tests using real OpenAI calls can be run
separately with: pytest -m integration

Run with:
    pytest tests/test_graph.py -v
"""

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.constants import IntentType, SeverityLevel
from app.graph.edges import (
    route_after_emergency,
    route_after_profile_lookup,
    route_after_router,
)
from app.graph.nodes import (
    _classify_severity_by_keywords,
    _build_patient_context,
    _get_latest_human_message,
    _max_severity,
    _parse_router_response,
)
from app.graph.state import HealioState, create_initial_state


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def base_state() -> HealioState:
    """A minimal valid HealioState for testing."""
    return create_initial_state(
        session_id="telegram:123",
        patient_id="123",
        first_message="Hello",
    )


@pytest.fixture
def emergency_state(base_state: HealioState) -> HealioState:
    """A state where severity has been set to EMERGENCY."""
    return {
        **base_state,
        "intent": IntentType.EMERGENCY,
        "severity": SeverityLevel.EMERGENCY,
    }


@pytest.fixture
def profile_loaded_state(base_state: HealioState) -> HealioState:
    """A state where the patient profile has been loaded."""
    return {
        **base_state,
        "flags": {"profile_loaded": True, "human_loop_triggered": False, "allergy_flagged": False},
        "patient_profile": {"name": "Ravi Kumar", "age": 35},
    }


# ── create_initial_state ───────────────────────────────────────────────────────

class TestCreateInitialState:
    """Tests for the create_initial_state() factory function."""

    def test_session_id_set(self, base_state: HealioState) -> None:
        """session_id is set correctly."""
        assert base_state["session_id"] == "telegram:123"

    def test_patient_id_set(self, base_state: HealioState) -> None:
        """patient_id is set correctly."""
        assert base_state["patient_id"] == "123"

    def test_first_message_in_messages(self, base_state: HealioState) -> None:
        """The first message is added to the messages list as a HumanMessage."""
        assert len(base_state["messages"]) == 1
        assert isinstance(base_state["messages"][0], HumanMessage)
        assert base_state["messages"][0].content == "Hello"

    def test_default_intent_is_unknown(self, base_state: HealioState) -> None:
        """Default intent is UNKNOWN."""
        assert base_state["intent"] == IntentType.UNKNOWN

    def test_default_severity_is_routine(self, base_state: HealioState) -> None:
        """Default severity is ROUTINE."""
        assert base_state["severity"] == SeverityLevel.ROUTINE

    def test_flags_initialised(self, base_state: HealioState) -> None:
        """Flags dict is initialised with all expected keys set to False."""
        assert base_state["flags"]["human_loop_triggered"] is False
        assert base_state["flags"]["profile_loaded"] is False
        assert base_state["flags"]["allergy_flagged"] is False

    def test_error_message_is_none(self, base_state: HealioState) -> None:
        """error_message is None by default."""
        assert base_state["error_message"] is None


# ── Severity keyword classification ───────────────────────────────────────────

class TestClassifySeverityByKeywords:
    """Tests for rule-based severity classification."""

    def test_chest_pain_is_emergency(self) -> None:
        """'chest pain' triggers EMERGENCY severity."""
        assert _classify_severity_by_keywords("I have chest pain") == SeverityLevel.EMERGENCY

    def test_stroke_is_emergency(self) -> None:
        """'stroke' triggers EMERGENCY severity."""
        assert _classify_severity_by_keywords("I think I'm having a stroke") == SeverityLevel.EMERGENCY

    def test_high_fever_is_urgent(self) -> None:
        """'high fever' triggers URGENT severity."""
        assert _classify_severity_by_keywords("I have had a high fever since morning") == SeverityLevel.URGENT

    def test_routine_query_is_routine(self) -> None:
        """A normal query returns ROUTINE severity."""
        assert _classify_severity_by_keywords("What are the clinic hours?") == SeverityLevel.ROUTINE

    def test_case_insensitive(self) -> None:
        """Keyword matching is case-insensitive."""
        assert _classify_severity_by_keywords("CHEST PAIN") == SeverityLevel.EMERGENCY

    def test_emergency_takes_priority_over_urgent(self) -> None:
        """If both emergency and urgent keywords present, EMERGENCY wins."""
        assert _classify_severity_by_keywords("severe chest pain with high fever") == SeverityLevel.EMERGENCY


# ── _max_severity ──────────────────────────────────────────────────────────────

class TestMaxSeverity:
    """Tests for severity level comparison."""

    def test_emergency_beats_routine(self) -> None:
        assert _max_severity(SeverityLevel.EMERGENCY, SeverityLevel.ROUTINE) == SeverityLevel.EMERGENCY

    def test_urgent_beats_routine(self) -> None:
        assert _max_severity(SeverityLevel.ROUTINE, SeverityLevel.URGENT) == SeverityLevel.URGENT

    def test_same_levels_returns_either(self) -> None:
        assert _max_severity(SeverityLevel.URGENT, SeverityLevel.URGENT) == SeverityLevel.URGENT


# ── _parse_router_response ─────────────────────────────────────────────────────

class TestParseRouterResponse:
    """Tests for parsing the LLM's JSON routing response."""

    def test_valid_json_parsed(self) -> None:
        """Valid JSON response is parsed correctly."""
        intent, severity = _parse_router_response(
            '{"intent": "appointment", "severity": "routine"}',
            session_id="test",
        )
        assert intent == IntentType.APPOINTMENT
        assert severity == SeverityLevel.ROUTINE

    def test_json_with_markdown_fences(self) -> None:
        """JSON wrapped in markdown code fences is unwrapped and parsed."""
        response = "```json\n{\"intent\": \"query\", \"severity\": \"urgent\"}\n```"
        intent, severity = _parse_router_response(response, session_id="test")
        assert intent == IntentType.QUERY
        assert severity == SeverityLevel.URGENT

    def test_invalid_json_returns_defaults(self) -> None:
        """Invalid JSON falls back to UNKNOWN intent and ROUTINE severity."""
        intent, severity = _parse_router_response("not json at all", session_id="test")
        assert intent == IntentType.UNKNOWN
        assert severity == SeverityLevel.ROUTINE

    def test_unknown_intent_value_falls_back(self) -> None:
        """An unrecognised intent string falls back to UNKNOWN."""
        intent, _ = _parse_router_response(
            '{"intent": "something_made_up", "severity": "routine"}',
            session_id="test",
        )
        assert intent == IntentType.UNKNOWN


# ── Routing functions ──────────────────────────────────────────────────────────

class TestRouteAfterRouter:
    """Tests for the route_after_router() conditional edge function."""

    def test_emergency_severity_routes_to_emergency(self, base_state: HealioState) -> None:
        """EMERGENCY severity always routes to emergency node."""
        state = {**base_state, "severity": SeverityLevel.EMERGENCY}
        assert route_after_router(state) == "emergency"

    def test_appointment_intent_routes_to_schedule(
        self, profile_loaded_state: HealioState
    ) -> None:
        """APPOINTMENT intent routes to schedule node."""
        state = {
            **profile_loaded_state,
            "intent": IntentType.APPOINTMENT,
            "severity": SeverityLevel.ROUTINE,
        }
        assert route_after_router(state) == "schedule"

    def test_profile_not_loaded_routes_to_profile_lookup(
        self, base_state: HealioState
    ) -> None:
        """When profile is not loaded, routes to profile_lookup regardless of intent."""
        state = {
            **base_state,
            "intent": IntentType.QUERY,
            "severity": SeverityLevel.ROUTINE,
            "flags": {"profile_loaded": False},
        }
        assert route_after_router(state) == "profile_lookup"

    def test_query_with_loaded_profile_routes_to_general_qa(
        self, profile_loaded_state: HealioState
    ) -> None:
        """QUERY intent with profile loaded routes to general_qa."""
        state = {
            **profile_loaded_state,
            "intent": IntentType.QUERY,
            "severity": SeverityLevel.ROUTINE,
        }
        assert route_after_router(state) == "general_qa"

    def test_chitchat_routes_to_general_qa(
        self, profile_loaded_state: HealioState
    ) -> None:
        """CHITCHAT intent routes to general_qa."""
        state = {
            **profile_loaded_state,
            "intent": IntentType.CHITCHAT,
            "severity": SeverityLevel.ROUTINE,
        }
        assert route_after_router(state) == "general_qa"


class TestRouteAfterProfileLookup:
    """Tests for the route_after_profile_lookup() conditional edge function."""

    def test_profile_intent_routes_to_qa(self, base_state: HealioState) -> None:
        """PROFILE intent routes to general_qa after profile is loaded."""
        state = {**base_state, "intent": IntentType.PROFILE}
        assert route_after_profile_lookup(state) == "general_qa"

    def test_appointment_intent_routes_to_schedule(self, base_state: HealioState) -> None:
        """APPOINTMENT intent routes to schedule after profile is loaded."""
        state = {**base_state, "intent": IntentType.APPOINTMENT}
        assert route_after_profile_lookup(state) == "schedule"

    def test_query_intent_routes_to_qa(self, base_state: HealioState) -> None:
        """QUERY intent routes to general_qa."""
        state = {**base_state, "intent": IntentType.QUERY}
        assert route_after_profile_lookup(state) == "general_qa"


class TestRouteAfterEmergency:
    """Tests for the route_after_emergency() conditional edge function."""

    def test_always_returns_end(self, emergency_state: HealioState) -> None:
        """Emergency node always terminates the graph."""
        from langgraph.graph import END

        result = route_after_emergency(emergency_state)
        assert result == END


# ── _build_patient_context ─────────────────────────────────────────────────────

class TestBuildPatientContext:
    """Tests for LLM patient context string builder."""

    def test_empty_profile_returns_default(self) -> None:
        """Empty profile returns the 'no profile' default message."""
        result = _build_patient_context({})
        assert "No patient profile" in result

    def test_full_profile_contains_all_fields(self) -> None:
        """Full profile produces a string with all key fields."""
        profile = {
            "name": "Ravi Kumar",
            "age": 35,
            "conditions": ["Diabetes"],
            "allergies": ["Penicillin"],
            "medications": ["Metformin"],
        }
        result = _build_patient_context(profile)
        assert "Ravi Kumar" in result
        assert "35" in result
        assert "Diabetes" in result
        assert "Penicillin" in result
        assert "Metformin" in result

    def test_allergy_has_warning_emoji(self) -> None:
        """Allergies line includes a warning indicator."""
        profile = {"allergies": ["Penicillin"]}
        result = _build_patient_context(profile)
        assert "⚠️" in result or "ALLERG" in result
