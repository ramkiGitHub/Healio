"""
app/channels/normalizer.py
==========================
Channel-agnostic message normalisation layer.

Why this file exists
--------------------
Each messaging channel (Telegram, WhatsApp) has its own payload format.
This module defines a single ``IncomingMessage`` data model that all channel
handlers normalise to before passing to the LangGraph pipeline.

This means the graph never needs to know which channel a message came from —
it always receives the same shape of data. Adding a new channel only requires
adding a new normalise function here, not changes to graph logic.

Similarly, ``format_for_channel`` produces channel-appropriate replies:
Telegram supports Markdown, while plain text is safer for WhatsApp.

Usage
-----
    from app.channels.normalizer import IncomingMessage, normalize_telegram

    message = normalize_telegram(update=telegram_update)
    # message.session_id, message.patient_id, message.text etc. are all set

How to extend
-------------
- Add a ``normalize_whatsapp(payload: dict) -> IncomingMessage`` function when
  the WhatsApp channel is activated.
- Add new fields to ``IncomingMessage`` if a new channel provides data that
  is useful to the graph (e.g., voice message transcripts, location, images).
"""

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.constants import ChannelType


class IncomingMessage(BaseModel):
    """Normalised representation of a patient message from any channel.

    All channel webhook handlers convert their channel-specific payloads
    to this model before invoking the LangGraph pipeline.

    Attributes:
        session_id: Unique ID for this conversation session.
                    Format: ``{channel}:{sender_id}`` (e.g., ``telegram:123456``).
                    This is the key used for LangGraph memory checkpointing.
        patient_id: Identifier for the patient, derived from the channel sender.
                    In Telegram this is the Telegram user ID (as a string).
        channel: The messaging channel this message arrived from.
        text: The raw text content of the patient's message.
        sender_name: Display name of the sender, if provided by the channel.
        received_at: UTC timestamp when the message was received by the webhook.
        raw_payload: The original channel payload for debugging.
                     Not used by graph logic.

    Example:
        >>> msg = IncomingMessage(
        ...     session_id="telegram:123456",
        ...     patient_id="123456",
        ...     channel=ChannelType.TELEGRAM,
        ...     text="I have a headache",
        ... )
    """

    session_id: str = Field(description="Unique session ID: '{channel}:{sender_id}'")
    patient_id: str = Field(description="Patient identifier from channel sender ID")
    channel: ChannelType = Field(description="The channel this message arrived from")
    text: str = Field(description="Raw text content of the patient message")
    sender_name: str | None = Field(
        default=None,
        description="Patient's display name from the channel, if available",
    )
    received_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp when the webhook received this message",
    )
    raw_payload: dict | None = Field(
        default=None,
        description="Original channel payload, stored for debugging only",
        exclude=True,  # Excluded from serialisation to avoid bloating logs
    )

    model_config = {"frozen": True}  # Immutable after creation


class OutgoingMessage(BaseModel):
    """Represents a reply to be sent back to the patient via their channel.

    Produced by ``format_for_channel()`` and consumed by channel-specific
    send functions (e.g., ``send_telegram_message()``).

    Attributes:
        session_id: The session this reply belongs to.
        channel: The channel to send the reply on.
        text: The reply text, formatted for the target channel.
        parse_mode: Channel-specific formatting mode.
                    'Markdown' for Telegram, None for WhatsApp plain text.
    """

    session_id: str
    channel: ChannelType
    text: str
    parse_mode: str | None = None


def normalize_telegram(
    sender_id: int,
    text: str,
    sender_first_name: str | None = None,
    sender_last_name: str | None = None,
    raw_payload: dict | None = None,
) -> IncomingMessage:
    """Normalise a Telegram message into an IncomingMessage DTO.

    Args:
        sender_id: Telegram user ID of the message sender.
        text: The message text content.
        sender_first_name: Sender's first name from Telegram, if available.
        sender_last_name: Sender's last name from Telegram, if available.
        raw_payload: The full raw Telegram Update dict, stored for debugging.

    Returns:
        An ``IncomingMessage`` with session_id and patient_id derived from
        the Telegram user ID.

    Example:
        >>> msg = normalize_telegram(
        ...     sender_id=123456,
        ...     text="I have a headache",
        ...     sender_first_name="Ravi",
        ... )
        >>> msg.session_id
        'telegram:123456'
        >>> msg.patient_id
        '123456'
    """
    str_sender_id = str(sender_id)
    name_parts = filter(None, [sender_first_name, sender_last_name])
    sender_name = " ".join(name_parts) or None

    return IncomingMessage(
        session_id=f"{ChannelType.TELEGRAM}:{str_sender_id}",
        patient_id=str_sender_id,
        channel=ChannelType.TELEGRAM,
        text=text.strip(),
        sender_name=sender_name,
        raw_payload=raw_payload,
    )


def normalize_whatsapp(
    sender_phone: str,
    text: str,
    sender_name: str | None = None,
    raw_payload: dict | None = None,
) -> IncomingMessage:
    """Normalise a WhatsApp message into an IncomingMessage DTO.

    NOTE: This function is a PLACEHOLDER for the WhatsApp integration.
    It will be fully implemented when the WhatsApp channel is activated
    (see .env.example for configuration steps).

    Args:
        sender_phone: The sender's phone number (E.164 format, e.g., +919876543210).
        text: The message text content.
        sender_name: Display name from the WhatsApp profile, if available.
        raw_payload: The full raw WhatsApp webhook payload dict.

    Returns:
        An ``IncomingMessage`` with session_id and patient_id derived from
        the sender's phone number.

    Example:
        >>> msg = normalize_whatsapp(
        ...     sender_phone="+919876543210",
        ...     text="Book appointment",
        ...     sender_name="Kavya",
        ... )
        >>> msg.session_id
        'whatsapp:+919876543210'
    """
    return IncomingMessage(
        session_id=f"{ChannelType.WHATSAPP}:{sender_phone}",
        patient_id=sender_phone,
        channel=ChannelType.WHATSAPP,
        text=text.strip(),
        sender_name=sender_name,
        raw_payload=raw_payload,
    )


def format_for_channel(session_id: str, channel: ChannelType, text: str) -> OutgoingMessage:
    """Format a plain-text reply appropriately for the target channel.

    Different channels have different formatting capabilities:
    - Telegram supports Markdown (bold, italic, code blocks).
    - WhatsApp supports only plain text in the MVP.

    As new channels are added, extend this function with their formatting rules.

    Args:
        session_id: The session this reply belongs to.
        channel: The target messaging channel.
        text: The plain-text reply from the LangGraph pipeline.

    Returns:
        An ``OutgoingMessage`` with channel-appropriate formatting applied.

    Example:
        >>> reply = format_for_channel(
        ...     session_id="telegram:123",
        ...     channel=ChannelType.TELEGRAM,
        ...     text="Your appointment is confirmed.",
        ... )
        >>> reply.parse_mode
        'Markdown'
    """
    if channel == ChannelType.TELEGRAM:
        return OutgoingMessage(
            session_id=session_id,
            channel=channel,
            text=text,
            parse_mode="Markdown",
        )

    # WhatsApp and any future channels: plain text, no special formatting
    return OutgoingMessage(
        session_id=session_id,
        channel=channel,
        text=text,
        parse_mode=None,
    )
