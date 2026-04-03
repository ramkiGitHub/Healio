"""
app/graph/edges.py
==================
LangGraph conditional edge routing functions.

Why this file exists
--------------------
In LangGraph, **conditional edges** decide which node runs next based on the
current state. Instead of embedding routing logic inside nodes (which mixes
concerns), all routing decisions live here.

Each function in this file:
1. Takes the current ``HealioState``.
2. Reads a specific field (``intent``, ``severity``, ``flags``).
3. Returns the **string name** of the next node to execute.

LangGraph uses these return values to find the next node in the graph.
The string names must exactly match the node names used in ``graph.py``.

Node names (must match graph.py exactly)
-----------------------------------------
- ``"router"``          — RouterNode
- ``"emergency"``       — EmergencyNode
- ``"profile_lookup"``  — ProfileLookupNode
- ``"schedule"``        — ScheduleNode
- ``"general_qa"``      — GeneralQANode
- ``END``               — LangGraph built-in terminal node

How to extend
-------------
1. Add a new node name string constant to ``NODE_*`` constants below.
2. Add a new routing function (or extend an existing one) that returns
   the new node name.
3. Register the new edge in ``app/graph/graph.py``.
"""

from langgraph.graph import END

from app.constants import IntentType, SeverityLevel
from app.graph.state import HealioState
from app.logging_config import get_logger

log = get_logger(__name__)

# ── Node name constants ────────────────────────────────────────────────────────
# Define these as constants so typos cause import errors rather than
# silent routing failures at runtime.

NODE_ROUTER = "router"
NODE_EMERGENCY = "emergency"
NODE_PROFILE_LOOKUP = "profile_lookup"
NODE_SCHEDULE = "schedule"
NODE_GENERAL_QA = "general_qa"


# ── Primary routing function ───────────────────────────────────────────────────

def route_after_router(state: HealioState) -> str:
    """Determine the next node after the Router Node has classified the message.

    This is the main routing function. It is registered as a conditional edge
    on the ``router`` node in ``app/graph/graph.py``.

    Routing logic (evaluated in priority order):
    1. If severity is EMERGENCY → go to emergency node immediately, regardless
       of intent. Safety first.
    2. If intent is APPOINTMENT → go to schedule node.
    3. If intent is PROFILE, or patient profile has not been loaded yet →
       go to profile lookup node first (profile lookup routes onward from there).
    4. All other intents (QUERY, CHITCHAT, UNKNOWN) → go to general Q&A node.

    Args:
        state: The current HealioState after the Router Node has run.

    Returns:
        The string name of the next node to execute.

    Example:
        >>> state = {"severity": SeverityLevel.EMERGENCY, "intent": IntentType.QUERY, ...}
        >>> route_after_router(state)
        'emergency'
    """
    severity: SeverityLevel = state.get("severity", SeverityLevel.ROUTINE)
    intent: IntentType = state.get("intent", IntentType.UNKNOWN)
    profile_loaded: bool = state.get("flags", {}).get("profile_loaded", False)

    # Priority 1: Emergency overrides everything
    if severity == SeverityLevel.EMERGENCY:
        log.info(
            "routing_to_emergency",
            session_id=state.get("session_id"),
            severity=severity,
            intent=intent,
        )
        return NODE_EMERGENCY

    # Priority 2: Appointment booking flow
    if intent == IntentType.APPOINTMENT:
        log.info(
            "routing_to_schedule",
            session_id=state.get("session_id"),
            intent=intent,
        )
        return NODE_SCHEDULE

    # Priority 3: Profile request OR profile not yet loaded
    # Loading the profile first ensures all downstream nodes have patient
    # context regardless of what the patient asked.
    if intent == IntentType.PROFILE or not profile_loaded:
        log.info(
            "routing_to_profile_lookup",
            session_id=state.get("session_id"),
            intent=intent,
            profile_loaded=profile_loaded,
        )
        return NODE_PROFILE_LOOKUP

    # Default: General Q&A (covers QUERY, CHITCHAT, UNKNOWN)
    log.info(
        "routing_to_general_qa",
        session_id=state.get("session_id"),
        intent=intent,
    )
    return NODE_GENERAL_QA


def route_after_profile_lookup(state: HealioState) -> str:
    """Determine the next node after the Profile Lookup Node.

    After the patient profile is loaded, we re-route based on the original
    intent. This avoids the patient always being sent to Q&A just because
    their profile needed loading.

    Args:
        state: The current HealioState after ProfileLookupNode has run.

    Returns:
        The string name of the next node to execute.

    Example:
        >>> state = {"intent": IntentType.QUERY, "flags": {"profile_loaded": True}, ...}
        >>> route_after_profile_lookup(state)
        'general_qa'
    """
    intent: IntentType = state.get("intent", IntentType.UNKNOWN)

    # If the patient was explicitly asking about their profile, go to Q&A to
    # compose a summary response using the now-loaded profile data.
    if intent == IntentType.PROFILE:
        log.info(
            "profile_lookup_routing_to_qa",
            session_id=state.get("session_id"),
        )
        return NODE_GENERAL_QA

    # For appointment intents intercepted before profile was loaded
    if intent == IntentType.APPOINTMENT:
        log.info(
            "profile_lookup_routing_to_schedule",
            session_id=state.get("session_id"),
        )
        return NODE_SCHEDULE

    # All other intents → Q&A (most common path)
    return NODE_GENERAL_QA


def route_after_emergency(state: HealioState) -> str:
    """Determine the next node after the Emergency Node.

    In MVP, the emergency node always terminates the graph after sending
    the alert and patient-facing emergency response. The graph ends here.

    In future this could route to a "follow up" node after the doctor
    acknowledges the alert.

    Args:
        state: The current HealioState after EmergencyNode has run.

    Returns:
        ``END`` — terminates the graph execution.
    """
    log.info(
        "emergency_node_complete_ending_graph",
        session_id=state.get("session_id"),
        human_loop_triggered=state.get("flags", {}).get("human_loop_triggered"),
    )
    return END
