"""
app/channels/whatsapp.py
========================
WhatsApp webhook handler — PLACEHOLDER (not active in MVP).

Why this file exists
--------------------
The WhatsApp endpoint is scaffolded now so:
1. The route exists and returns a clear ``501 Not Implemented`` response
   rather than a confusing ``404 Not Found``.
2. The code structure and pattern are ready — activating WhatsApp only
   requires filling in the handler logic and setting env vars; no structural
   changes to main.py or the graph are needed.
3. Developers know exactly what to implement and configure.

How to activate WhatsApp
------------------------
1. Choose a provider and fill in the required .env variables:

   --- Option A: Twilio ---
   WHATSAPP_PROVIDER=twilio
   WHATSAPP_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   WHATSAPP_AUTH_TOKEN=your_auth_token
   WHATSAPP_FROM_NUMBER=whatsapp:+14155238886

   --- Option B: Meta Cloud API ---
   WHATSAPP_PROVIDER=meta
   WHATSAPP_ACCESS_TOKEN=your_access_token
   WHATSAPP_PHONE_NUMBER_ID=your_phone_number_id
   WHATSAPP_VERIFY_TOKEN=your_verify_token

2. Implement ``_handle_twilio_payload()`` or ``_handle_meta_payload()``
   in this file (marked with TODO comments below).

3. Remove the 501 guard at the top of the ``whatsapp_webhook`` handler.

4. Deploy and register your public URL with Twilio or Meta:
   Twilio:  https://console.twilio.com → Messaging → WhatsApp Sandbox
   Meta:    https://developers.facebook.com → WhatsApp → Configuration
"""

import hashlib
import hmac

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.channels.normalizer import normalize_whatsapp
from app.config import settings
from app.logging_config import get_logger

log = get_logger(__name__)

router = APIRouter()


# ── Webhook verification (Meta Cloud API) ─────────────────────────────────────

@router.get("/whatsapp", tags=["Channels"])
async def whatsapp_verify(request: Request) -> PlainTextResponse:
    """Handle the Meta Cloud API webhook verification challenge.

    Meta sends a GET request with a ``hub.challenge`` parameter when you
    first register the webhook URL. This endpoint responds with the challenge
    value to prove ownership of the URL.

    This endpoint is also a PLACEHOLDER — it returns 501 until
    ``WHATSAPP_PROVIDER=meta`` and ``WHATSAPP_VERIFY_TOKEN`` are configured.

    Args:
        request: The incoming FastAPI request.

    Returns:
        The ``hub.challenge`` value as a plain text response (HTTP 200), or
        HTTP 403 if the verify token does not match, or
        HTTP 501 if WhatsApp is not configured.

    Docs:
        https://developers.facebook.com/docs/graph-api/webhooks/getting-started
    """
    if not settings.whatsapp_provider:
        log.warning("whatsapp_verify_called_but_not_configured")
        return PlainTextResponse("WhatsApp not configured", status_code=501)

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

    STATUS: PLACEHOLDER — Returns HTTP 501 until WhatsApp is configured.

    When activated, this handler will:
    1. Validate the request signature (Twilio HMAC or Meta signature).
    2. Parse the provider-specific payload.
    3. Normalise the message into an ``IncomingMessage`` DTO.
    4. Invoke the LangGraph pipeline.
    5. Send the reply back to the patient via the WhatsApp provider.

    Args:
        request: The incoming FastAPI request containing the webhook payload.

    Returns:
        HTTP 200 acknowledgment JSON (providers expect fast 200 responses).
        HTTP 501 if WhatsApp is not yet configured.
        HTTP 400 on signature validation failure.

    Raises:
        HTTPException: On signature validation failure.
    """
    # ── PLACEHOLDER GUARD ─────────────────────────────────────────────────────
    # Remove this block when WHATSAPP_PROVIDER is configured in .env
    if not settings.whatsapp_provider:
        log.warning(
            "whatsapp_webhook_called_but_not_configured",
            hint="Set WHATSAPP_PROVIDER in .env to activate WhatsApp integration",
        )
        return JSONResponse(
            status_code=501,
            content={
                "error": "not_implemented",
                "detail": (
                    "WhatsApp integration is not configured. "
                    "See .env.example for setup instructions."
                ),
            },
        )
    # ── END PLACEHOLDER GUARD ─────────────────────────────────────────────────

    body = await request.body()

    # Route to the correct provider handler
    from app.constants import WhatsAppProvider  # local import to avoid circular

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

    TODO: Implement when Twilio is configured.
    Steps to implement:
    1. Validate Twilio HMAC-SHA1 signature using X-Twilio-Signature header.
       Docs: https://www.twilio.com/docs/usage/webhooks/webhooks-security
    2. Parse form-encoded body: ``From``, ``Body``, ``ProfileName`` fields.
    3. Normalise: ``normalize_whatsapp(sender_phone=from_, text=body_text)``.
    4. Run LangGraph pipeline (same as Telegram handler).
    5. Send reply via Twilio REST API:
       POST https://api.twilio.com/2010-04-01/Accounts/{SID}/Messages.json

    Args:
        request: The FastAPI request with Twilio headers.
        body: Raw request body bytes.

    Returns:
        HTTP 200 JSON acknowledgment.
    """
    log.warning("twilio_handler_not_implemented")
    return JSONResponse(
        status_code=501,
        content={"detail": "Twilio handler not yet implemented. See TODO in whatsapp.py."},
    )


async def _handle_meta_payload(request: Request, body: bytes) -> JSONResponse:
    """Parse and handle a Meta Cloud API WhatsApp webhook payload.

    TODO: Implement when Meta Cloud API is configured.
    Steps to implement:
    1. Validate Meta signature using X-Hub-Signature-256 header.
       Docs: https://developers.facebook.com/docs/graph-api/webhooks/getting-started#verification-requests
    2. Parse JSON body: entry[0].changes[0].value.messages[0]
    3. Extract: ``from`` (phone), ``text.body`` (message text).
    4. Normalise: ``normalize_whatsapp(sender_phone=from_, text=text_body)``.
    5. Run LangGraph pipeline (same as Telegram handler).
    6. Send reply via Meta Graph API:
       POST https://graph.facebook.com/v17.0/{PHONE_NUMBER_ID}/messages

    Args:
        request: The FastAPI request with Meta headers.
        body: Raw request body bytes.

    Returns:
        HTTP 200 JSON acknowledgment (Meta requires 200 within 20 seconds).
    """
    log.warning("meta_handler_not_implemented")
    return JSONResponse(
        status_code=501,
        content={"detail": "Meta Cloud API handler not yet implemented. See TODO in whatsapp.py."},
    )


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
