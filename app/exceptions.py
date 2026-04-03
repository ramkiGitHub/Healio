"""
app/exceptions.py
=================
Custom exception hierarchy for Healio.

Why this file exists
--------------------
Using a typed exception hierarchy instead of bare ``Exception`` or generic
``ValueError`` makes error handling predictable:
- Callers can catch specific exception types.
- Logs carry the exact context of what went wrong.
- The FastAPI global exception handler maps each type to the correct HTTP
  status code (see app/main.py).

Hierarchy
---------
HealioBaseError
├── ChannelError          — Problems receiving or sending messages
│   ├── TelegramError
│   └── WhatsAppError
├── GraphError            — LangGraph execution failures
│   ├── RoutingError
│   └── NodeExecutionError
├── ToolError             — LangChain tool failures
│   ├── EHRLookupError
│   │   └── PatientNotFoundError
│   ├── CalendarToolError
│   └── AlertToolError
└── NLPError              — BioBERT / NLP pipeline failures
    └── ModelNotLoadedError

How to extend
-------------
Add a new exception class under the appropriate parent. Always set a
meaningful ``detail`` message so the structured log is self-explanatory.
"""


# ── Base ───────────────────────────────────────────────────────────────────────

class HealioBaseError(Exception):
    """Root exception for all Healio application errors.

    All custom exceptions inherit from this class so callers can catch
    the entire Healio error family with a single ``except HealioBaseError``.

    Args:
        detail: Human-readable description of what went wrong.

    Example:
        >>> raise HealioBaseError("Something went wrong")
    """

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(detail={self.detail!r})"


# ── Channel errors ─────────────────────────────────────────────────────────────

class ChannelError(HealioBaseError):
    """Raised when a messaging channel fails to receive or send a message.

    Covers authentication failures, payload parse errors, and send failures.
    """


class TelegramError(ChannelError):
    """Raised for Telegram-specific failures.

    Args:
        detail: Description of the failure.
        chat_id: The Telegram chat ID involved, if available.

    Example:
        >>> raise TelegramError("Failed to send message", chat_id="123456")
    """

    def __init__(self, detail: str, chat_id: str | None = None) -> None:
        self.chat_id = chat_id
        super().__init__(detail)


class WhatsAppError(ChannelError):
    """Raised for WhatsApp-specific failures.

    Args:
        detail: Description of the failure.
        provider: The WhatsApp provider in use ('twilio' or 'meta').

    Example:
        >>> raise WhatsAppError("Provider not configured", provider="twilio")
    """

    def __init__(self, detail: str, provider: str | None = None) -> None:
        self.provider = provider
        super().__init__(detail)


# ── Graph errors ───────────────────────────────────────────────────────────────

class GraphError(HealioBaseError):
    """Raised when the LangGraph execution pipeline encounters a failure.

    Covers graph compilation errors, unexpected state, and unhandled node
    failures.
    """


class RoutingError(GraphError):
    """Raised when the Router Node cannot determine a valid routing target.

    Args:
        detail: Description of why routing failed.
        raw_intent: The raw intent string returned by the LLM, if any.

    Example:
        >>> raise RoutingError("LLM returned invalid intent", raw_intent="???")
    """

    def __init__(self, detail: str, raw_intent: str | None = None) -> None:
        self.raw_intent = raw_intent
        super().__init__(detail)


class NodeExecutionError(GraphError):
    """Raised when a specific graph node fails during execution.

    Args:
        detail: Description of the failure.
        node_name: Name of the node that failed.

    Example:
        >>> raise NodeExecutionError("LLM call failed", node_name="emergency_node")
    """

    def __init__(self, detail: str, node_name: str | None = None) -> None:
        self.node_name = node_name
        super().__init__(detail)


# ── Tool errors ────────────────────────────────────────────────────────────────

class ToolError(HealioBaseError):
    """Raised when a LangChain tool encounters an unrecoverable failure."""


class EHRLookupError(ToolError):
    """Raised when the EHR tool fails to look up a patient record.

    Args:
        detail: Description of the failure.
        patient_id: The patient ID that was looked up.

    Example:
        >>> raise EHRLookupError("EHR store unreachable", patient_id="P001")
    """

    def __init__(self, detail: str, patient_id: str | None = None) -> None:
        self.patient_id = patient_id
        super().__init__(detail)


class PatientNotFoundError(EHRLookupError):
    """Raised when a patient ID does not exist in the EHR store.

    Args:
        patient_id: The patient ID that was not found.

    Example:
        >>> raise PatientNotFoundError(patient_id="P999")
    """

    def __init__(self, patient_id: str) -> None:
        super().__init__(
            detail=f"Patient '{patient_id}' not found in EHR store.",
            patient_id=patient_id,
        )


class CalendarToolError(ToolError):
    """Raised when the calendar/scheduling tool fails.

    Args:
        detail: Description of the failure.
        provider: The calendar provider in use ('mock' or 'google').

    Example:
        >>> raise CalendarToolError("Slot unavailable", provider="mock")
    """

    def __init__(self, detail: str, provider: str | None = None) -> None:
        self.provider = provider
        super().__init__(detail)


class AlertToolError(ToolError):
    """Raised when the Human-in-the-Loop alert tool fails to send an alert.

    Args:
        detail: Description of the failure.
        doctor_chat_id: The Telegram chat ID of the doctor, if available.

    Example:
        >>> raise AlertToolError("Telegram send failed", doctor_chat_id="789")
    """

    def __init__(self, detail: str, doctor_chat_id: str | None = None) -> None:
        self.doctor_chat_id = doctor_chat_id
        super().__init__(detail)


# ── NLP errors ─────────────────────────────────────────────────────────────────

class NLPError(HealioBaseError):
    """Raised when the NLP pipeline (BioBERT / HuggingFace) fails."""


class ModelNotLoadedError(NLPError):
    """Raised when a required NLP model has not been loaded at startup.

    This typically means DISABLE_BIOBERT=true but someone called a method
    that requires it, or startup failed silently.

    Args:
        model_name: The name of the model that is not loaded.

    Example:
        >>> raise ModelNotLoadedError(model_name="d4data/biomedical-ner-all")
    """

    def __init__(self, model_name: str) -> None:
        super().__init__(
            detail=f"NLP model '{model_name}' is not loaded. "
            "Check DISABLE_BIOBERT setting and startup logs."
        )
        self.model_name = model_name
