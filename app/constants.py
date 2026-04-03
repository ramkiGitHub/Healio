"""
app/constants.py
================
Central registry for all application-wide constants, enums, and static values.

Why this file exists
--------------------
Keeping constants in one place prevents magic strings and numbers from
scattering across the codebase. When a value needs to change (e.g., a new
intent type is added), this is the only file that needs updating.

How to extend
-------------
- Add new IntentType members as the graph gains new capabilities.
- Add new SeverityLevel members if the triage model becomes more granular.
- Add new emergency keywords to EMERGENCY_KEYWORDS as clinical feedback arrives.
"""

from enum import StrEnum


# ── Channel identifiers ────────────────────────────────────────────────────────

class ChannelType(StrEnum):
    """Supported messaging channels.

    Each channel has a corresponding webhook handler in app/channels/.
    Add a new member here when a new channel integration is added.
    """

    TELEGRAM = "telegram"
    WHATSAPP = "whatsapp"   # Placeholder — not active in MVP


# ── Patient intent types ───────────────────────────────────────────────────────

class IntentType(StrEnum):
    """Patient message intent categories produced by the Router Node.

    The LangGraph conditional edges use these values to route execution
    to the correct downstream node.

    Attributes:
        EMERGENCY: Patient describes life-threatening symptoms.
        APPOINTMENT: Patient wants to book, reschedule, or cancel an appointment.
        PROFILE: Patient is asking about their own records or history.
        QUERY: General medical question or information request.
        CHITCHAT: Non-medical small talk or greeting.
        UNKNOWN: Router could not confidently assign an intent.
    """

    EMERGENCY = "emergency"
    APPOINTMENT = "appointment"
    PROFILE = "profile"
    QUERY = "query"
    CHITCHAT = "chitchat"
    UNKNOWN = "unknown"


# ── Severity levels ────────────────────────────────────────────────────────────

class SeverityLevel(StrEnum):
    """Clinical severity of a patient's reported symptoms.

    Produced by the SeverityScorer in app/nlp/severity.py and used
    by the Router Node to trigger emergency routing.

    Attributes:
        EMERGENCY: Requires immediate medical attention or emergency services.
        URGENT: Should be seen by a clinician within hours.
        ROUTINE: Non-urgent; standard appointment appropriate.
    """

    EMERGENCY = "emergency"
    URGENT = "urgent"
    ROUTINE = "routine"


# ── Calendar provider types ────────────────────────────────────────────────────

class CalendarProvider(StrEnum):
    """Appointment scheduling back-end providers.

    Controlled by the CALENDAR_PROVIDER environment variable.
    See app/tools/calendar.py for provider implementations.

    Attributes:
        MOCK: Returns hardcoded availability slots. No credentials needed.
        GOOGLE: Integrates with Google Calendar API. Requires OAuth2 setup.
    """

    MOCK = "mock"
    GOOGLE = "google"   # Placeholder — not active in MVP


# ── WhatsApp provider types ────────────────────────────────────────────────────

class WhatsAppProvider(StrEnum):
    """WhatsApp messaging back-end providers.

    Controlled by the WHATSAPP_PROVIDER environment variable.
    See app/channels/whatsapp.py for activation instructions.

    Attributes:
        TWILIO: Uses Twilio WhatsApp Business API.
        META: Uses Meta (Facebook) Cloud API directly.
    """

    TWILIO = "twilio"
    META = "meta"


# ── Emergency detection keywords ──────────────────────────────────────────────

# Rule-based keyword lists used by SeverityScorer (app/nlp/severity.py).
# BioBERT NER will supplement these in Phase 2, but these lists provide
# a fast, reliable first pass that requires no model inference.
#
# HOW TO EXTEND:
# Add clinical keywords to the appropriate list. All matching is
# case-insensitive and checks for whole-word substrings.

EMERGENCY_KEYWORDS: frozenset[str] = frozenset(
    {
        # Cardiac
        "chest pain", "chest tightness", "heart attack", "cardiac arrest",
        "palpitations", "heart racing",
        # Neurological
        "stroke", "seizure", "convulsion", "unconscious", "unresponsive",
        "paralysis", "sudden numbness", "sudden confusion",
        # Respiratory
        "can't breathe", "cannot breathe", "difficulty breathing",
        "shortness of breath", "choking", "not breathing",
        # Trauma / bleeding
        "severe bleeding", "heavy bleeding", "deep cut", "head injury",
        "broken bone", "fracture",
        # Allergic reaction
        "anaphylaxis", "severe allergic", "throat swelling", "tongue swelling",
        # Mental health crisis
        "suicidal", "want to die", "self harm", "overdose",
        # General emergency
        "emergency", "ambulance", "911", "112", "help me",
    }
)

URGENT_KEYWORDS: frozenset[str] = frozenset(
    {
        "high fever", "fever above 103", "persistent vomiting", "severe pain",
        "severe headache", "blurred vision", "sudden vision loss",
        "blood in urine", "blood in stool", "fainting", "dizziness",
        "allergic reaction", "rash spreading",
    }
)


# ── Prompt templates ───────────────────────────────────────────────────────────

# System prompt injected into all GPT-4o-mini calls.
# {patient_context} is replaced at runtime with the patient's profile summary.
SYSTEM_PROMPT_TEMPLATE: str = """You are Healio, a compassionate and knowledgeable medical assistant \
for a clinic in India. Your role is to help patients with health queries, \
appointment scheduling, and basic symptom guidance.

IMPORTANT RULES:
1. You are NOT a doctor and must never provide a diagnosis.
2. Always recommend consulting a qualified doctor for any health concern.
3. For emergencies, always advise calling 112 (India emergency) immediately.
4. Be empathetic, clear, and use simple language (avoid medical jargon).
5. Keep responses concise — patients are on a mobile messaging app.

Patient context:
{patient_context}

Today's date: {current_date}"""

# Prompt used by the Router Node to classify patient intent.
ROUTER_PROMPT_TEMPLATE: str = """Classify the patient's message into exactly one of these intents:
- emergency: life-threatening symptoms
- appointment: booking, rescheduling, or cancelling an appointment
- profile: asking about their medical history, records, or prescriptions
- query: general medical question
- chitchat: greeting or non-medical conversation
- unknown: cannot determine intent

Also classify severity as: emergency | urgent | routine

Respond in JSON only, with keys "intent" and "severity".

Patient message: {message}"""


# ── HTTP / API constants ───────────────────────────────────────────────────────

# Timeout (seconds) for outbound HTTP requests to external APIs.
HTTP_TIMEOUT_SECONDS: int = 10

# Maximum number of retry attempts for transient API failures.
MAX_RETRY_ATTEMPTS: int = 3

# Base wait time (seconds) for exponential backoff between retries.
RETRY_WAIT_SECONDS: float = 1.0


# ── Conversation memory ────────────────────────────────────────────────────────

# Maximum number of messages to retain in the conversation window.
# Older messages are trimmed to stay within LLM context limits.
MAX_CONVERSATION_MESSAGES: int = 20
