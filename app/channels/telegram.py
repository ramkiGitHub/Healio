"""
app/channels/telegram.py
========================
Telegram Bot API webhook and polling handler.

Why this file exists
--------------------
This module is the Telegram entry point for Healio. It is responsible for:
1. Receiving messages from Telegram (via webhook or long-polling).
2. Validating the incoming payload (signature check for webhooks).
3. Sanitising the message text before it enters the AI pipeline.
4. Normalising the Telegram-specific payload into a channel-agnostic
   ``IncomingMessage`` DTO.
5. Invoking the LangGraph pipeline with the normalised message.
6. Sending the graph's response back to the patient on Telegram.

Webhook vs. polling
-------------------
- **Webhook mode** (production): Telegram delivers messages via HTTP POST to
  ``/webhook/telegram``. Requires a public HTTPS URL. Set
  ``TELEGRAM_WEBHOOK_URL`` in ``.env``.
- **Polling mode** (development): The bot polls Telegram's ``getUpdates``
  endpoint in a background loop. Runs automatically when
  ``TELEGRAM_WEBHOOK_URL`` is blank.

Usage
-----
The ``router`` exported from this module is mounted in ``app/main.py``.
The LangGraph pipeline invocation will be wired in Phase 1 completion
(currently calls a stub that returns an echo response).

How to extend
-------------
- Add handlers for other Telegram message types (voice, location, photos)
  by checking ``update.message.voice``, ``update.message.location`` etc.
  in ``_process_telegram_update()``.
- Add inline keyboard / button support for appointment confirmations.
"""

import hashlib
import hmac
import html
import json
import re

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from tenacity import retry, stop_after_attempt, wait_exponential

from app.channels.normalizer import IncomingMessage, format_for_channel, normalize_telegram
from app.config import settings
from app.constants import ChannelType
from app.exceptions import TelegramError
from app.logging_config import get_logger

log = get_logger(__name__)

router = APIRouter()

# Telegram Bot API base URL
_TELEGRAM_API_BASE = f"https://api.telegram.org/bot{settings.telegram_bot_token}"

# Maximum length of a Telegram message (Telegram limit: 4096 chars)
_MAX_MESSAGE_LENGTH = 4096

# Regex for basic input sanitisation — strips control characters
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


# ── Webhook endpoint ───────────────────────────────────────────────────────────

@router.post("/telegram", tags=["Channels"])
async def telegram_webhook(request: Request) -> JSONResponse:
    """Receive inbound Telegram messages via webhook.

    Telegram calls this endpoint via HTTP POST for every new message.
    This handler:
    1. Reads and validates the raw JSON body.
    2. Verifies the request is genuinely from Telegram (in production).
    3. Extracts the message text and sender details.
    4. Invokes the Healio graph pipeline.
    5. Sends the AI response back to the patient.

    Telegram expects this endpoint to return HTTP 200 within 60 seconds.
    If it takes longer, Telegram will retry delivery.

    Args:
        request: The incoming FastAPI request from Telegram.

    Returns:
        HTTP 200 JSON ``{"ok": true}`` on success, which tells Telegram the
        message was received and processed.

    Raises:
        HTTPException: HTTP 400 if the payload is malformed.
        HTTPException: HTTP 403 if signature validation fails (production only).
    """
    body = await request.body()

    # In production, verify the request is from Telegram using the bot token
    # as a shared secret (Telegram signs updates with X-Telegram-Bot-Api-Secret-Token)
    if settings.is_production:
        secret_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if not _validate_telegram_secret(secret_token):
            log.warning("telegram_webhook_invalid_secret", path=str(request.url))
            raise HTTPException(status_code=403, detail="Invalid secret token")

    # Parse the JSON payload
    try:
        update = json.loads(body)
    except json.JSONDecodeError as exc:
        log.warning("telegram_webhook_invalid_json", error=str(exc))
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    # Process the update asynchronously (do not block the webhook response)
    await _process_telegram_update(update)

    return JSONResponse(content={"ok": True})


# ── Update processing ──────────────────────────────────────────────────────────

async def _process_telegram_update(update: dict) -> None:
    """Parse a Telegram Update object and route it through the Healio pipeline.

    Handles text messages only in MVP. Future extensions can add support for
    voice, location, and document messages here.

    Args:
        update: The raw Telegram Update dict as received from the webhook.

    Returns:
        None. Side effect: sends an AI-generated reply to the patient.
    """
    message = update.get("message") or update.get("edited_message")
    if not message:
        # Not a message update (could be a callback query, inline query, etc.)
        # These are ignored in MVP
        log.debug("telegram_non_message_update", update_id=update.get("update_id"))
        return

    # Extract text — could be None for media messages
    raw_text: str | None = message.get("text")
    if not raw_text:
        log.debug(
            "telegram_non_text_message_ignored",
            chat_id=message.get("chat", {}).get("id"),
        )
        await send_telegram_message(
            chat_id=str(message["chat"]["id"]),
            text="I can only process text messages at the moment. Please type your query.",
        )
        return

    # Extract sender details
    sender = message.get("from", {})
    sender_id: int = sender.get("id", 0)
    sender_first_name: str | None = sender.get("first_name")
    sender_last_name: str | None = sender.get("last_name")
    chat_id: str = str(message["chat"]["id"])

    # Sanitise input before it enters the AI pipeline
    clean_text = _sanitise_input(raw_text)

    if not clean_text:
        log.warning(
            "telegram_empty_text_after_sanitisation",
            sender_id=sender_id,
            raw_length=len(raw_text),
        )
        await send_telegram_message(
            chat_id=chat_id,
            text="I received an empty message. Could you please try again?",
        )
        return

    # Normalise into the channel-agnostic DTO
    incoming: IncomingMessage = normalize_telegram(
        sender_id=sender_id,
        text=clean_text,
        sender_first_name=sender_first_name,
        sender_last_name=sender_last_name,
        raw_payload=update,
    )

    log.info(
        "telegram_message_received",
        session_id=incoming.session_id,
        patient_id=incoming.patient_id,
        text_length=len(incoming.text),
    )

    # ── Invoke LangGraph pipeline ──────────────────────────────────────────────
    from app.graph.graph import run_graph

    reply_text = await run_graph(incoming)
    # ── End graph invocation ───────────────────────────────────────────────────

    # Format reply for Telegram (Markdown mode)
    outgoing = format_for_channel(
        session_id=incoming.session_id,
        channel=ChannelType.TELEGRAM,
        text=reply_text,
    )

    # Send reply back to the patient
    await send_telegram_message(
        chat_id=chat_id,
        text=outgoing.text,
        parse_mode=outgoing.parse_mode,
    )


# ── Telegram send function ─────────────────────────────────────────────────────

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
async def send_telegram_message(
    chat_id: str,
    text: str,
    parse_mode: str | None = "Markdown",
) -> None:
    """Send a text message to a Telegram chat.

    Uses the Telegram ``sendMessage`` API method. Retries up to 3 times
    with exponential backoff on transient failures (network errors, Telegram
    rate limiting).

    Long messages are automatically split into chunks to stay within
    Telegram's 4096-character message limit.

    Args:
        chat_id: The Telegram chat ID to send the message to.
        text: The message text. Supports Markdown if ``parse_mode="Markdown"``.
        parse_mode: Telegram parse mode. "Markdown", "HTML", or None.

    Raises:
        TelegramError: If the Telegram API returns a non-2xx status after
                       all retry attempts are exhausted.

    Example:
        >>> await send_telegram_message(
        ...     chat_id="123456",
        ...     text="Hello, how can I help you?",
        ... )
    """
    chunks = _split_message(text, max_length=_MAX_MESSAGE_LENGTH)

    async with httpx.AsyncClient(timeout=10.0) as client:
        for chunk in chunks:
            payload: dict[str, str | None] = {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": parse_mode,
            }
            # Remove None values — Telegram API ignores missing optional params
            payload = {k: v for k, v in payload.items() if v is not None}

            response = await client.post(
                f"{_TELEGRAM_API_BASE}/sendMessage",
                json=payload,
            )

            if not response.is_success:
                error_data = response.json() if response.content else {}
                raise TelegramError(
                    detail=(
                        f"Telegram API error {response.status_code}: "
                        f"{error_data.get('description', 'Unknown error')}"
                    ),
                    chat_id=chat_id,
                )

            log.debug(
                "telegram_message_sent",
                chat_id=chat_id,
                chunk_length=len(chunk),
            )


# ── Helper functions ───────────────────────────────────────────────────────────

def _sanitise_input(text: str) -> str:
    """Sanitise patient message text before passing to the AI pipeline.

    Removes control characters that could interfere with JSON serialisation
    or cause unexpected LLM behaviour. Truncates extremely long inputs to
    prevent token abuse.

    Args:
        text: Raw text from the Telegram message.

    Returns:
        Cleaned text safe for use in the AI pipeline.

    Example:
        >>> _sanitise_input("Hello\x00 world")
        'Hello world'
    """
    # Remove control characters (keep newlines \n and tabs \t)
    clean = _CONTROL_CHARS_RE.sub("", text)

    # Unescape HTML entities (Telegram may encode & as &amp; etc.)
    clean = html.unescape(clean)

    # Truncate to prevent excessively long inputs from blowing up token budgets
    max_length = 2000
    if len(clean) > max_length:
        log.warning("input_truncated", original_length=len(clean), max_length=max_length)
        clean = clean[:max_length]

    return clean.strip()


def _validate_telegram_secret(secret_token: str | None) -> bool:
    """Validate the X-Telegram-Bot-Api-Secret-Token header.

    When setting up a Telegram webhook, you can specify a secret token.
    Telegram includes this token in every webhook request so you can verify
    the request is genuinely from Telegram.

    In development mode this always returns True (no validation needed
    since the local server isn't publicly accessible).

    Args:
        secret_token: The value of the ``X-Telegram-Bot-Api-Secret-Token``
                      header from the incoming request.

    Returns:
        True if the token is valid or if running in development mode.

    Note:
        The secret token is derived from the bot token using SHA-256.
        For production use, set a ``TELEGRAM_WEBHOOK_SECRET`` env var
        (can be added to ``app/config.py`` when needed).
    """
    if settings.is_development:
        return True

    if not secret_token:
        return False

    # Derive expected secret from bot token
    expected = hashlib.sha256(settings.telegram_bot_token.encode()).hexdigest()[:32]
    return hmac.compare_digest(secret_token, expected)


def _split_message(text: str, max_length: int = 4096) -> list[str]:
    """Split a long message into chunks that fit within Telegram's size limit.

    Attempts to split on paragraph boundaries (double newline) first,
    then on single newlines, then on word boundaries, and finally on
    character boundaries as a last resort.

    Args:
        text: The message text to split.
        max_length: Maximum length per chunk. Defaults to 4096 (Telegram limit).

    Returns:
        A list of text chunks, each within the max_length limit.

    Example:
        >>> chunks = _split_message("A" * 5000, max_length=4096)
        >>> len(chunks)
        2
    """
    if len(text) <= max_length:
        return [text]

    chunks = []
    while len(text) > max_length:
        split_at = text.rfind("\n\n", 0, max_length)
        if split_at == -1:
            split_at = text.rfind("\n", 0, max_length)
        if split_at == -1:
            split_at = text.rfind(" ", 0, max_length)
        if split_at == -1:
            split_at = max_length

        chunks.append(text[:split_at].strip())
        text = text[split_at:].strip()

    if text:
        chunks.append(text)

    return chunks


# ── Graph stub (temporary) ─────────────────────────────────────────────────────

async def _graph_stub(message: IncomingMessage) -> str:
    """Temporary stub that returns an echo response until the graph is wired.

    This function will be replaced by a real ``run_graph()`` call
    once ``app/graph/graph.py`` is implemented (Phase 1 completion).

    Args:
        message: The normalised incoming patient message.

    Returns:
        A simple echo string confirming Healio received the message.
    """
    log.debug("graph_stub_called", session_id=message.session_id)
    return (
        f"👋 Hi {message.sender_name or 'there'}! Healio received your message:\n\n"
        f"_{message.text}_\n\n"
        f"The AI pipeline is being connected. Stay tuned!"
    )
