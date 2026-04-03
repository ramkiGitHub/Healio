"""
app/config.py
=============
Application-wide settings loaded from environment variables.

Why this file exists
--------------------
All configuration is centralised here using ``pydantic-settings``.
This means:
- Missing required variables cause an immediate, clear startup failure.
- All values are type-validated before the application starts.
- Values are accessible app-wide via the singleton ``settings`` object.
- No ``os.getenv()`` calls are scattered across the codebase.

Usage
-----
    from app.config import settings

    print(settings.openai_api_key)
    print(settings.telegram_bot_token)

How to extend
-------------
Add a new field with the correct type annotation. If the variable is
required, do NOT provide a default. If it is optional (e.g., a placeholder),
use ``str | None = None`` as the type + default.
"""

import logging
from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.constants import CalendarProvider, WhatsAppProvider

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Healio application settings.

    All values are read from environment variables (or a ``.env`` file).
    Pydantic validates every field at startup — the app will not start
    if a required variable is missing or has the wrong type.

    Environment variables are matched **case-insensitively**, so
    ``OPENAI_API_KEY`` in your ``.env`` maps to ``openai_api_key`` here.

    Attributes:
        app_env: Runtime environment. Controls debug features and log format.
        log_level: Python logging level string (e.g. "INFO", "DEBUG").
        openai_api_key: OpenAI secret key. Required.
        openai_model: GPT model identifier. Defaults to gpt-4o-mini.
        openai_max_tokens: Max tokens per LLM response.
        openai_temperature: LLM sampling temperature (0.0–1.0).
        langchain_tracing_v2: Enables LangSmith trace logging.
        langchain_api_key: LangSmith API key.
        langchain_project: LangSmith project name.
        telegram_bot_token: Telegram Bot API token. Required.
        doctor_chat_id: Telegram chat ID for HITL emergency alerts. Required.
        alert_timeout_seconds: Seconds to wait for doctor ack before escalating.
        telegram_webhook_url: Public URL for Telegram webhook delivery.
                              If blank, the bot uses long-polling (dev mode).
        whatsapp_provider: Active WhatsApp provider ('twilio' or 'meta').
                           None means WhatsApp is disabled (MVP default).
        whatsapp_account_sid: Twilio Account SID (Twilio provider only).
        whatsapp_auth_token: Twilio Auth Token (Twilio provider only).
        whatsapp_from_number: Twilio WhatsApp sender number.
        whatsapp_access_token: Meta Cloud API access token (Meta provider only).
        whatsapp_phone_number_id: Meta Cloud API phone number ID.
        whatsapp_verify_token: Meta webhook verification token.
        calendar_provider: Active calendar backend ('mock' or 'google').
        google_calendar_credentials_path: Path to Google service account JSON.
        google_calendar_id: Google Calendar ID to book appointments on.
        database_url: SQLAlchemy-compatible database connection string.
        biobert_model: HuggingFace model name/path for medical NER.
        disable_biobert: If True, skips BioBERT loading (faster dev startup).
    """

    model_config = SettingsConfigDict(
        # Read variables from .env file if present
        env_file=".env",
        env_file_encoding="utf-8",
        # Ignore extra variables in .env that are not declared here
        extra="ignore",
        # Case-insensitive matching of env var names
        case_sensitive=False,
    )

    # ── Application ────────────────────────────────────────────────────────────
    app_env: str = Field(default="development", description="Runtime environment")
    log_level: str = Field(default="INFO", description="Python logging level")

    # ── OpenAI ─────────────────────────────────────────────────────────────────
    openai_api_key: str = Field(description="OpenAI API key. Required.")
    openai_model: str = Field(
        default="gpt-4o-mini",
        description="GPT model identifier",
    )
    openai_max_tokens: int = Field(default=512, ge=64, le=4096)
    openai_temperature: float = Field(default=0.2, ge=0.0, le=1.0)

    # ── LangSmith ──────────────────────────────────────────────────────────────
    langchain_tracing_v2: bool = Field(
        default=True,
        description="Enable LangSmith tracing",
    )
    langchain_api_key: str = Field(description="LangSmith API key. Required.")
    langchain_project: str = Field(default="healio-mvp")

    # ── Telegram ───────────────────────────────────────────────────────────────
    telegram_bot_token: str = Field(description="Telegram Bot token. Required.")
    doctor_chat_id: str = Field(
        description="Telegram chat ID of on-call doctor for HITL alerts. Required."
    )
    alert_timeout_seconds: int = Field(
        default=30,
        ge=10,
        le=300,
        description="Seconds to wait for doctor ack before auto-escalation",
    )
    telegram_webhook_url: str | None = Field(
        default=None,
        description="Public HTTPS URL for Telegram webhook. Blank = polling mode.",
    )

    # ── WhatsApp (PLACEHOLDER) ─────────────────────────────────────────────────
    whatsapp_provider: WhatsAppProvider | None = Field(
        default=None,
        description="Active WhatsApp provider. None = WhatsApp disabled.",
    )
    # Twilio
    whatsapp_account_sid: str | None = None
    whatsapp_auth_token: str | None = None
    whatsapp_from_number: str | None = None
    # Meta Cloud API
    whatsapp_access_token: str | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_verify_token: str | None = None

    # ── Google Calendar (PLACEHOLDER) ──────────────────────────────────────────
    calendar_provider: CalendarProvider = Field(
        default=CalendarProvider.MOCK,
        description="Calendar back-end. 'mock' for MVP, 'google' when ready.",
    )
    google_calendar_credentials_path: str | None = None
    google_calendar_id: str | None = None

    # ── Database ───────────────────────────────────────────────────────────────
    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/db/healio.db",
        description="SQLAlchemy database connection string",
    )

    # ── HuggingFace / BioBERT ──────────────────────────────────────────────────
    biobert_model: str = Field(
        default="d4data/biomedical-ner-all",
        description="HuggingFace model name for medical NER",
    )
    disable_biobert: bool = Field(
        default=False,
        description="Set True to skip BioBERT loading during development",
    )

    # ── Validators ─────────────────────────────────────────────────────────────

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        """Ensure log level is a valid Python logging level.

        Args:
            value: The raw string value from the environment.

        Returns:
            The uppercased log level string.

        Raises:
            ValueError: If the value is not a recognised logging level.
        """
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = value.upper()
        if upper not in valid_levels:
            msg = f"LOG_LEVEL must be one of {valid_levels}, got '{value}'"
            raise ValueError(msg)
        return upper

    @field_validator("app_env")
    @classmethod
    def validate_app_env(cls, value: str) -> str:
        """Ensure app_env is one of the recognised environment names.

        Args:
            value: The raw string value from the environment.

        Returns:
            The lowercased app environment string.

        Raises:
            ValueError: If the value is not 'development' or 'production'.
        """
        lower = value.lower()
        if lower not in {"development", "production"}:
            msg = f"APP_ENV must be 'development' or 'production', got '{value}'"
            raise ValueError(msg)
        return lower

    @model_validator(mode="after")
    def warn_placeholder_configs(self) -> "Settings":
        """Log warnings for placeholder integrations that are not yet configured.

        This validator runs after all fields are set. It does NOT raise errors
        for missing placeholder config — it emits warnings so developers
        are aware of what still needs to be activated.

        Returns:
            The validated Settings instance, unchanged.
        """
        if self.whatsapp_provider is None:
            logger.warning(
                "WhatsApp integration is not configured. "
                "Set WHATSAPP_PROVIDER in .env to activate. "
                "The /webhook/whatsapp endpoint will return 501."
            )

        if self.calendar_provider == CalendarProvider.GOOGLE and not self.google_calendar_credentials_path:
            logger.warning(
                "CALENDAR_PROVIDER=google but GOOGLE_CALENDAR_CREDENTIALS_PATH is not set. "
                "Falling back to mock calendar provider."
            )

        return self

    # ── Derived properties ─────────────────────────────────────────────────────

    @property
    def is_production(self) -> bool:
        """Returns True if the app is running in production mode."""
        return self.app_env == "production"

    @property
    def is_development(self) -> bool:
        """Returns True if the app is running in development mode."""
        return self.app_env == "development"

    @property
    def telegram_polling_mode(self) -> bool:
        """Returns True if Telegram should use long-polling instead of webhooks.

        Polling mode is used in local development when no public HTTPS URL
        is available for Telegram to deliver webhook events.
        """
        return not bool(self.telegram_webhook_url)

    @property
    def effective_calendar_provider(self) -> CalendarProvider:
        """Returns the effective calendar provider, falling back to mock if Google
        credentials are not configured.

        Returns:
            CalendarProvider.MOCK or CalendarProvider.GOOGLE
        """
        if (
            self.calendar_provider == CalendarProvider.GOOGLE
            and not self.google_calendar_credentials_path
        ):
            return CalendarProvider.MOCK
        return self.calendar_provider


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the singleton Settings instance.

    Uses ``lru_cache`` so the ``.env`` file is only parsed once, regardless
    of how many times ``get_settings()`` is called across the application.

    Returns:
        The validated, cached Settings instance.

    Example:
        >>> from app.config import get_settings
        >>> settings = get_settings()
        >>> print(settings.openai_model)
        'gpt-4o-mini'
    """
    return Settings()


# Module-level convenience alias — import this in other modules.
# Usage: ``from app.config import settings``
settings: Settings = get_settings()
