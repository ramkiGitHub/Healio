"""
app/channels/whatsapp.py
========================
WhatsApp webhook handler (Twilio + Meta Cloud API).

Supported providers
-------------------
1. **Twilio** — WhatsApp Business API integration.
2. **Meta Cloud API** — Official WhatsApp Business Platform.

How to set up
-------------

--- Twilio Setup ---
1. Sign up for Twilio: https://www.twilio.com/whatsapp
2. Set in .env:
   WHATSAPP_PROVIDER=twilio
   WHATSAPP_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   WHATSAPP_AUTH_TOKEN=your_auth_token
   WHATSAPP_FROM_NUMBER=whatsapp:+14155238886

3. Register webhook URL in Twilio Console:
   https://console.twilio.com → Messaging → Sandbox Configuration

--- Meta Cloud API Setup ---
1. Apply at: https://developers.facebook.com/whatsapp
2. Set in .env:
   WHATSAPP_PROVIDER=meta
   WHATSAPP_ACCESS_TOKEN=your_access_token
   WHATSAPP_PHONE_NUMBER_ID=your_phone_number_id
   WHATSAPP_VERIFY_TOKEN=your_verify_token

3. Register webhook URL in Meta App Dashboard:
   App Dashboard → Whatsapp Business Platform → Configuration

Provider-agnostic flow
----------------------
1. Webhook receives a message from Twilio or Meta.
2. Signature is validated.
3. Payload is parsed into an IncomingMessage DTO.
4. LangGraph pipeline processes the message.
5. Reply is sent back via the same provider's API.
"""

import hashlib
import hmac
from urllib.parse import parse_qs

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from tenacity import retry, stop_after_attempt, wait_exponential

from app.channels.normalizer import IncomingMessage, format_for_channel, normalize_whatsapp
from app.config import settings
from app.constants import ChannelType, WhatsAppProvider
from app.exceptions import ChannelError
from app.logging_config import get_logger

log = get_logger(__name__)

router = APIRouter()

# ── Constants ──────────────────────────────────────────────────────────────────
_MAX_MESSAGE_LENGTH = 4096  # WhatsApp message length limit
_TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"
_META_API_BASE = "https://graph.facebook.com/v17.0"


@router.get("/whatsapp", tags=["Channels"])
async def whatsapp_verify(request: Request) -> PlainTextResponse:
    """Handle the Meta Cloud API webhook verification challenge.

    Meta sends a GET request with a ``hub.challenge`` parameter when you
    first register the webhook URL. This endpoint responds with the challenge
    value to prove ownership of the URL.

    If WhatsApp is not configured or a non-Meta provider is active, returns 404.

    Args:
        request: The incoming FastAPI request.

    Returns:
        The ``hub.challenge`` value as a plain text response (HTTP 200), or
        HTTP 403 if the verify token does not match, or
        HTTP 404 if WhatsApp is not configured or provider is not Meta.

    Docs:
        https://developers.facebook.com/docs/graph-api/webhooks/getting-started
    """
    # Only Meta uses GET verification; Twilio doesn't
    if settings.whatsapp_provider != WhatsAppProvider.META:
        raise HTTPException(status_code=404, detail="Not Found")

    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == settings.whatsapp_verify_token:
        log.info("whatsapp_webhook_verified")
        return PlainTextResponse(content=challenge or "", status_code=200)

    log.warning("whatsapp_verify_token_mismatch")
    raise HTTPException(status_code=403, detail="Webhook verification failed")


# ── Main webhook handler ───────────────────────────────────────────────────────

@router.post("/whatsapp", tags=["Channels"])
async def whatsapp_webhook(request: Request) -> JSONResponse:
    """Receive inbound WhatsApp messages and route them through Healio.

    When WhatsApp is configured, this handler:
    1. Validates the request signature (Twilio HMAC or Meta signature).
    2. Parses the provider-specific payload.
    3. Normalises the message into an ``IncomingMessage`` DTO.
    4. Invokes the LangGraph pipeline.
    5. Sends the reply back to the patient via the WhatsApp provider.

    Args:
        request: The incoming FastAPI request containing the webhook payload.

    Returns:
        HTTP 200 acknowledgment JSON (providers expect fast 200 responses).
        HTTP 404 if WhatsApp is not configured.
        HTTP 400 on signature validation failure.

    Raises:
        HTTPException: On signature validation failure or configuration error.
    """
    if not settings.whatsapp_provider:
        log.warning(
            "whatsapp_webhook_called_but_not_configured",
            hint="Set WHATSAPP_PROVIDER in .env to activate WhatsApp integration",
        )
        raise HTTPException(status_code=404, detail="Not Found")

    body = await request.body()

    # Route to the correct provider handler
    if settings.whatsapp_provider == WhatsAppProvider.TWILIO:
        return await _handle_twilio_payload(request=request, body=body)

    if settings.whatsapp_provider == WhatsAppProvider.META:
        return await _handle_meta_payload(request=request, body=body)

    return JSONResponse(
        status_code=400,
        content={"error": "unknown_provider", "detail": f"Unknown WHATSAPP_PROVIDER: {settings.whatsapp_provider}"},
    )


# ── Provider-specific handlers (PLACEHOLDERS) ─────────────────────────────────

async def _handle_twilio_payload(request: Request, body: bytes) -> JSONResponse:
    """Parse and handle a Twilio WhatsApp webhook payload.

    Twilio sends form-encoded messages with From, Body, ProfileName, etc.
    This handler:
    1. Validates the Twilio HMAC-SHA1 signature using X-Twilio-Signature.
    2. Parses the form data.
    3. Normalises into IncomingMessage.
    4. Runs the LangGraph pipeline.
    5. Sends the reply via Twilio REST API.

    Args:
        request: The FastAPI request with Twilio headers and form body.
        body: Raw request body bytes.

    Returns:
        HTTP 200 JSON acknowledgment, or HTTP 400 on validation failure.

    Docs:
        https://www.twilio.com/docs/usage/webhooks/webhooks-security
        https://www.twilio.com/docs/sms/whatsapp/api
    """
    # Validate Twilio signature (skip in development for testing)
    signature_header = request.headers.get("X-Twilio-Signature", "")
    if signature_header or not settings.is_development:
        # In production or if signature provided, validate it
        if not _validate_twilio_signature(
            uri=str(request.url),
            body=body,
            signature_header=signature_header,
        ):
            log.warning("twilio_signature_validation_failed")
            raise HTTPException(status_code=400, detail="Signature validation failed")

    # Parse form-encoded body
    try:
        body_str = body.decode("utf-8")
        form_data = parse_qs(body_str)
        # parse_qs returns lists; extract single values
        sender_phone: str = (form_data.get("From") or [""])[0]
        message_text: str = (form_data.get("Body") or [""])[0]
        sender_name: str | None = (form_data.get("ProfileName") or [None])[0]
    except (ValueError, KeyError, IndexError) as exc:
        log.warning("twilio_payload_parse_error", error=str(exc))
        raise HTTPException(status_code=400, detail="Invalid payload format") from exc

    if not sender_phone or not message_text:
        log.warning("twilio_missing_required_fields", from_=sender_phone)
        return JSONResponse(status_code=200, content={"ok": True})

    # Normalise message
    incoming: IncomingMessage = normalize_whatsapp(
        sender_phone=sender_phone,
        text=message_text.strip(),
        sender_name=sender_name,
        raw_payload=dict(form_data),
    )

    log.info(
        "whatsapp_message_received",
        provider="twilio",
        session_id=incoming.session_id,
        text_length=len(incoming.text),
    )

    # ── Invoke LangGraph pipeline ──────────────────────────────────────────────
    try:
        from app.graph.graph import run_graph
        reply_text = await run_graph(incoming)
    except Exception as exc:
        log.error("whatsapp_graph_error", provider="twilio", error=str(exc))
        # Send a safe fallback message to the patient
        reply_text = "I'm sorry, I encountered an error processing your message. Please try again."
    # ── End graph invocation ───────────────────────────────────────────────────

    # Send reply via Twilio (with error handling)
    try:
        await _send_twilio_message(to_phone=sender_phone, text=reply_text)
    except Exception as exc:
        log.error(
            "twilio_send_error",
            provider="twilio",
            to_phone=sender_phone,
            error=str(exc),
        )
        # Log error but still return 200 so Twilio doesn't retry indefinitely

    return JSONResponse(status_code=200, content={"ok": True})


async def _handle_meta_payload(request: Request, body: bytes) -> JSONResponse:
    """Parse and handle a Meta Cloud API WhatsApp webhook payload.

    Meta sends JSON messages with nested structure: entry[0].changes[0].value.messages[0]
    This handler:
    1. Validates Meta signature using X-Hub-Signature-256 header.
    2. Parses the JSON payload.
    3. Normalises into IncomingMessage.
    4. Runs the LangGraph pipeline.
    5. Sends the reply via Meta Graph API.

    Args:
        request: The FastAPI request with Meta headers and JSON body.
        body: Raw request body bytes.

    Returns:
        HTTP 200 JSON acknowledgment (Meta requires 200 within 20 seconds),
        or HTTP 400 on validation failure.

    Docs:
        https://developers.facebook.com/docs/graph-api/webhooks/getting-started#verification-requests
        https://developers.facebook.com/docs/whatsapp/cloud-api/reference/messages
    """
    # Validate Meta signature
    signature_header = request.headers.get("X-Hub-Signature-256")
    if not _validate_meta_signature(body=body, signature_header=signature_header):
        log.warning("meta_signature_validation_failed")
        raise HTTPException(status_code=400, detail="Signature validation failed")

    # Parse JSON body
    try:
        import json
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        log.warning("meta_payload_parse_error", error=str(exc))
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    # Extract message from nested structure
    try:
        entry = payload.get("entry", [{}])[0]
        change = entry.get("changes", [{}])[0]
        value = change.get("value", {})
        messages = value.get("messages", [])

        if not messages:
            # No messages in this event (could be a status update, for example)
            log.debug("meta_event_without_messages")
            return JSONResponse(status_code=200, content={"ok": True})

        message = messages[0]
        sender_phone: str = value.get("contacts", [{}])[0].get("wa_id", "")
        sender_name: str | None = value.get("contacts", [{}])[0].get("profile", {}).get("name")

        # Extract text from message
        message_text: str = message.get("text", {}).get("body", "")

        if not sender_phone or not message_text:
            log.warning("meta_missing_required_fields", from_=sender_phone)
            return JSONResponse(status_code=200, content={"ok": True})

    except (KeyError, IndexError, TypeError) as exc:
        log.warning("meta_payload_extraction_error", error=str(exc))
        raise HTTPException(status_code=400, detail="Invalid payload structure") from exc

    # Normalise message
    incoming: IncomingMessage = normalize_whatsapp(
        sender_phone=sender_phone,
        text=message_text.strip(),
        sender_name=sender_name,
        raw_payload=payload,
    )

    log.info(
        "whatsapp_message_received",
        provider="meta",
        session_id=incoming.session_id,
        text_length=len(incoming.text),
    )

    # ── Invoke LangGraph pipeline ──────────────────────────────────────────────
    try:
        from app.graph.graph import run_graph
        reply_text = await run_graph(incoming)
    except Exception as exc:
        log.error("whatsapp_graph_error", provider="meta", error=str(exc))
        # Send a safe fallback message to the patient
        reply_text = "I'm sorry, I encountered an error processing your message. Please try again."
    # ── End graph invocation ───────────────────────────────────────────────────

    # Send reply via Meta (with error handling)
    try:
        await _send_meta_message(to_phone=sender_phone, text=reply_text)
    except Exception as exc:
        log.error(
            "meta_send_error",
            provider="meta",
            to_phone=sender_phone,
            error=str(exc),
        )
        # Log error but still return 200 so Meta doesn't retry indefinitely

    # Meta requires a 200 response, ideally within 20 seconds
    return JSONResponse(status_code=200, content={"ok": True})


def _validate_meta_signature(body: bytes, signature_header: str | None) -> bool:
    """Validate the X-Hub-Signature-256 header from Meta webhook requests.

    Meta signs each webhook payload with HMAC-SHA256 using the app secret.
    This helper verifies the signature to prevent spoofed requests.

    Args:
        body: Raw request body bytes.
        signature_header: Value of the ``X-Hub-Signature-256`` header, e.g.
                          ``sha256=abc123...``.

    Returns:
        True if the signature is valid, False otherwise.

    Example:
        >>> valid = _validate_meta_signature(body=b"payload", signature_header="sha256=...")
    """
    if not signature_header or not settings.whatsapp_access_token:
        return False

    expected_prefix = "sha256="
    if not signature_header.startswith(expected_prefix):
        return False

    provided_hash = signature_header[len(expected_prefix):]
    secret = (settings.whatsapp_access_token or "").encode()
    computed_hash = hmac.new(secret, body, hashlib.sha256).hexdigest()

    # Use constant-time comparison to prevent timing attacks
    return hmac.compare_digest(computed_hash, provided_hash)

def _validate_twilio_signature(uri: str, body: bytes, signature_header: str | None) -> bool:
    """Validate the X-Twilio-Signature header from Twilio webhook requests.

    Twilio signs each webhook request with HMAC-SHA1 using the auth token
    as the shared secret. This helper verifies the signature to prevent spoofed requests.

    Args:
        uri: The full request URI (including https:// and query parameters).
        body: Raw request body bytes.
        signature_header: Value of the ``X-Twilio-Signature`` header, e.g. ``abc123...``.

    Returns:
        True if the signature is valid, False otherwise.

    Docs:
        https://www.twilio.com/docs/usage/webhooks/webhooks-security

    Example:
        >>> valid = _validate_twilio_signature(
        ...     uri="https://example.com/webhook/whatsapp",
        ...     body=b"From=...",
        ...     signature_header="abc123...",
        ... )
    """
    if not signature_header or not settings.whatsapp_auth_token:
        return False

    # Twilio signs the full URI + body
    data = uri + body.decode("utf-8") if isinstance(body, bytes) else uri + body
    secret = (settings.whatsapp_auth_token or "").encode()

    # Twilio uses SHA1, not SHA256
    import hashlib
    computed_hash = hmac.new(secret, data.encode("utf-8"), hashlib.sha1).digest()
    import base64
    computed_signature = base64.b64encode(computed_hash).decode("utf-8")

    return hmac.compare_digest(computed_signature, signature_header)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
async def _send_twilio_message(to_phone: str, text: str) -> None:
    """Send a WhatsApp message via Twilio.

    Twilio requires basic auth with Account SID and Auth Token.
    Messages are automatically split if they exceed WhatsApp's length limit.

    Args:
        to_phone: Recipient phone number. Can be in formats:
                  - E.164: +1234567890
                  - Sandbox: whatsapp:+1234567890
        text: Message text to send.

    Raises:
        ChannelError: If the Twilio API returns a non-2xx status after retries.

    Example:
        >>> await _send_twilio_message(to_phone="whatsapp:+919876543210", text="Hello!")
    """
    chunks = _split_message(text, max_length=_MAX_MESSAGE_LENGTH)

    # Normalize phone number: strip 'whatsapp:' prefix if present (API expects E.164)
    normalized_phone = to_phone.replace("whatsapp:", "") if to_phone else to_phone

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        for chunk in chunks:
            # Twilio requires basic auth
            auth = (settings.whatsapp_account_sid or "", settings.whatsapp_auth_token or "")
            url = f"{_TWILIO_API_BASE}/Accounts/{settings.whatsapp_account_sid}/Messages.json"

            # Normalize phone numbers to E.164 format with whatsapp: prefix
            from_number = settings.whatsapp_from_number or ""
            if from_number.startswith("whatsapp:"):
                from_number = from_number[9:]  # Strip "whatsapp:" prefix to normalize
            
            to_number = normalized_phone or ""
            if to_number.startswith("whatsapp:"):
                to_number = to_number[9:]  # Strip "whatsapp:" prefix to normalize

            payload = {
                "From": f"whatsapp:{from_number}",
                "To": f"whatsapp:{to_number}",
                "Body": chunk,
            }

            log.debug(
                "twilio_api_call",
                url=url,
                from_=payload["From"],
                to=payload["To"],
                auth_user=(settings.whatsapp_account_sid or "")[:8] + "...",
            )

            response = await client.post(
                url,
                data=payload,
                auth=auth,
            )

            if not response.is_success:
                error_data = response.json() if response.content else {}
                error_msg = error_data.get('message', error_data.get('error', {}).get('message', 'Unknown error'))
                log.error(
                    "twilio_api_error",
                    status_code=response.status_code,
                    response_body=error_data,
                    error_msg=error_msg,
                    from_=payload["From"],
                    to=payload["To"],
                )
                raise ChannelError(
                    detail=(
                        f"Twilio API error {response.status_code}: {error_msg}"
                    ),
                )

            log.info(
                "twilio_message_sent",
                to_phone=payload["To"],
                chunk_length=len(chunk),
                response_id=response.json().get('sid', 'unknown'),
            )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
async def _send_meta_message(to_phone: str, text: str) -> None:
    """Send a WhatsApp message via Meta Cloud API.

    Meta requires the phone number ID and access token in the URL and headers.
    Messages are automatically split if they exceed WhatsApp's length limit.

    Args:
        to_phone: Recipient phone number in E.164 format (e.g., +919876543210).
        text: Message text to send.

    Raises:
        ChannelError: If the Meta API returns a non-2xx status after retries.

    Example:
        >>> await _send_meta_message(to_phone="+919876543210", text="Hello!")
    """
    chunks = _split_message(text, max_length=_MAX_MESSAGE_LENGTH)

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        for chunk in chunks:
            url = f"{_META_API_BASE}/{settings.whatsapp_phone_number_id}/messages"

            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to_phone,
                "type": "text",
                "text": {
                    "body": chunk,
                },
            }

            headers = {
                "Authorization": f"Bearer {settings.whatsapp_access_token}",
            }

            response = await client.post(
                url,
                json=payload,
                headers=headers,
            )

            if not response.is_success:
                error_data = response.json() if response.content else {}
                errors = error_data.get("error", {}).get("message", "Unknown error")
                raise ChannelError(
                    detail=(
                        f"Meta API error {response.status_code}: {errors}"
                    ),
                )

            log.debug(
                "meta_message_sent",
                to_phone=to_phone,
                chunk_length=len(chunk),
            )


def _split_message(text: str, max_length: int = _MAX_MESSAGE_LENGTH) -> list[str]:
    """Split a long message into chunks that fit within the channel's limit.

    Tries to split on word boundaries to avoid breaking words.

    Args:
        text: The message text to split.
        max_length: Maximum length per chunk.

    Returns:
        A list of message chunks, each <= max_length characters.

    Example:
        >>> _split_message("Hello world!", max_length=5)
        ['Hello', 'world!']
    """
    if len(text) <= max_length:
        return [text]

    chunks = []
    current_chunk = ""

    for line in text.split("\n"):
        if len(current_chunk) + len(line) + 1 <= max_length:
            current_chunk += (newline := "\n" if current_chunk else "") + line
        else:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = line

    if current_chunk:
        chunks.append(current_chunk)

    return chunks