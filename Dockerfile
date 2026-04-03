# ─────────────────────────────────────────────────────────────────────────────
# Healio — Multi-stage Dockerfile
#
# Stage 1 (builder): installs all Python dependencies into a virtual env.
# Stage 2 (runtime): copies only the venv and app code — keeps image lean.
#
# Build:  docker build -t healio:latest .
# Run:    docker run --env-file .env -p 8000:8000 healio:latest
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: dependency builder ───────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build tools needed for some Python packages (e.g. torch, transformers)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency manifest first (maximises Docker layer cache)
COPY pyproject.toml .

# Create a virtual env and install all runtime dependencies
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --upgrade pip && \
    pip install --no-cache-dir .

# ── Stage 2: runtime image ────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Security: run as non-root user
RUN addgroup --system healio && adduser --system --ingroup healio healio

WORKDIR /app

# Copy the pre-built virtual env from builder stage
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application source
COPY app/ ./app/
COPY data/ ./data/

# Ensure the non-root user owns the working directory
RUN chown -R healio:healio /app

USER healio

# Expose the FastAPI port
EXPOSE 8000

# Health check — calls the /health endpoint every 30s
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Start the FastAPI server via uvicorn
# --workers 1: single worker safe for SQLite (increase post-migration to PostgreSQL)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
