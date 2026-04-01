# Single-image runtime for StreamDocs (frontend + backend + worker)
# Note: Postgres and Redis are expected to run separately (e.g. via docker compose).

# --- Stage 1: Build frontend ---
FROM oven/bun:1 AS frontend-build

WORKDIR /app

COPY package.json bun.lock /app/
COPY frontend/package.json /app/frontend/

WORKDIR /app/frontend
RUN bun install

COPY frontend /app/frontend

# In the single-container setup we proxy /api/* to the backend via nginx,
# so the frontend can talk to the API from the same origin.
ARG VITE_API_URL=/api/v1
ENV VITE_API_URL=${VITE_API_URL}

RUN bun run build


# --- Stage 2: Runtime ---
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    CELERY_WORKER_CONCURRENCY=2

WORKDIR /app

# System deps: nginx (serve frontend), supervisor (run multiple processes), curl (healthchecks/debug)
RUN apt-get update \
  && apt-get install -y --no-install-recommends nginx supervisor curl ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# Install uv (same approach as backend/Dockerfile)
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Install Python deps (workspace) using uv.lock + pyproject.toml from repo root
COPY uv.lock pyproject.toml /app/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-workspace --package app

# Copy backend code
COPY backend/scripts /app/backend/scripts
COPY backend/pyproject.toml backend/alembic.ini /app/backend/
COPY backend/app /app/backend/app

# Sync the full project (includes backend package)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package app

# Frontend artifacts + nginx config
COPY --from=frontend-build /app/frontend/dist/ /usr/share/nginx/html/
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf

# Supervisor + entrypoint
COPY docker/supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY docker/entrypoint.sh /entrypoint.sh

# Data directory for uploads (can be mounted as a volume)
RUN mkdir -p /app/backend/data/uploads \
  && chmod +x /entrypoint.sh

EXPOSE 80

# Optional: also expose backend port (not needed if you go through nginx)
EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
