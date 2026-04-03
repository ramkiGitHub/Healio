"""
tests/conftest.py
=================
Pytest configuration and shared fixtures for the Healio test suite.

This module sets stub environment variables **before** any application
module is imported, which prevents pydantic-settings from raising a
``ValidationError`` for required fields (``openai_api_key``,
``langchain_api_key``, ``telegram_bot_token``, ``doctor_chat_id``) during
the test-collection phase.

The values are intentionally fake so no real API calls are made.
Tests that exercise live integrations should be marked ``@pytest.mark.live``
and kept out of CI.
"""

import os

# ---------------------------------------------------------------------------
# Inject stub secrets before application modules are imported.
# pydantic-settings reads os.environ at import time, so these must be set
# here, at module level, before any ``from app import ...`` statement.
# ---------------------------------------------------------------------------
os.environ.setdefault("OPENAI_API_KEY", "sk-test-00000000000000000000000000000000")
os.environ.setdefault("LANGCHAIN_API_KEY", "ls-test-00000000000000000000000000000000")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "0000000000:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
os.environ.setdefault("DOCTOR_CHAT_ID", "123456789")
# Disable BioBERT so tests don't try to download the HuggingFace model
os.environ.setdefault("DISABLE_BIOBERT", "true")
# Use in-memory SQLite for all graph tests
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
