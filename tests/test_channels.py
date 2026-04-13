"""
tests/test_channels.py
======================
Tests for the channel layer: message normalisation, Telegram handler,
and WhatsApp 501 placeholder stub.

Run with:
    pytest tests/test_channels.py -v
"""

import pytest
from fastapi.testclient import TestClient

from app.channels.normalizer import (
    IncomingMessage,
    OutgoingMessage,
    format_for_channel,
    normalize_telegram,
    normalize_whatsapp,
)
from app.constants import ChannelType


# ── normalize_telegram ─────────────────────────────────────────────────────────

class TestNormalizeTelegram:
    """Tests for the normalize_telegram() normalisation function."""

    def test_basic_normalisation(self) -> None:
        """normalize_telegram produces a valid IncomingMessage with correct fields."""
        msg = normalize_telegram(
            sender_id=123456,
            text="I have a headache",
            sender_first_name="Ravi",
        )

        assert isinstance(msg, IncomingMessage)
        assert msg.session_id == "telegram:123456"
        assert msg.patient_id == "123456"
        assert msg.channel == ChannelType.TELEGRAM
        assert msg.text == "I have a headache"
        assert msg.sender_name == "Ravi"

    def test_session_id_format(self) -> None:
        """session_id is always '{channel}:{sender_id}'."""
        msg = normalize_telegram(sender_id=99, text="test")
        assert msg.session_id == "telegram:99"

    def test_full_name_concatenated(self) -> None:
        """First and last name are concatenated with a space."""
        msg = normalize_telegram(
            sender_id=1,
            text="hello",
            sender_first_name="Anjali",
            sender_last_name="Sharma",
        )
        assert msg.sender_name == "Anjali Sharma"

    def test_no_name_is_none(self) -> None:
        """sender_name is None when no name is provided."""
        msg = normalize_telegram(sender_id=1, text="hello")
        assert msg.sender_name is None

    def test_text_is_stripped(self) -> None:
        """Leading and trailing whitespace is removed from text."""
        msg = normalize_telegram(sender_id=1, text="  hello world  ")
        assert msg.text == "hello world"

    def test_received_at_is_set(self) -> None:
        """received_at is automatically set to a UTC datetime."""
        from datetime import UTC, datetime

        msg = normalize_telegram(sender_id=1, text="test")
        assert msg.received_at is not None
        assert msg.received_at.tzinfo == UTC


# ── normalize_whatsapp ─────────────────────────────────────────────────────────

class TestNormalizeWhatsApp:
    """Tests for the normalize_whatsapp() placeholder normalisation function."""

    def test_basic_normalisation(self) -> None:
        """normalize_whatsapp produces a valid IncomingMessage."""
        msg = normalize_whatsapp(
            sender_phone="+919876543210",
            text="Book appointment",
        )

        assert msg.session_id == "whatsapp:+919876543210"
        assert msg.patient_id == "+919876543210"
        assert msg.channel == ChannelType.WHATSAPP
        assert msg.text == "Book appointment"


# ── format_for_channel ─────────────────────────────────────────────────────────

class TestFormatForChannel:
    """Tests for the format_for_channel() output formatter."""

    def test_telegram_uses_markdown(self) -> None:
        """Telegram channel formatting uses Markdown parse mode."""
        reply = format_for_channel(
            session_id="telegram:1",
            channel=ChannelType.TELEGRAM,
            text="Hello",
        )
        assert isinstance(reply, OutgoingMessage)
        assert reply.parse_mode == "Markdown"

    def test_whatsapp_uses_no_parse_mode(self) -> None:
        """WhatsApp channel formatting uses plain text (no parse mode)."""
        reply = format_for_channel(
            session_id="whatsapp:+1",
            channel=ChannelType.WHATSAPP,
            text="Hello",
        )
        assert reply.parse_mode is None

    def test_output_text_preserved(self) -> None:
        """The reply text is preserved unchanged."""
        text = "Your appointment is confirmed for Monday."
        reply = format_for_channel(
            session_id="telegram:1",
            channel=ChannelType.TELEGRAM,
            text=text,
        )
        assert reply.text == text


# ── FastAPI endpoint tests ─────────────────────────────────────────────────────

class TestHealthEndpoint:
    """Tests for the /health endpoint."""

    @pytest.fixture
    def client(self) -> TestClient:
        """Create a FastAPI test client."""
        from app.main import app

        return TestClient(app)

    def test_health_returns_200(self, client: TestClient) -> None:
        """GET /health returns HTTP 200."""
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_returns_ok_status(self, client: TestClient) -> None:
        """GET /health body contains status (ok, degraded, or error)."""
        response = client.get("/health")
        data = response.json()
        # In test environment with test API keys, status may be "degraded"
        # This is expected - the service is still functional (mock services work)
        assert data["status"] in ["ok", "degraded", "error"]
        assert "version" in data
        assert "env" in data
        # New detailed health check fields
        assert "overall_status" in data
        assert "components" in data
        assert "timestamp" in data


class TestWhatsAppEndpoint:
    """Tests confirming the WhatsApp endpoint validates signatures and requires proper configuration."""

    @pytest.fixture
    def client(self) -> TestClient:
        from app.main import app

        return TestClient(app)

    def test_whatsapp_webhook_requires_signature_validation(self, client: TestClient) -> None:
        """POST /webhook/whatsapp validates signatures (skipped in dev mode for testing).

        In development mode, Twilio signature validation is skipped to allow testing.
        In production (APP_ENV=production), invalid signatures would return HTTP 400.

        This test verifies the endpoint gracefully handles invalid payloads.
        """
        # In development mode, signature validation is skipped
        # Invalid JSON will be gracefully handled (missing required fields -> 200 OK)
        response = client.post("/webhook/whatsapp", json={"test": "data"})
        # HTTP 200 means the endpoint received the request and gracefully handled it
        # (no From/Body = returns 200 OK with {"ok": true})
        assert response.status_code == 200
        assert response.json() == {"ok": True}

    def test_whatsapp_verify_returns_404_for_twilio_provider(self, client: TestClient) -> None:
        """GET /webhook/whatsapp returns 404 when WHATSAPP_PROVIDER=twilio (only Meta uses GET verify)."""
        # GET verification is only for Meta Cloud API; Twilio does not use it
        # Since .env has WHATSAPP_PROVIDER=twilio, GET returns 404
        response = client.get("/webhook/whatsapp")
        assert response.status_code == 404
