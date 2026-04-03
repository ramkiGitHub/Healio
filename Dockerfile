# ─────────────────────────────────────────────────────────────────────────────
# Healio — Multi-stage Dockerfile
#
# Stage 1 (builder): installs all Python dependencies into an isolated venv.
# Stage 2 (runtime): copies only the venv + app code — keeps the image lean
#                    and free of build tools.
#
# Build:  docker build -t healio:latest .
# Run:    docker run --env-file .env -p 8000:8000 healio:latest
#
# Python base: python:3.12-slim  (Debian Bookworm, amd64/arm64)
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: dependency builder ───────────────────────────────────────────────
FROM python:3.12-slim AS builder

# Suppress .pyc files and ensure stdout/stderr are unbuffered during build
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

# Install build tools needed for compiled Python packages (e.g. torch)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy only the files hatchling needs to resolve the package metadata.
# Avoids invalidating the cache when only app source changes.
COPY pyproject.toml README.md ./

# Create an isolated virtual env then install all runtime dependencies.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --upgrade pip --no-cache-dir && \
    pip install --no-cache-dir .

# ── Stage 2: runtime image ────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Runtime Python flags
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Install wget for the HEALTHCHECK (lighter than curl in slim images)
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Security: run as non-root user
RUN addgroup --system healio && adduser --system --ingroup healio healio

WORKDIR /app

# Copy the pre-built virtual env from builder stage
COPY --from=builder /opt/venv /opt/venv

# Copy application source and seed data
COPY app/ ./app/
COPY data/ ./data/

# Pre-create the SQLite DB directory so the non-root user can write to it
RUN mkdir -p /app/data/db

# Transfer ownership to the non-root user
RUN chown -R healio:healio /app

USER healio

# Expose the FastAPI port
EXPOSE 8000

# Health check — wget is available in the runtime stage (installed above)
# --quiet --spider: fetch without saving body; exits non-zero on HTTP error
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD wget -q --spider http://localhost:8000/health || exit 1

# Start the FastAPI server via uvicorn.
# --workers 1: single worker is safe with SQLite.
# Increase to >1 only after migrating to PostgreSQL.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
