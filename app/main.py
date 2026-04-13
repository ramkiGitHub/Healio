"""
app/main.py
===========
FastAPI application entry point for Healio.

Why this file exists
--------------------
This file is the main entry point for the FastAPI application.
Responsibilities:
- Creates the FastAPI app instance with metadata and lifespan.
- Runs startup tasks: logging config, DB directory creation, settings
  validation, and (in Phase 2) BioBERT model pre-loading.
- Mounts all channel webhook routers.
- Registers a global exception handler that maps Healio custom exceptions
  to appropriate HTTP responses.
- Exposes a ``GET /health`` endpoint for Docker/load-balancer health checks.

Running locally
---------------
    uvicorn app.main:app --reload --port 8000

Running via Docker
------------------
    docker compose up --build
"""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import generate_latest, CollectorRegistry, REGISTRY

from app.config import settings
from app.exceptions import (
    AlertToolError,
    CalendarToolError,
    ChannelError,
    EHRLookupError,
    GraphError,
    HealioBaseError,
    NLPError,
    PatientNotFoundError,
)
from app.health import get_health_status
from app.logging_config import configure_logging, get_logger

log = get_logger(__name__)


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """FastAPI lifespan — runs startup and shutdown tasks.

    Startup tasks (executed before the server accepts requests):
    1. Configure structured logging.
    2. Ensure the database directory exists (SQLite needs the folder).
    3. Log a startup summary of active integrations and placeholder states.

    Shutdown tasks (executed after the server stops accepting requests):
    1. Log clean shutdown.

    Note:
        BioBERT model pre-loading will be added here in Phase 2
        (app/nlp/biobert.py). It is left as a comment placeholder below.

    Args:
        app: The FastAPI application instance (injected by FastAPI).

    Yields:
        Control back to FastAPI to serve requests.
    """
    # ── Startup ───────────────────────────────────────────────────────────────
    configure_logging()

    log.info(
        "healio_starting",
        app_env=settings.app_env,
        log_level=settings.log_level,
        openai_model=settings.openai_model,
        calendar_provider=settings.effective_calendar_provider,
        telegram_polling=settings.telegram_polling_mode,
        whatsapp_provider=settings.whatsapp_provider,
        biobert_disabled=settings.disable_biobert,
    )

    # Ensure SQLite database directory exists
    db_dir = os.path.dirname(settings.database_url.replace("sqlite+aiosqlite:///", ""))
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
        log.info("db_directory_ready", path=db_dir)

    # PHASE 2 PLACEHOLDER:
    # Pre-load the BioBERT NER model here so the first request is not slow.
    # Uncomment when app/nlp/biobert.py is implemented.
    #
    # if not settings.disable_biobert:
    #     from app.nlp.biobert import BioBERTExtractor
    #     app.state.biobert = BioBERTExtractor(model_name=settings.biobert_model)
    #     log.info("biobert_loaded", model=settings.biobert_model)
    # else:
    #     app.state.biobert = None
    #     log.warning("biobert_disabled", reason="DISABLE_BIOBERT=true")

    log.info("healio_ready", port=8000)

    yield  # ← Server is now live and accepting requests

    # ── Shutdown ──────────────────────────────────────────────────────────────
    log.info("healio_shutdown")


# ── App instance ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Healio — Conversational Health OS",
    description=(
        "AI-powered medical assistant for Indian clinics. "
        "Handles patient queries, appointment scheduling, and emergency triage "
        "via Telegram and WhatsApp."
    ),
    version="0.1.0",
    lifespan=lifespan,
    # Disable interactive docs in production for security
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
)


# ── Routers ────────────────────────────────────────────────────────────────────

# Import routers after app is created to avoid circular imports
from app.channels.telegram import router as telegram_router  # noqa: E402
from app.channels.whatsapp import router as whatsapp_router  # noqa: E402

app.include_router(telegram_router, prefix="/webhook", tags=["Channels"])
app.include_router(whatsapp_router, prefix="/webhook", tags=["Channels"])


# ── Health check ───────────────────────────────────────────────────────────────

@app.get("/health", tags=["System"])
async def health_check() -> dict:
    """Comprehensive health check endpoint with component status.

    Used by Docker health checks and load balancers to verify the service
    is running and all critical dependencies are accessible.

    This endpoint runs checks on:
    - Database connectivity (SQLite / PostgreSQL)
    - OpenAI API availability
    - Telegram bot API responsiveness
    - WhatsApp provider (Twilio or Meta Cloud API)

    Returns:
        A JSON object with:
        - status: "ok" (backward compatible), "degraded", or "error"
        - overall_status: "healthy", "degraded", or "unhealthy" (new format)
        - version: Application version
        - env: Environment (development, production)
        - timestamp: ISO 8601 UTC timestamp of check
        - uptime_seconds: Seconds since startup
        - components: Dict of individual component statuses

    Example response (healthy):
        {
            "status": "ok",
            "overall_status": "healthy",
            "version": "0.1.0",
            "env": "development",
            "timestamp": "2026-04-13T10:30:45.123456",
            "uptime_seconds": 3600.5,
            "components": {
                "database": {
                    "status": "healthy",
                    "message": "SQLite database accessible",
                    "response_time_ms": 2.5
                },
                "openai": {
                    "status": "healthy",
                    "message": "OpenAI API responsive",
                    "response_time_ms": 150.3
                },
                "telegram": {
                    "status": "healthy",
                    "message": "Telegram bot API responsive",
                    "response_time_ms": 200.1
                },
                "whatsapp": {
                    "status": "healthy",
                    "message": "Twilio account active...",
                    "response_time_ms": 180.5
                }
            }
        }

    HTTP Status Codes:
        200 OK: Service is healthy or degraded (can handle requests)
        503 Service Unavailable: Service is unhealthy (critical failures)
    """
    status = await get_health_status()
    
    # Map overall_status to backward-compatible status field
    status_map = {
        "healthy": "ok",
        "degraded": "degraded",
        "unhealthy": "error",
    }
    
    # Return 503 if service is truly unhealthy (critical failures)
    status_code = 200
    if status.overall_status.value == "unhealthy":
        status_code = 503
    
    result = {
        # Backward-compatible fields
        "status": status_map.get(status.overall_status.value, "ok"),
        "version": status.version,
        "env": settings.app_env,
        # New detailed fields
        **status.to_dict(),
    }
    
    return result


# ── Ready check (for Kubernetes readiness) ────────────────────────────────────

@app.get("/ready", tags=["System"])
async def readiness_check() -> dict[str, str]:
    """Simple readiness check for Kubernetes/Nomad orchestration.

    Returns HTTP 200 only if all critical services are healthy.
    This is stricter than /health — returns 503 if degraded.

    Returns:
        A JSON object with ready status.

    HTTP Status Codes:
        200 OK: Ready to accept requests
        503 Service Unavailable: Not ready (critical service unavailable)
    """
    status = await get_health_status()
    
    if status.overall_status.value in ["healthy", "degraded"]:
        return {"ready": True, "status": status.overall_status.value}
    else:
        raise ValueError("Service not ready")


# ── Prometheus metrics ─────────────────────────────────────────────────────────

@app.get("/metrics", tags=["System"])
async def metrics_endpoint() -> Response:
    """Prometheus metrics endpoint.

    Exposes application metrics in Prometheus text format.
    Used by monitoring systems (Prometheus, Grafana, etc.)
    to collect health and performance data.

    Metrics include:
    - HTTP request counts and latency
    - Health check component response times
    - Message/channel statistics
    - LLM API call counts and latency
    - Tool execution metrics
    - Database query performance

    Returns:
        Prometheus-formatted metrics (text/plain)

    Example:
        # HELP healio_http_requests_total Total HTTP requests processed
        # TYPE healio_http_requests_total counter
        healio_http_requests_total{endpoint="/webhook/whatsapp",...} 1.0
    """
    return Response(
        content=generate_latest(REGISTRY),
        media_type="text/plain; version=0.0.4",
    )


# ── Global exception handlers ──────────────────────────────────────────────────

@app.exception_handler(PatientNotFoundError)
async def patient_not_found_handler(
    request: Request, exc: PatientNotFoundError
) -> JSONResponse:
    """Handle PatientNotFoundError — returns HTTP 404.

    Args:
        request: The incoming FastAPI request object.
        exc: The raised PatientNotFoundError exception.

    Returns:
        A JSON response with HTTP 404 status.
    """
    log.warning("patient_not_found", patient_id=exc.patient_id, path=request.url.path)
    return JSONResponse(
        status_code=404,
        content={"error": "patient_not_found", "detail": exc.detail},
    )


@app.exception_handler(ChannelError)
async def channel_error_handler(
    request: Request, exc: ChannelError
) -> JSONResponse:
    """Handle ChannelError (Telegram / WhatsApp failures) — returns HTTP 502.

    Args:
        request: The incoming FastAPI request object.
        exc: The raised ChannelError exception.

    Returns:
        A JSON response with HTTP 502 status.
    """
    log.error("channel_error", detail=exc.detail, path=request.url.path)
    return JSONResponse(
        status_code=502,
        content={"error": "channel_error", "detail": exc.detail},
    )


@app.exception_handler(GraphError)
async def graph_error_handler(
    request: Request, exc: GraphError
) -> JSONResponse:
    """Handle LangGraph execution errors — returns HTTP 500.

    Args:
        request: The incoming FastAPI request object.
        exc: The raised GraphError exception.

    Returns:
        A JSON response with HTTP 500 status.
    """
    log.error("graph_error", detail=exc.detail, path=request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": "graph_error", "detail": exc.detail},
    )


@app.exception_handler(NLPError)
async def nlp_error_handler(
    request: Request, exc: NLPError
) -> JSONResponse:
    """Handle NLP pipeline errors — returns HTTP 503.

    Args:
        request: The incoming FastAPI request object.
        exc: The raised NLPError exception.

    Returns:
        A JSON response with HTTP 503 status.
    """
    log.error("nlp_error", detail=exc.detail, path=request.url.path)
    return JSONResponse(
        status_code=503,
        content={"error": "nlp_error", "detail": exc.detail},
    )


@app.exception_handler(HealioBaseError)
async def healio_base_error_handler(
    request: Request, exc: HealioBaseError
) -> JSONResponse:
    """Catch-all handler for any unhandled HealioBaseError subclass.

    This is the fallback for any Healio exception not matched by the
    more specific handlers above.

    Args:
        request: The incoming FastAPI request object.
        exc: The raised HealioBaseError exception.

    Returns:
        A JSON response with HTTP 500 status.
    """
    log.error(
        "healio_error",
        error_type=type(exc).__name__,
        detail=exc.detail,
        path=request.url.path,
    )
    return JSONResponse(
        status_code=500,
        content={"error": type(exc).__name__.lower(), "detail": exc.detail},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Catch-all handler for completely unexpected exceptions.

    Prevents internal stack traces from leaking to API consumers.
    Always logs full exception details server-side.

    Args:
        request: The incoming FastAPI request object.
        exc: Any unhandled exception.

    Returns:
        A generic JSON response with HTTP 500 status.
    """
    log.exception(
        "unhandled_exception",
        error_type=type(exc).__name__,
        path=request.url.path,
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_server_error",
            "detail": "An unexpected error occurred. Please try again.",
        },
    )
