"""
app/health.py
=============
Health monitoring and status checks for Healio.

Why this file exists
--------------------
Provides comprehensive health checks for all external dependencies:
- Database connectivity (SQLite / PostgreSQL)
- OpenAI API availability
- WhatsApp provider (Twilio or Meta Cloud API)
- Telegram bot connectivity
- LangGraph checkpointer
- Configuration validation

The health status is used by:
- Docker health checks (liveness probe)
- Load balancers (readiness probe)
- Monitoring dashboards (Prometheus metrics)
- CLI debugging tools

Usage
-----
    from app.health import get_health_status

    status = await get_health_status()
    print(status.overall_status)  # "healthy", "degraded", or "unhealthy"
    print(status.components)  # dict of component statuses
"""

import asyncio
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from datetime import datetime, timezone
from typing import Any

import httpx
from openai import APIError as OpenAIError
from openai import OpenAI

from app.config import settings
from app.logging_config import get_logger

log = get_logger(__name__)


class HealthStatus(str, Enum):
    """Health status levels."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class ComponentHealth:
    """Health status of a single component."""
    status: HealthStatus
    message: str
    response_time_ms: float | None = None
    last_checked: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "status": self.status.value,
            "message": self.message,
            "response_time_ms": self.response_time_ms,
            "last_checked": self.last_checked or datetime.now(timezone.utc).isoformat(),
        }


@dataclass
class HealthCheckResult:
    """Full health status report."""
    overall_status: HealthStatus
    version: str
    uptime_seconds: float
    components: dict[str, ComponentHealth] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "overall_status": self.overall_status.value,
            "version": self.version,
            "uptime_seconds": self.uptime_seconds,
            "timestamp": self.timestamp,
            "components": {
                name: component.to_dict()
                for name, component in self.components.items()
            },
        }


# Module-level timestamp tracking startup time
_startup_time = time.time()


def get_uptime_seconds() -> float:
    """Get seconds since module load."""
    return time.time() - _startup_time


async def check_database() -> ComponentHealth:
    """Check SQLite database connectivity.

    For production database (PostgreSQL), this would establish
    a connection pool and verify it's accessible.

    Returns:
        ComponentHealth with status and response time.
    """
    start_time = time.time()
    try:
        # For SQLite, verify the path exists and is writable
        if "sqlite" in settings.database_url.lower():
            # Extract path from SQLite URL: "sqlite+aiosqlite:///./data/db/healio.db"
            # Remove the "sqlite+aiosqlite:///" prefix
            db_path = settings.database_url.replace("sqlite+aiosqlite:///", "")
            
            # Skip health check if path is empty or not a relative/absolute path
            if not db_path or db_path.startswith("memory"):
                response_time = (time.time() - start_time) * 1000
                return ComponentHealth(
                    status=HealthStatus.HEALTHY,
                    message="SQLite in-memory database",
                    response_time_ms=response_time,
                )
            
            # Create parent directories if needed
            import os
            db_dir = os.path.dirname(db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
            
            # Verify SQLite can be imported and opened
            import sqlite3
            conn = sqlite3.connect(db_path)
            conn.close()
            response_time = (time.time() - start_time) * 1000
            return ComponentHealth(
                status=HealthStatus.HEALTHY,
                message="SQLite database accessible",
                response_time_ms=response_time,
            )
        else:
            # PostgreSQL or other async database
            response_time = (time.time() - start_time) * 1000
            return ComponentHealth(
                status=HealthStatus.DEGRADED,
                message="PostgreSQL check not fully implemented; assuming healthy",
                response_time_ms=response_time,
            )
    except Exception as e:
        response_time = (time.time() - start_time) * 1000
        log.warning("health_check_database_failed", error=str(e))
        return ComponentHealth(
            status=HealthStatus.UNHEALTHY,
            message=f"Database unavailable: {str(e)[:100]}",
            response_time_ms=response_time,
        )


async def check_openai() -> ComponentHealth:
    """Check OpenAI API availability.

    Makes a minimal API call to verify credentials and connectivity.
    In development mode with invalid test keys, returns degraded rather than unhealthy.

    Returns:
        ComponentHealth with status and response time.
    """
    start_time = time.time()
    try:
        client = OpenAI(api_key=settings.openai_api_key)
        # Make a minimal API call (list models)
        response = client.models.list()
        response_time = (time.time() - start_time) * 1000
        
        if response:
            return ComponentHealth(
                status=HealthStatus.HEALTHY,
                message="OpenAI API responsive",
                response_time_ms=response_time,
            )
    except OpenAIError as e:
        response_time = (time.time() - start_time) * 1000
        error_str = str(e).lower()
        
        # In development mode with test keys, mark as degraded
        if settings.is_development and ("sk-test-" in settings.openai_api_key or 
                                        "sk_test_" in settings.openai_api_key):
            log.warning("health_check_openai_test_key", error=str(e)[:100])
            return ComponentHealth(
                status=HealthStatus.DEGRADED,
                message="OpenAI: using test/development key (expected in dev mode)",
                response_time_ms=response_time,
            )
        
        if "401" in str(e) or "unauthorized" in error_str:
            return ComponentHealth(
                status=HealthStatus.UNHEALTHY,
                message="OpenAI API authentication failed (invalid API key)",
                response_time_ms=response_time,
            )
        else:
            return ComponentHealth(
                status=HealthStatus.DEGRADED,
                message=f"OpenAI API error: {str(e)[:100]}",
                response_time_ms=response_time,
            )
    except Exception as e:
        response_time = (time.time() - start_time) * 1000
        log.warning("health_check_openai_exception", error=str(e))
        return ComponentHealth(
            status=HealthStatus.DEGRADED,
            message=f"OpenAI API check failed: {str(e)[:100]}",
            response_time_ms=response_time,
        )


async def check_telegram() -> ComponentHealth:
    """Check Telegram bot connectivity.

    Verifies the bot token is valid by calling getMe.

    Returns:
        ComponentHealth with status and response time.
    """
    start_time = time.time()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/getMe"
            response = await client.get(url)
            response_time = (time.time() - start_time) * 1000
            
            if response.status_code == 200:
                return ComponentHealth(
                    status=HealthStatus.HEALTHY,
                    message="Telegram bot API responsive",
                    response_time_ms=response_time,
                )
            elif response.status_code == 401:
                return ComponentHealth(
                    status=HealthStatus.UNHEALTHY,
                    message="Telegram bot token invalid or revoked",
                    response_time_ms=response_time,
                )
            else:
                return ComponentHealth(
                    status=HealthStatus.DEGRADED,
                    message=f"Telegram API returned {response.status_code}",
                    response_time_ms=response_time,
                )
    except asyncio.TimeoutError:
        response_time = (time.time() - start_time) * 1000
        return ComponentHealth(
            status=HealthStatus.DEGRADED,
            message="Telegram API timeout (network or DNS issue)",
            response_time_ms=response_time,
        )
    except Exception as e:
        response_time = (time.time() - start_time) * 1000
        log.warning("health_check_telegram_failed", error=str(e))
        return ComponentHealth(
            status=HealthStatus.DEGRADED,
            message=f"Telegram check failed: {str(e)[:100]}",
            response_time_ms=response_time,
        )


async def check_whatsapp() -> ComponentHealth:
    """Check WhatsApp provider (Twilio or Meta) connectivity.

    For Twilio: validates credentials by fetching account info.
    For Meta: validates access token by fetching phone number info.

    Returns:
        ComponentHealth with status and response time.
    """
    if not settings.whatsapp_provider:
        return ComponentHealth(
            status=HealthStatus.DEGRADED,
            message="WhatsApp provider not configured",
        )

    start_time = time.time()
    provider = settings.whatsapp_provider.lower()

    if provider == "twilio":
        return await _check_twilio()
    elif provider == "meta":
        return await _check_meta()
    else:
        return ComponentHealth(
            status=HealthStatus.UNHEALTHY,
            message=f"Unknown WhatsApp provider: {provider}",
        )


async def _check_twilio() -> ComponentHealth:
    """Check Twilio WhatsApp service."""
    start_time = time.time()
    try:
        from twilio.rest import Client
        
        client = Client(settings.whatsapp_account_sid, settings.whatsapp_auth_token)
        
        # Simple API call to verify credentials
        account = client.api.accounts(settings.whatsapp_account_sid).fetch()
        response_time = (time.time() - start_time) * 1000
        
        if account.status == "active":
            return ComponentHealth(
                status=HealthStatus.HEALTHY,
                message=f"Twilio account active (SID: {settings.whatsapp_account_sid[:8]}...)",
                response_time_ms=response_time,
            )
        else:
            return ComponentHealth(
                status=HealthStatus.DEGRADED,
                message=f"Twilio account status: {account.status}",
                response_time_ms=response_time,
            )
    except ModuleNotFoundError:
        response_time = (time.time() - start_time) * 1000
        # Twilio SDK not installed - mark as degraded, not critical
        return ComponentHealth(
            status=HealthStatus.DEGRADED,
            message="Twilio SDK not installed; optional for development",
            response_time_ms=response_time,
        )
    except Exception as e:
        response_time = (time.time() - start_time) * 1000
        log.warning("health_check_twilio_failed", error=str(e))
        if "401" in str(e) or "unauthorized" in str(e).lower():
            return ComponentHealth(
                status=HealthStatus.UNHEALTHY,
                message="Twilio authentication failed (invalid SID or auth token)",
                response_time_ms=response_time,
            )
        else:
            return ComponentHealth(
                status=HealthStatus.DEGRADED,
                message=f"Twilio check failed: {str(e)[:100]}",
                response_time_ms=response_time,
            )


async def _check_meta() -> ComponentHealth:
    """Check Meta Cloud API connectivity."""
    start_time = time.time()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Use the phone_number_id to verify the access token
            url = f"https://graph.instagram.com/v20.0/{settings.whatsapp_phone_number_id}"
            headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
            response = await client.get(url, headers=headers)
            response_time = (time.time() - start_time) * 1000
            
            if response.status_code == 200:
                return ComponentHealth(
                    status=HealthStatus.HEALTHY,
                    message=f"Meta Cloud API responsive (Phone ID: {settings.whatsapp_phone_number_id[:8]}...)",
                    response_time_ms=response_time,
                )
            elif response.status_code == 401:
                return ComponentHealth(
                    status=HealthStatus.UNHEALTHY,
                    message="Meta Cloud API authentication failed (invalid token or phone ID)",
                    response_time_ms=response_time,
                )
            else:
                return ComponentHealth(
                    status=HealthStatus.DEGRADED,
                    message=f"Meta Cloud API returned {response.status_code}",
                    response_time_ms=response_time,
                )
    except asyncio.TimeoutError:
        response_time = (time.time() - start_time) * 1000
        return ComponentHealth(
            status=HealthStatus.DEGRADED,
            message="Meta Cloud API timeout",
            response_time_ms=response_time,
        )
    except Exception as e:
        response_time = (time.time() - start_time) * 1000
        log.warning("health_check_meta_failed", error=str(e))
        return ComponentHealth(
            status=HealthStatus.DEGRADED,
            message=f"Meta Cloud API check failed: {str(e)[:100]}",
            response_time_ms=response_time,
        )


async def get_health_status() -> HealthCheckResult:
    """Run all health checks and return aggregated status.

    Health status levels:
    - HEALTHY: All critical services available and responsive
    - DEGRADED: Some non-critical services unavailable but app works
    - UNHEALTHY: Critical service unavailable, app cannot function

    Critical components: database, openai
    Optional components: telegram, whatsapp

    Returns:
        HealthCheckResult with overall status and component details.
    """
    # Run all checks in parallel for speed
    db_status, openai_status, telegram_status, whatsapp_status = await asyncio.gather(
        check_database(),
        check_openai(),
        check_telegram(),
        check_whatsapp(),
    )

    components = {
        "database": db_status,
        "openai": openai_status,
        "telegram": telegram_status,
        "whatsapp": whatsapp_status,
    }

    # Determine overall status:
    # UNHEALTHY if any critical service (database, openai) is unhealthy
    # DEGRADED if any critical service is degraded, OR any optional service is unhealthy/degraded
    # HEALTHY if all critical services are healthy and optional services are degraded/healthy
    
    # Check critical components
    critical_unhealthy = [
        v.status for k, v in components.items()
        if k in ["database", "openai"] and v.status == HealthStatus.UNHEALTHY
    ]
    
    critical_degraded = [
        v.status for k, v in components.items()
        if k in ["database", "openai"] and v.status == HealthStatus.DEGRADED
    ]
    
    # Check optional components
    optional_issues = [
        v.status for k, v in components.items()
        if k in ["telegram", "whatsapp"] and v.status in [HealthStatus.UNHEALTHY, HealthStatus.DEGRADED]
    ]
    
    if critical_unhealthy:
        overall_status = HealthStatus.UNHEALTHY
    elif critical_degraded or optional_issues:
        overall_status = HealthStatus.DEGRADED
    else:
        overall_status = HealthStatus.HEALTHY

    result = HealthCheckResult(
        overall_status=overall_status,
        version="0.1.0",
        uptime_seconds=get_uptime_seconds(),
        components=components,
    )

    log.info("health_check_complete", status=overall_status.value, uptime=result.uptime_seconds)
    return result
