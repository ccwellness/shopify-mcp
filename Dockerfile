# =============================================================================
# Single image, multi-entrypoint. docker-compose.yml selects which command
# (gunicorn / rq worker / mcp_server) to run via CMD override.
# =============================================================================

# ---------- Stage 1: builder ----------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Install build deps for psycopg + cryptography wheels (most should be wheels
# already, but we keep the toolchain available for source builds).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./

# Install dependencies into an isolated prefix so we can copy them into the
# runtime image without dragging in build tooling.
RUN pip install --prefix=/install ".[dev]" || pip install --prefix=/install "." \
    && rm -rf /root/.cache

# ---------- Stage 2: runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/install/bin:${PATH}"

# Runtime libs only — no compilers in the final image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r app && useradd -r -g app -d /app -s /sbin/nologin app

WORKDIR /app

COPY --from=builder /install /install
COPY --chown=app:app . /app

USER app

# Default to the Flask app on 5000. docker-compose overrides for worker / mcp.
EXPOSE 5000
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "app:create_app()"]

# Healthcheck: hits a /healthz endpoint that Phase 1+ will provide.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl --fail --silent http://127.0.0.1:5000/healthz || exit 1
