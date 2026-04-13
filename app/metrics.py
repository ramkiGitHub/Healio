"""
app/metrics.py
==============
Prometheus metrics collection for Healio monitoring.

Why this file exists
--------------------
Provides instrumentation for observability:
- Request latency and count by endpoint
- Health check component response times
- Conversation/message processing metrics
- API call counts and errors

Usage
-----
    from app.metrics import (
        http_requests_total,
        http_request_duration_seconds,
        health_check_duration_seconds,
    )

    # Increment request counter
    http_requests_total.labels(method="POST", endpoint="/webhook/whatsapp").inc()

    # Record request duration
    http_request_duration_seconds.labels(endpoint="/webhook/whatsapp").observe(0.25)
"""

from prometheus_client import Counter, Histogram, Gauge

# ── Request metrics ───────────────────────────────────────────────────────────

http_requests_total = Counter(
    "healio_http_requests_total",
    "Total HTTP requests processed",
    labelnames=["method", "endpoint", "status_code"],
)

http_request_duration_seconds = Histogram(
    "healio_http_request_duration_seconds",
    "HTTP request duration in seconds",
    labelnames=["method", "endpoint"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

# ── Health check metrics ──────────────────────────────────────────────────────

health_check_duration_seconds = Histogram(
    "healio_health_check_duration_seconds",
    "Health check component response time (seconds)",
    labelnames=["component"],
    buckets=(0.001, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0),
)

health_check_status = Gauge(
    "healio_health_check_status",
    "Component health status (0=unhealthy, 1=degraded, 2=healthy)",
    labelnames=["component"],
)

# ── Message/Channel metrics ───────────────────────────────────────────────────

channel_messages_received = Counter(
    "healio_channel_messages_received_total",
    "Total messages received by channel",
    labelnames=["channel"],  # telegram, whatsapp
)

channel_messages_sent = Counter(
    "healio_channel_messages_sent_total",
    "Total messages sent by channel",
    labelnames=["channel"],
)

channel_errors = Counter(
    "healio_channel_errors_total",
    "Total channel errors",
    labelnames=["channel", "error_type"],
)

# ── Graph/Conversation metrics ────────────────────────────────────────────────

conversations_active = Gauge(
    "healio_conversations_active",
    "Number of active conversations",
)

conversation_turns = Counter(
    "healio_conversation_turns_total",
    "Total conversation turns processed",
)

conversation_duration_seconds = Histogram(
    "healio_conversation_duration_seconds",
    "Conversation duration in seconds",
    buckets=(1, 5, 10, 30, 60, 300, 600, 1800),
)

# ── LLM metrics ────────────────────────────────────────────────────────────────

llm_calls = Counter(
    "healio_llm_calls_total",
    "Total LLM API calls",
    labelnames=["model", "status"],  # status: success, error
)

llm_tokens_used = Counter(
    "healio_llm_tokens_used_total",
    "Total tokens used in LLM calls",
    labelnames=["model", "token_type"],  # token_type: prompt, completion
)

llm_latency_seconds = Histogram(
    "healio_llm_latency_seconds",
    "LLM API response latency",
    labelnames=["model"],
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0),
)

# ── Tool execution metrics ─────────────────────────────────────────────────────

tool_calls = Counter(
    "healio_tool_calls_total",
    "Total tool function calls",
    labelnames=["tool_name", "status"],  # status: success, error
)

tool_duration_seconds = Histogram(
    "healio_tool_duration_seconds",
    "Tool execution duration",
    labelnames=["tool_name"],
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 5.0),
)

# ── Database metrics ───────────────────────────────────────────────────────────

database_queries = Counter(
    "healio_database_queries_total",
    "Total database queries",
    labelnames=["operation"],  # select, insert, update, delete
)

database_query_duration_seconds = Histogram(
    "healio_database_query_duration_seconds",
    "Database query duration",
    labelnames=["operation"],
    buckets=(0.001, 0.01, 0.05, 0.1, 0.5),
)
