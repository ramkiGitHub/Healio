"""
app/graph/nodes.py
==================
All LangGraph node implementations for the Healio pipeline.

Why this file exists
--------------------
Each function in this file is a **LangGraph node** — a discrete, testable
unit that takes a ``HealioState``, performs one job, and returns a partial
state dict containing only the fields it updated.

Nodes never call each other directly. LangGraph's conditional edges
(defined in ``edges.py``) control the execution order.

Node overview
-------------
1. ``router_node``        — Classifies the patient's intent and severity
                            using GPT-4o-mini. Entry point on every turn.
2. ``emergency_node``     — Handles emergencies: sends HITL doctor alert,
                            generates patient-facing safety response.
3. ``profile_lookup_node``— Fetches the patient profile from the Mock EHR tool
                            and injects it into state.
4. ``schedule_node``      — Manages multi-turn appointment booking dialogue.
5. ``general_qa_node``    — Answers general medical queries using GPT-4o-mini
                            with full patient context.

Adding a new node
-----------------
1. Define a new function with signature:
   ``def my_node(state: HealioState) -> dict: ...``
2. Register it in ``app/graph/graph.py`` with:
   ``graph.add_node("my_node", my_node)``
3. Add routing edges in ``app/graph/edges.py``.
"""

import json
from datetime import UTC, datetime

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.constants import (
    EMERGENCY_KEYWORDS,
    ROUTER_PROMPT_TEMPLATE,
    SYSTEM_PROMPT_TEMPLATE,
    URGENT_KEYWORDS,
    IntentType,
    SeverityLevel,
)
from app.exceptions import NodeExecutionError, PatientNotFoundError, RoutingError
from app.graph.state import HealioState
from app.logging_config import get_logger
from app.nlp.biobert import get_biobert_extractor
from app.nlp.severity import SeverityScorer
from app.tools.alerts import HITLAlertTool
from app.tools.calendar import get_calendar_tool
from app.tools.ehr import get_ehr_tool

log = get_logger(__name__)

# ── Shared LLM instance ────────────────────────────────────────────────────────
# A single ChatOpenAI client shared by all nodes.
# LangChain handles connection pooling internally.
_llm = ChatOpenAI(
    model=settings.openai_model,
    max_tokens=settings.openai_max_tokens,
    temperature=settings.openai_temperature,
    api_key=settings.openai_api_key,
)


# ── 1. Router Node ─────────────────────────────────────────────────────────────

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    reraise=True,
)
def router_node(state: HealioState) -> dict:
    """Classify the patient's latest message into an intent and severity level.

    This is the first node executed on every conversation turn. It reads the
    latest patient message and uses two mechanisms to classify it:
    1. **Rule-based keyword matching**: Fast, no LLM call required. Used to
       catch obvious emergency keywords immediately.
    2. **GPT-4o-mini classification**: For nuanced intent classification where
       keywords are insufficient.

    The rule-based check always takes precedence — if emergency keywords are
    detected, we never wait for the LLM.

    Args:
        state: Current HealioState. Reads ``messages`` (latest HumanMessage).

    Returns:
        Partial state dict updating ``intent`` and ``severity``.

    Raises:
        NodeExecutionError: If the LLM call fails after all retries.
        RoutingError: If the LLM returns an unparseable response.

    Example state update:
        {"intent": IntentType.QUERY, "severity": SeverityLevel.ROUTINE}
    """
    # Extract the latest message from the patient
    latest_message = _get_latest_human_message(state)

    if not latest_message:
        log.warning("router_node_no_human_message", session_id=state.get("session_id"))
        return {"intent": IntentType.UNKNOWN, "severity": SeverityLevel.ROUTINE}

    log.info(
        "router_node_started",
        session_id=state.get("session_id"),
        message_preview=latest_message[:80],
    )

    # ── Step 1: Rule-based emergency keyword check ─────────────────────────────
    # Check before calling the LLM — zero latency, catches obvious cases.
    keyword_severity = _classify_severity_by_keywords(latest_message)

    if keyword_severity == SeverityLevel.EMERGENCY:
        log.info(
            "router_node_emergency_detected_by_keywords",
            session_id=state.get("session_id"),
        )
        return {
            "intent": IntentType.EMERGENCY,
            "severity": SeverityLevel.EMERGENCY,
            "biobert_entities": [],
        }

    # ── Step 2: BioBERT medical NER (Phase 2) ─────────────────────────────────
    # Extracts clinical entities (Sign_symptom, Disease_disorder, etc.) and
    # enhances severity detection beyond simple keyword matching.
    extractor = get_biobert_extractor()
    entities = extractor.extract(latest_message)
    biobert_severity = SeverityScorer().score_from_entities(entities)
    entities_as_dicts = [e.to_dict() for e in entities]

    if biobert_severity == SeverityLevel.EMERGENCY:
        log.info(
            "router_node_emergency_detected_by_biobert",
            session_id=state.get("session_id"),
            entities=[e.text for e in entities],
        )
        return {
            "intent": IntentType.EMERGENCY,
            "severity": SeverityLevel.EMERGENCY,
            "biobert_entities": entities_as_dicts,
        }

    # ── Step 3: LLM-based intent + severity classification ────────────────────
    prompt = ROUTER_PROMPT_TEMPLATE.format(message=latest_message)

    try:
        response = _llm.invoke([HumanMessage(content=prompt)])
        raw_content = response.content if hasattr(response, "content") else str(response)
    except Exception as exc:
        raise NodeExecutionError(
            detail=f"LLM call failed in router_node: {exc}",
            node_name="router_node",
        ) from exc

    # Parse the JSON response from the LLM
    intent, llm_severity = _parse_router_response(raw_content, state.get("session_id", ""))

    # Use the strictest of: keyword / BioBERT / LLM severity
    final_severity = _max_severity(_max_severity(keyword_severity, biobert_severity), llm_severity)

    log.info(
        "router_node_complete",
        session_id=state.get("session_id"),
        intent=intent,
        severity=final_severity,
        biobert_entities_count=len(entities_as_dicts),
    )

    return {
        "intent": intent,
        "severity": final_severity,
        "biobert_entities": entities_as_dicts,
    }


# ── 2. Emergency Node ──────────────────────────────────────────────────────────

def emergency_node(state: HealioState) -> dict:
    """Handle emergency situations: alert the doctor and respond to the patient.

    This node is triggered when the Router Node classifies severity as EMERGENCY.
    It performs two actions:
    1. Sends a Human-in-the-Loop (HITL) alert to the on-call doctor via
       Telegram (tool call to ``app/tools/alerts.py``).
    2. Generates and returns an empathetic, safety-first patient response.

    The HITL alert tool is a PHASE 2 addition — in Phase 1 this node logs
    the emergency and returns the patient response directly.

    Args:
        state: Current HealioState. Reads ``patient_id``, ``session_id``,
               ``messages``, and ``patient_profile``.

    Returns:
        Partial state dict updating ``messages`` (AI response appended)
        and ``flags`` (``human_loop_triggered`` set to True).

    Example state update:
        {
            "messages": [..., AIMessage(content="Emergency response text")],
            "flags": {"human_loop_triggered": True, ...}
        }
    """
    session_id = state.get("session_id", "")
    patient_id = state.get("patient_id", "")
    latest_message = _get_latest_human_message(state)
    patient_name = state.get("patient_profile", {}).get("name", "")

    log.warning(
        "emergency_node_triggered",
        session_id=session_id,
        patient_id=patient_id,
        message_preview=(latest_message or "")[:80],
    )

    # ── HITL Alert: notify the on-call doctor via Telegram ───────────────────
    # Send a structured alert to DOCTOR_CHAT_ID. If the alert fails (network
    # issue, bad token etc.), we log a critical error but still send the
    # patient-facing response — never leave a patient without a reply.
    try:
        HITLAlertTool().send_alert(
            patient_id=patient_id,
            patient_name=patient_name or "",
            message=latest_message or "",
            session_id=session_id,
            severity="emergency",
        )
    except Exception as alert_exc:
        # Critical: alert failed, but we continue to serve the patient
        log.error(
            "emergency_alert_failed",
            session_id=session_id,
            patient_id=patient_id,
            error=str(alert_exc),
        )

    # Compose the patient-facing emergency response
    greeting = f"{patient_name}, I" if patient_name else "I"
    emergency_response = (
        f"🚨 *Emergency Alert Sent*\n\n"
        f"{greeting}'ve immediately alerted the on-call medical team about your situation.\n\n"
        f"*Please do the following right now:*\n"
        f"• Call *112* (India Emergency Services) if you need immediate help\n"
        f"• Stay calm and do not move if you are in pain\n"
        f"• If you are alone, unlock your door if possible\n\n"
        f"A doctor will contact you shortly. Do not ignore this — your safety comes first."
    )

    # Update flags to record that the human loop was triggered
    updated_flags = {**state.get("flags", {}), "human_loop_triggered": True}

    return {
        "messages": [AIMessage(content=emergency_response)],
        "flags": updated_flags,
    }


# ── 3. Profile Lookup Node ─────────────────────────────────────────────────────

def profile_lookup_node(state: HealioState) -> dict:
    """Fetch the patient's profile from the EHR store and inject it into state.

    This node is triggered when:
    - The patient explicitly asks about their profile/history (PROFILE intent).
    - A downstream node needs patient context but the profile is not yet loaded.

    In Phase 1, this node reads from ``data/mock_patients.json``.
    The Mock EHR Tool will be connected in Phase 2 (app/tools/ehr.py).

    Args:
        state: Current HealioState. Reads ``patient_id``.

    Returns:
        Partial state dict updating ``patient_profile`` and ``flags``
        (``profile_loaded`` set to True even if no record found — to
        prevent infinite loops on unknown patients).

    Example state update:
        {
            "patient_profile": {"name": "Ravi Kumar", "age": 35, ...},
            "flags": {"profile_loaded": True, ...}
        }
    """
    session_id = state.get("session_id", "")
    patient_id = state.get("patient_id", "")

    log.info(
        "profile_lookup_node_started",
        session_id=session_id,
        patient_id=patient_id,
    )

    # ── EHR tool: fetch patient profile from Mock EHR store ──────────────────
    # Raises PatientNotFoundError for unknown IDs — caught here to keep
    # the graph running (unknown patients get an empty profile).
    try:
        profile = get_ehr_tool().lookup_patient(patient_id=patient_id)
    except PatientNotFoundError:
        log.info(
            "profile_lookup_no_record",
            session_id=session_id,
            patient_id=patient_id,
        )
        profile = {}

    updated_flags = {**state.get("flags", {}), "profile_loaded": True}

    log.info(
        "profile_lookup_node_complete",
        session_id=session_id,
        patient_id=patient_id,
        profile_found=bool(profile),
    )

    return {
        "patient_profile": profile,
        "flags": updated_flags,
    }


# ── 4. Schedule Node ───────────────────────────────────────────────────────────

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    reraise=True,
)
def schedule_node(state: HealioState) -> dict:
    """Manage a multi-turn appointment booking conversation.

    This node guides the patient through the appointment booking process,
    collecting: preferred date → preferred time → reason → confirmation.

    In Phase 1, this node uses GPT-4o-mini to drive the dialogue.
    In Phase 3, it will call the Calendar Tool to check real slot availability.

    Args:
        state: Current HealioState. Reads ``messages``, ``patient_profile``,
               and ``appointment_context`` (persists across turns).

    Returns:
        Partial state dict updating ``messages`` (AI reply appended) and
        ``appointment_context`` (booking progress updated).

    Raises:
        NodeExecutionError: If the LLM call fails after all retries.
    """
    session_id = state.get("session_id", "")
    patient_profile = state.get("patient_profile", {})
    patient_name = patient_profile.get("name", "")

    log.info("schedule_node_started", session_id=session_id)

    # Fetch today's and tomorrow's available slots from the calendar tool
    # so the LLM can quote real slot times instead of making them up.
    calendar_context = _build_calendar_context()

    system_content = (
        "You are a helpful clinic appointment scheduling assistant for Healio. "
        "Help the patient book an appointment by collecting: "
        "1) preferred date, 2) preferred time, 3) reason for visit. "
        "Then confirm the details. "
        "Be concise — the patient is on a mobile messaging app. "
        f"The patient's name is: {patient_name or 'Unknown'}. "
        f"Today's date is: {datetime.now(UTC).strftime('%A, %B %d, %Y')}.\n\n"
        f"{calendar_context}\n\n"
        "Once you have all three pieces of information (date, time, and reason), "
        "summarise them and ask the patient to confirm. "
        "When the patient confirms, end your reply with exactly: "
        "'✅ Appointment request received.' "
        "Do NOT book the appointment yourself — the clinic staff will call to confirm."
    )

    messages_for_llm = [
        SystemMessage(content=system_content),
        *state.get("messages", []),
    ]

    try:
        response = _llm.invoke(messages_for_llm)
        reply_text = response.content if hasattr(response, "content") else str(response)
    except Exception as exc:
        raise NodeExecutionError(
            detail=f"LLM call failed in schedule_node: {exc}",
            node_name="schedule_node",
        ) from exc

    log.info("schedule_node_complete", session_id=session_id)

    # Detect confirmation to mark appointment as received in state
    appointment_ctx = dict(state.get("appointment_context") or {})
    if "✅ Appointment request received" in reply_text or "Appointment request received" in reply_text:
        appointment_ctx["booking_step"] = "received"

    return {
        "messages": [AIMessage(content=reply_text)],
        "appointment_context": appointment_ctx,
    }


# ── 5. General Q&A Node ────────────────────────────────────────────────────────

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    reraise=True,
)
def general_qa_node(state: HealioState) -> dict:
    """Answer general medical queries using GPT-4o-mini with patient context.

    This is the default node for all non-emergency, non-appointment queries.
    It uses the full patient profile and conversation history to produce
    contextually relevant, empathetic responses.

    Runs an allergy conflict check: if the latest patient message mentions
    a medication the patient is allergic to, a prominent warning is prepended
    to the LLM system prompt and the ``allergy_flagged`` state flag is set.

    Args:
        state: Current HealioState. Reads ``messages``, ``patient_profile``,
               ``intent``, and ``flags``.

    Returns:
        Partial state dict updating ``messages`` (AI reply appended) and
        ``flags`` (``allergy_flagged`` set if a conflict was detected).

    Raises:
        NodeExecutionError: If the LLM call fails after all retries.
    """
    session_id = state.get("session_id", "")
    patient_profile = state.get("patient_profile", {})

    log.info(
        "general_qa_node_started",
        session_id=session_id,
        intent=state.get("intent"),
    )

    # ── Allergy conflict check (Phase 3) ─────────────────────────────────────
    # Detects if the patient's message mentions a drug they are allergic to.
    # Prepends a hard warning to the system prompt so the LLM cannot miss it.
    latest_message = _get_latest_human_message(state) or ""
    allergies: list[str] = patient_profile.get("allergies", [])
    conflicting_allergens = _check_allergy_conflict(latest_message, allergies)
    allergy_flagged = bool(conflicting_allergens)

    if allergy_flagged:
        log.warning(
            "allergy_conflict_detected",
            session_id=session_id,
            patient_id=state.get("patient_id"),
            conflicting=conflicting_allergens,
        )

    allergy_warning = ""
    if allergy_flagged:
        allergen_list = ", ".join(conflicting_allergens)
        allergy_warning = (
            f"\n\n⚠️ ALLERGY ALERT ⚠️\n"
            f"This patient is allergic to: {allergen_list}.\n"
            f"Their message mentions one of these. You MUST prominently warn "
            f"the patient about this allergy conflict at the start of your response."
        )

    # Build patient context string to inject into the system prompt
    patient_context = _build_patient_context(patient_profile)

    system_content = SYSTEM_PROMPT_TEMPLATE.format(
        patient_context=patient_context,
        current_date=datetime.now(UTC).strftime("%A, %B %d, %Y"),
    ) + allergy_warning

    # Build message list: system prompt + full conversation history
    messages_for_llm = [
        SystemMessage(content=system_content),
        *state.get("messages", []),
    ]

    try:
        response = _llm.invoke(messages_for_llm)
        reply_text = response.content if hasattr(response, "content") else str(response)
    except Exception as exc:
        raise NodeExecutionError(
            detail=f"LLM call failed in general_qa_node: {exc}",
            node_name="general_qa_node",
        ) from exc

    log.info(
        "general_qa_node_complete",
        session_id=session_id,
        allergy_flagged=allergy_flagged,
    )

    # Merge allergy_flagged into the existing flags dict
    updated_flags = {**state.get("flags", {}), "allergy_flagged": allergy_flagged}

    return {
        "messages": [AIMessage(content=reply_text)],
        "flags": updated_flags,
    }


# ── Private helper functions ───────────────────────────────────────────────────

def _get_latest_human_message(state: HealioState) -> str | None:
    """Extract the text content of the most recent HumanMessage from state.

    Args:
        state: Current HealioState.

    Returns:
        The text content of the latest HumanMessage, or None if not found.
    """
    messages = state.get("messages", [])
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return None


def _classify_severity_by_keywords(text: str) -> SeverityLevel:
    """Rule-based severity classification using keyword lists.

    Checks the message text against pre-defined emergency and urgent keyword
    lists (defined in ``app/constants.py``). This is fast (no LLM call) and
    errs on the side of escalation — it is safer to over-flag than to miss
    a real emergency.

    Args:
        text: The patient's message text (lowercased internally).

    Returns:
        ``SeverityLevel.EMERGENCY``, ``SeverityLevel.URGENT``, or
        ``SeverityLevel.ROUTINE``.
    """
    lower_text = text.lower()

    for keyword in EMERGENCY_KEYWORDS:
        if keyword in lower_text:
            return SeverityLevel.EMERGENCY

    for keyword in URGENT_KEYWORDS:
        if keyword in lower_text:
            return SeverityLevel.URGENT

    return SeverityLevel.ROUTINE


def _parse_router_response(
    raw_content: str,
    session_id: str,
) -> tuple[IntentType, SeverityLevel]:
    """Parse the JSON response from the Router Node LLM call.

    The LLM is prompted to return JSON with ``intent`` and ``severity`` keys.
    This function parses that JSON and maps the values to typed enums,
    falling back to safe defaults if parsing fails.

    Args:
        raw_content: Raw string content from the LLM response.
        session_id: Session ID for logging context.

    Returns:
        A tuple of ``(IntentType, SeverityLevel)``.

    Raises:
        RoutingError: If the LLM response cannot be parsed at all.
    """
    # Strip markdown code blocks if the LLM wrapped the JSON
    clean = raw_content.strip()
    if clean.startswith("```"):
        clean = clean.split("```")[1]
        if clean.startswith("json"):
            clean = clean[4:]
        clean = clean.strip()

    try:
        data = json.loads(clean)
    except json.JSONDecodeError:
        log.warning(
            "router_json_parse_failed",
            session_id=session_id,
            raw_content=raw_content[:200],
        )
        return IntentType.UNKNOWN, SeverityLevel.ROUTINE

    raw_intent = str(data.get("intent", "unknown")).lower()
    raw_severity = str(data.get("severity", "routine")).lower()

    # Map to enums, falling back to safe defaults on unknown values
    try:
        intent = IntentType(raw_intent)
    except ValueError:
        log.warning(
            "router_unknown_intent",
            session_id=session_id,
            raw_intent=raw_intent,
        )
        intent = IntentType.UNKNOWN

    try:
        severity = SeverityLevel(raw_severity)
    except ValueError:
        log.warning(
            "router_unknown_severity",
            session_id=session_id,
            raw_severity=raw_severity,
        )
        severity = SeverityLevel.ROUTINE

    return intent, severity


def _max_severity(a: SeverityLevel, b: SeverityLevel) -> SeverityLevel:
    """Return the higher of two severity levels.

    Priority order: EMERGENCY > URGENT > ROUTINE.

    Args:
        a: First severity level.
        b: Second severity level.

    Returns:
        The more severe of the two levels.

    Example:
        >>> _max_severity(SeverityLevel.ROUTINE, SeverityLevel.URGENT)
        <SeverityLevel.URGENT: 'urgent'>
    """
    priority = {
        SeverityLevel.ROUTINE: 0,
        SeverityLevel.URGENT: 1,
        SeverityLevel.EMERGENCY: 2,
    }
    return a if priority[a] >= priority[b] else b


def _build_patient_context(profile: dict) -> str:
    """Format a patient profile dict into a readable context string for the LLM.

    This context is injected into the system prompt so the LLM can
    reference the patient's conditions, allergies, and medications.

    Args:
        profile: The patient profile dict from HealioState.

    Returns:
        A human-readable string summarising the patient's medical context,
        or a default message if no profile is available.

    Example:
        >>> _build_patient_context({"name": "Ravi", "age": 35, "conditions": ["diabetes"]})
        'Patient: Ravi, Age: 35\\nConditions: diabetes\\n...'
    """
    if not profile:
        return "No patient profile on record. Treat as a new patient."

    lines = []
    if name := profile.get("name"):
        lines.append(f"Patient: {name}")
    if age := profile.get("age"):
        lines.append(f"Age: {age}")
    if conditions := profile.get("conditions"):
        lines.append(f"Conditions: {', '.join(conditions)}")
    if allergies := profile.get("allergies"):
        lines.append(f"⚠️ ALLERGIES: {', '.join(allergies)}")
    if medications := profile.get("medications"):
        lines.append(f"Medications: {', '.join(medications)}")
    if last_visit := profile.get("last_visit"):
        lines.append(f"Last visit: {last_visit}")

    return "\n".join(lines) if lines else "No patient profile on record."


def _mock_profile_lookup(patient_id: str) -> dict:
    """Temporary mock EHR lookup until Phase 2 EHR tool is implemented.

    Returns a hardcoded profile for a test patient ID, empty dict otherwise.
    This will be replaced by the real MockEHRTool in Phase 2.

    Args:
        patient_id: The patient identifier to look up.

    Returns:
        A patient profile dict, or an empty dict if not found.
    """
    mock_db = {
        "test_patient": {
            "patient_id": "test_patient",
            "name": "Ravi Kumar",
            "age": 35,
            "conditions": ["Type 2 Diabetes", "Hypertension"],
            "allergies": ["Penicillin"],
            "medications": ["Metformin 500mg", "Amlodipine 5mg"],
            "last_visit": "2026-03-15",
            "blood_group": "O+",
        }
    }
    return mock_db.get(patient_id, {})


def _check_allergy_conflict(message: str, allergies: list[str]) -> list[str]:
    """Return any allergens from *allergies* that are mentioned in *message*.

    The check is case-insensitive and looks for the allergen name as a
    substring, which is intentionally broad so that partial drug names (e.g.
    "penicillin" inside "amoxicillin-/penicillin group") are not missed.

    Args:
        message:   The raw patient message text.
        allergies: List of allergen strings from the patient profile.

    Returns:
        A (possibly empty) list of allergen strings that appear in the message.

    Example:
        >>> _check_allergy_conflict("can i take penicillin?", ["Penicillin", "NSAIDs"])
        ['Penicillin']
    """
    if not message or not allergies:
        return []
    lower_msg = message.lower()
    return [allergen for allergen in allergies if allergen.lower() in lower_msg]


def _build_calendar_context() -> str:
    """Return a short natural-language summary of available slots for today
    and tomorrow, suitable for injection into the ``schedule_node`` system
    prompt.

    Uses :func:`app.tools.calendar.get_calendar_tool` so the LLM sees
    *real* (mock) availability rather than a hardcoded range.  Silently
    degrades to a generic availability line if the calendar tool raises.

    Returns:
        Multi-line string describing available appointment slots, or a
        fallback availability note on error.
    """
    from datetime import timedelta  # local import to avoid circular risk

    try:
        tool = get_calendar_tool()
        today = datetime.now(UTC).date()
        lines: list[str] = []

        for offset in range(3):  # today + next 2 days
            target = today + timedelta(days=offset)
            date_str = target.isoformat()  # "YYYY-MM-DD"
            slots = tool.get_available_slots(date_str)
            if slots:
                times = ", ".join(s.time_str for s in slots[:6])
                suffix = " (and more)" if len(slots) > 6 else ""
                day_label = target.strftime("%A %d %b")
                lines.append(f"- {day_label}: {times}{suffix}")

        if lines:
            return "Real-time available appointment slots:\n" + "\n".join(lines)
        return "No slots currently available in the next 3 days. Ask the patient for a later date."

    except Exception:
        log.warning("calendar_context_build_failed")
        return "Available slots: Monday–Saturday, 9 am–5:30 pm (30-minute appointments)."
