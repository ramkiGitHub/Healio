"""
app/tools/alerts.py
===================
Human-in-the-Loop (HITL) emergency alert tool — sends real-time alerts to the
on-call doctor via Telegram when a patient message is classified as EMERGENCY.

Why this file exists
--------------------
When a patient reports a life-threatening symptom (chest pain, stroke, etc.),
Healio must immediately notify the on-call doctor. This is the
Human-in-the-Loop component: the AI handles triage and notification, but a
human doctor takes over for actual emergency response.

Alert delivery channel: Telegram Bot API
Recipient: DOCTOR_CHAT_ID from settings (the doctor's personal Telegram chat)

Why synchronous HTTP?
---------------------
LangGraph nodes are invoked synchronously via ``compiled_graph.invoke()``.
Using ``httpx.Client`` (sync) keeps the alerting code simple and avoids
nested event loop issues. The alert is a single short HTTP POST and
completes within 1–2 seconds on a good connection.

Retry strategy
--------------
Uses tenacity with 3 attempts and exponential backoff (1s → 2s → 4s).
If all retries fail, ``AlertToolError`` is raised — the caller (emergency_node)
catches this, logs the failure, and still sends the patient-facing response.
The alert failure is logged as a critical error for ops visibility.

Usage
-----
    from app.tools.alerts import HITLAlertTool

    tool = HITLAlertTool()
    tool.send_alert(
        patient_id="P001",
        patient_name="Anjali Sharma",
        message="I have severe chest pain",
        session_id="telegram:123456789",
        severity="emergency",
    )
"""

from datetime import UTC, datetime

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.constants import HTTP_TIMEOUT_SECONDS, MAX_RETRY_ATTEMPTS
from app.exceptions import AlertToolError
from app.logging_config import get_logger

log = get_logger(__name__)


class HITLAlertTool:
    """Sends structured emergency alert messages to the on-call doctor via Telegram.

    Constructs a formatted Markdown alert message containing:
    - Severity emoji and level
    - Patient name and ID
    - Session ID (for conversation lookup)
    - Timestamp (UTC)
    - Preview of the patient's emergency message

    Uses ``httpx.Client`` (synchronous) because LangGraph nodes are
    invoked synchronously.

    Usage:
        tool = HITLAlertTool()
        tool.send_alert(
            patient_id="P001",
            patient_name="Anjali Sharma",
            message="Can't breathe properly",
            session_id="telegram:123456789",
            severity="emergency",
        )
    """

    @retry(
        stop=stop_after_attempt(MAX_RETRY_ATTEMPTS),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        reraise=True,
    )
    def send_alert(
        self,
        patient_id: str,
        patient_name: str,
        message: str,
        session_id: str,
        severity: str = "emergency",
    ) -> None:
        """Send an emergency HITL alert to the on-call doctor's Telegram chat.

        Formats a structured Markdown alert and POSTs it to the Telegram
        Bot API ``sendMessage`` endpoint. Retries up to ``MAX_RETRY_ATTEMPTS``
        times on transient network errors.

        Args:
            patient_id: The patient's unique identifier (e.g., ``"P001"``).
            patient_name: Patient display name for the alert message.
                          Pass an empty string if unknown.
            message: The patient's emergency message. Truncated to 200 chars
                     in the alert to keep it readable on mobile.
            session_id: The LangGraph session/thread ID so the doctor can
                        look up the full conversation in logs.
            severity: Severity level string — ``"emergency"`` or ``"urgent"``.
                      Controls the alert emoji and capitalisation.

        Raises:
            AlertToolError: If the Telegram API returns an error status or
                            the network request fails after all retries.

        Example:
            >>> tool = HITLAlertTool()
            >>> tool.send_alert("P001", "Anjali", "chest pain", "tg:123", "emergency")
        """
        api_url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
        alert_text = self._format_alert(
            patient_id=patient_id,
            patient_name=patient_name,
            message=message,
            session_id=session_id,
            severity=severity,
        )

        log.info(
            "sending_hitl_alert",
            patient_id=patient_id,
            session_id=session_id,
            severity=severity,
            doctor_chat_id=settings.doctor_chat_id,
        )

        try:
            with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
                response = client.post(
                    api_url,
                    json={
                        "chat_id": settings.doctor_chat_id,
                        "text": alert_text,
                        "parse_mode": "Markdown",
                    },
                )
                response.raise_for_status()

        except httpx.HTTPStatusError as exc:
            log.error(
                "alert_tool_http_error",
                status_code=exc.response.status_code,
                patient_id=patient_id,
                session_id=session_id,
            )
            raise AlertToolError(
                detail=(
                    f"Telegram alert delivery failed with HTTP {exc.response.status_code} "
                    f"for session '{session_id}'"
                ),
                doctor_chat_id=settings.doctor_chat_id,
            ) from exc

        except httpx.RequestError as exc:
            log.error(
                "alert_tool_request_error",
                error=str(exc),
                patient_id=patient_id,
                session_id=session_id,
            )
            raise AlertToolError(
                detail=f"Telegram alert request failed: {exc}",
                doctor_chat_id=settings.doctor_chat_id,
            ) from exc

        log.info(
            "hitl_alert_delivered",
            patient_id=patient_id,
            session_id=session_id,
            severity=severity,
        )

    @staticmethod
    def _format_alert(
        patient_id: str,
        patient_name: str,
        message: str,
        session_id: str,
        severity: str,
    ) -> str:
        """Build the Telegram Markdown alert message string.

        Args:
            patient_id: Patient identifier.
            patient_name: Patient display name (empty string if unknown).
            message: Patient's emergency message (truncated to 200 chars).
            session_id: Conversation session ID for traceability.
            severity: Severity level string — controls emoji and header text.

        Returns:
            A formatted Telegram Markdown string, ready to POST to the API.

        Example:
            >>> HITLAlertTool._format_alert("P001", "Anjali", "chest pain", "tg:1", "emergency")
            '🚨 *HEALIO ALERT — EMERGENCY*\\n\\n*Patient:* Anjali (`P001`)...'
        """
        severity_emoji = "🚨" if severity == "emergency" else "⚠️"
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        message_preview = message[:200] + ("..." if len(message) > 200 else "")
        display_name = patient_name.strip() if patient_name.strip() else "Unknown Patient"

        return (
            f"{severity_emoji} *HEALIO ALERT — {severity.upper()}*\n\n"
            f"*Patient:* {display_name} (`{patient_id}`)\n"
            f"*Session:* `{session_id}`\n"
            f"*Time:* {timestamp}\n\n"
            f"*Patient message:*\n_{message_preview}_\n\n"
            f"Please respond to the patient immediately via this chat."
        )
