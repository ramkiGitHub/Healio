"""
Health Monitoring for Healio
=============================

This document describes the health monitoring and metrics endpoints available
in Healio, which enable observability, monitoring, and orchestration integration.

Overview
--------

Healio exposes three observability endpoints:

1. **/health** — Comprehensive health check with component status
2. **/ready** — Kubernetes/orchestration readiness probe
3. **/metrics** — Prometheus metrics for monitoring systems

These endpoints allow:
- Docker/Kubernetes liveness and readiness probes
- Load balancer health checks
- Monitoring systems (Prometheus, Grafana, Datadog) to track service health
- Incident alerting when dependencies fail

Architecture
------------

The health monitoring system checks four critical components:

### Critical Dependencies (service cannot function without these)
- **Database** — SQLite or PostgreSQL connectivity check
- **OpenAI API** — Verifies API key and availability (models.list() call)

### Optional Dependencies (service can run in degraded mode)
- **Telegram** — Bot API connectivity (getMe endpoint check)
- **WhatsApp** — Provider (Twilio or Meta) connectivity

If any critical dependency fails, the service is marked `unhealthy` (HTTP 503).
If only optional dependencies fail, the service is marked `degraded` (HTTP 200).

Health Check Endpoints
======================

1. GET /health — Comprehensive Status
---------------------------

### Request
```bash
curl http://localhost:8000/health
```

### Response (Healthy)
HTTP 200 OK
```json
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
      "response_time_ms": 2.5,
      "last_checked": "2026-04-13T10:30:45.123456"
    },
    "openai": {
      "status": "healthy",
      "message": "OpenAI API responsive",
      "response_time_ms": 150.3,
      "last_checked": "2026-04-13T10:30:45.123456"
    },
    "telegram": {
      "status": "healthy",
      "message": "Telegram bot API responsive",
      "response_time_ms": 200.1,
      "last_checked": "2026-04-13T10:30:45.123456"
    },
    "whatsapp": {
      "status": "healthy",
      "message": "Twilio account active (SID: ACb2fe54...)",
      "response_time_ms": 180.5,
      "last_checked": "2026-04-13T10:30:45.123456"
    }
  }
}
```

### Response (Degraded - Test Environment)
HTTP 200 OK
```json
{
  "status": "degraded",
  "overall_status": "degraded",
  "version": "0.1.0",
  "env": "development",
  "components": {
    "database": {
      "status": "healthy",
      ...
    },
    "openai": {
      "status": "degraded",
      "message": "OpenAI: using test/development key (expected in dev mode)",
      "response_time_ms": 65.2
    },
    "telegram": {
      "status": "healthy",
      ...
    },
    "whatsapp": {
      "status": "degraded",
      "message": "Twilio SDK not installed; optional for development",
      "response_time_ms": 1.5
    }
  }
}
```

### Response (Unhealthy)
HTTP 503 Service Unavailable
```json
{
  "status": "error",
  "overall_status": "unhealthy",
  "version": "0.1.0",
  "env": "production",
  "components": {
    "database": {
      "status": "unhealthy",
      "message": "Database unavailable: [Errno 2] No such file or directory",
      "response_time_ms": 5.2
    },
    "openai": {
      "status": "healthy",
      ...
    }
  }
}
```

### Field Descriptions

- **status**: Backward-compatible status field
  - `"ok"` → All checks passed (overall_status = healthy)
  - `"degraded"` → Some non-critical service unavailable
  - `"error"` → Critical service failure (overall_status = unhealthy)

- **overall_status**: New detailed status
  - `"healthy"` — All critical services up
  - `"degraded"` — Optional services down or missing dependencies
  - `"unhealthy"` — Critical service failure

- **version**: Application semantic version

- **env**: Environment (development, production)

- **timestamp**: ISO 8601 UTC timestamp of when check was performed

- **uptime_seconds**: Seconds since application started

- **components**: Dict of component-level checks with:
  - `status`: Component health (healthy, degraded, unhealthy)
  - `message`: Human-readable status message
  - `response_time_ms`: Milliseconds to check this component
  - `last_checked`: ISO 8601 timestamp of check


2. GET /ready — Readiness Probe
------------------

### Request
```bash
curl http://localhost:8000/ready
```

### Response (Ready)
HTTP 200 OK
```json
{
  "ready": true,
  "status": "healthy"
}
```

### Response (Not Ready)
HTTP 503 Service Unavailable
```json
{
  "ready": false,
  "detail": "Service not ready"
}
```

### Usage with Kubernetes

In your Kubernetes deployment manifest:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: healio
spec:
  template:
    spec:
      containers:
      - name: healio
        readinessProbe:
          httpGet:
            path: /ready
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 10
          timeoutSeconds: 5
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 15
          periodSeconds: 30
```


3. GET /metrics — Prometheus Metrics
---------------------

### Request
```bash
curl http://localhost:8000/metrics
```

### Response
```
# HELP healio_http_requests_total Total HTTP requests processed
# TYPE healio_http_requests_total counter
healio_http_requests_total{method="POST",endpoint="/webhook/whatsapp",status_code="200"} 42.0
healio_http_requests_total{method="GET",endpoint="/health",status_code="200"} 128.0

# HELP healio_http_request_duration_seconds HTTP request duration in seconds
# TYPE healio_http_request_duration_seconds histogram
healio_http_request_duration_seconds_bucket{method="POST",endpoint="/webhook/whatsapp",le="0.01"} 5.0
healio_http_request_duration_seconds_bucket{method="POST",endpoint="/webhook/whatsapp",le="0.05"} 35.0
...

# HELP healio_health_check_duration_seconds Health check component response time (seconds)
# TYPE healio_health_check_duration_seconds histogram
healio_health_check_duration_seconds_bucket{component="database",le="0.001"} 18.0
healio_health_check_duration_seconds_bucket{component="openai",le="0.1"} 42.0
...

# HELP healio_channel_messages_received_total Total messages received by channel
# TYPE healio_channel_messages_received_total counter
healio_channel_messages_received_total{channel="telegram"} 245.0
healio_channel_messages_received_total{channel="whatsapp"} 128.0

# HELP healio_llm_calls_total Total LLM API calls
# TYPE healio_llm_calls_total counter
healio_llm_calls_total{model="gpt-4o-mini",status="success"} 368.0
healio_llm_calls_total{model="gpt-4o-mini",status="error"} 3.0

# HELP healio_llm_tokens_used_total Total tokens used in LLM calls
# TYPE healio_llm_tokens_used_total counter
healio_llm_tokens_used_total{model="gpt-4o-mini",token_type="prompt"} 125430.0
healio_llm_tokens_used_total{model="gpt-4o-mini",token_type="completion"} 42150.0
...
```

### Available Metrics

#### HTTP Requests
- `healio_http_requests_total` — Counter of requests by method, endpoint, status
- `healio_http_request_duration_seconds` — Histogram of request latency

#### Health Checks
- `healio_health_check_duration_seconds` — Histogram of component check times
- `healio_health_check_status` — Gauge of component status (0=unhealthy, 1=degraded, 2=healthy)

#### Messaging
- `healio_channel_messages_received_total` — Messages received per channel (telegram, whatsapp)
- `healio_channel_messages_sent_total` — Messages sent per channel
- `healio_channel_errors_total` — Errors by channel and error type

#### Conversations
- `healio_conversations_active` — Currently active conversations
- `healio_conversation_turns_total` — Total conversation turns processed
- `healio_conversation_duration_seconds` — Distribution of conversation durations

#### LLM API
- `healio_llm_calls_total` — LLM API calls by model and status
- `healio_llm_tokens_used_total` — Token usage by model and type (prompt/completion)
- `healio_llm_latency_seconds` — LLM API response latency by model

#### Tools
- `healio_tool_calls_total` — Tool calls by tool name and status
- `healio_tool_duration_seconds` — Tool execution time by name

#### Database
- `healio_database_queries_total` — Queries by operation type (select, insert, update, delete)
- `healio_database_query_duration_seconds` — Query latency by operation

### Grafana Dashboard Option

To visualize these metrics in Grafana:

1. Add Prometheus data source pointing to scrape config (see below)
2. Create dashboard with panels:
   ```
   - Health check status by component
   - Request latency distribution
   - Message throughput (requests/min)
   - LLM API response time and token usage
   - Active conversations
   - Error rate by channel
   ```

Monitoring Integration
=======================

### Prometheus Scrape Configuration

In your Prometheus `prometheus.yml`:

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'healio'
    static_configs:
      - targets: ['localhost:8000']
    metrics_path: '/metrics'
    scrape_interval: 30s  # Health checks are expensive; scrape less frequently
```

### Docker Health Check

In your `Dockerfile`:

```dockerfile
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1
```

Or in `docker-compose.yml`:

```yaml
services:
  healio:
    build: .
    ports:
      - "8000:8000"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
```

### AWS ECS Task Definition

```json
{
  "healthCheck": {
    "command": [
      "CMD-SHELL",
      "curl -f http://localhost:8000/health || exit 1"
    ],
    "interval": 30,
    "timeout": 10,
    "retries": 3,
    "startPeriod": 40
  }
}
```

### Alert Examples

#### PagerDuty / Datadog / Alertmanager

Monitor for:
- `healio_health_check_status{component="database"} == 0` → Critical alert (database down)
- `healio_health_check_status{component="openai"} == 0` → Critical alert (OpenAI unavailable)
- `healio_llm_calls_total{status="error"} > 0.1 * healio_llm_calls_total` → Warning (>10% LLM errors)
- `healio_channel_errors_total > 0` → Warning (any channel errors)
- `increase(healio_channel_messages_received_total[1h]) == 0` → Warning (no messages in 1 hour)

Development Mode Behavior
==========================

In development (APP_ENV=development):

- **OpenAI**: Test API keys (sk-test-*) are accepted; marked as "degraded" rather than unhealthy
- **Twilio**: Missing SDK is accepted; marked as "degraded"
- **Database**: Any readable directory is accepted
- **Signature Validation**: Skipped for easier local testing (see app/channels/whatsapp.py)

This allows you to run Healio locally without all credentials configured.

Environment Variables
=====================

Control health checks via `.env`:

```env
# Reduce health check load in high-load scenarios (optional)
# HEALTH_CHECK_TIMEOUT_SECONDS=5

# Skip specific checks during development (optional)
# SKIP_HEALTH_CHECK_TELEGRAM=true
# SKIP_HEALTH_CHECK_OPENAI=true
```

(These are placeholders for future implementation; currently all checks run.)

Troubleshooting
==============

### /health returns "unhealthy" with database error

**Cause**: Database path doesn't exist or is not writable
**Solution**: Ensure `data/db/` directory exists and is writable:
```bash
mkdir -p ./data/db
chmod 777 ./data/db
```

### /health returns "degraded" with OpenAI error

**Cause**: Invalid or expired API key
**Solution**:
- In development: OK, this is expected with test keys
- In production: Update OPENAI_API_KEY in `.env` and restart

### /health returns "degraded" with Telegram error

**Cause**: Invalid bot token or network unreachable
**Solution**:
- Verify TELEGRAM_BOT_TOKEN is correct
- Check network connectivity to api.telegram.org
- Verify firewall allows outbound HTTPS to Telegram

### Metrics endpoint returns 404

**Cause**: prometheus-client not installed
**Solution**: Re-run `uv sync` to install dependencies:
```bash
uv sync
```

### Prometheus scrape fails

**Cause**: Healio not running or port not exposed
**Solution**:
- Verify Healio is running: `curl http://localhost:8000/health`
- Check Prometheus target config points to correct host:port
- Verify firewall allows access to port 8000

Testing Health Endpoints
========================

```bash
# Test health endpoint
curl http://localhost:8000/health | jq .

# Test readiness probe
curl http://localhost:8000/ready | jq .

# Test metrics export
curl http://localhost:8000/metrics | head -20

# Test with Docker
docker run -p 8000:8000 healio:latest &
sleep 5
curl http://localhost:8000/health

# Load test with concurrent requests
ab -n 100 -c 10 http://localhost:8000/health
```

See Also
========

- [app/health.py](../app/health.py) — Health check implementation
- [app/metrics.py](../app/metrics.py) — Prometheus metrics definitions
- [DEPLOYMENT.md](./DEPLOYMENT.md) — Production deployment guide
- Prometheus Docs: https://prometheus.io/docs/
- Kubernetes Health Checks: https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/
"""
