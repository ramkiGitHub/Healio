"""
app/logging_config.py
=====================
Structured logging configuration using ``structlog``.

Why this file exists
--------------------
Plain ``print()`` or unstructured ``logging`` make it hard to search, filter,
and analyse logs in production. ``structlog`` outputs each log line as a JSON
object (in production) or a coloured, readable string (in development), with
consistent fields like ``timestamp``, ``level``, ``event``, and any bound
context (e.g., ``session_id``, ``patient_id``).

Usage
-----
    from app.logging_config import get_logger

    log = get_logger(__name__)
    log.info("message_received", session_id="abc", channel="telegram")

    # Bind context once, use throughout a request lifetime:
    bound_log = log.bind(session_id="abc", patient_id="P001")
    bound_log.info("ehr_lookup_started")
    bound_log.error("ehr_lookup_failed", error="Patient not found")

How to extend
-------------
Add additional processors to the ``structlog.configure()`` call if you need
custom log enrichment (e.g., adding a request_id from an HTTP middleware).
"""

import logging
import sys

import structlog

from app.config import settings


def configure_logging() -> None:
    """Configure structlog and the standard library logging system.

    Call this once at application startup (inside the FastAPI lifespan
    function in app/main.py).

    In **development** mode, logs are rendered as colourised, human-readable
    key-value strings for easy terminal reading.

    In **production** mode, logs are rendered as JSON objects for ingestion
    by log aggregators (e.g., CloudWatch, Datadog, Loki).
    """
    log_level = getattr(logging, settings.log_level, logging.INFO)

    # Configure standard library logging to forward to structlog
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    # Shared processors applied to every log event regardless of environment
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if settings.is_development:
        # Human-friendly colourised output for local development
        renderer = structlog.dev.ConsoleRenderer(colors=True)
    else:
        # JSON output for production log aggregators
        shared_processors.append(structlog.processors.format_exc_info)
        renderer = structlog.processors.JSONRenderer()  # type: ignore[assignment]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Apply the renderer to the stdlib handler
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger bound to the given module name.

    This is the primary way to obtain a logger anywhere in the application.
    The ``name`` parameter should always be ``__name__`` so log lines carry
    the module path for easy filtering.

    Args:
        name: Module name, typically ``__name__``.

    Returns:
        A structlog BoundLogger ready for structured log calls.

    Example:
        >>> log = get_logger(__name__)
        >>> log.info("server_started", port=8000)
    """
    return structlog.get_logger(name)  # type: ignore[return-value]
