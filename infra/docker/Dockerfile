# syntax=docker/dockerfile:1
# Cloud AI OS — single image for agent-api AND worker (ARCHITECTURE.md §3, §14).
#
# Build context is the REPO ROOT:
#   docker build -f infra/docker/Dockerfile -t cloudos:dev .
#
# Multi-arch (linux/amd64 + linux/arm64 — Oracle A1 is ARM64):
#   docker buildx create --use --name cloudos-builder   # once
#   docker buildx build --platform linux/amd64,linux/arm64 \
#     -f infra/docker/Dockerfile -t <registry>/cloudos:latest --push .
#   (drop --push and use --load for a single local arch)
#
# All Python deps (fastapi, uvicorn[standard], psycopg[binary,pool], pydantic,
# httpx, pyyaml, python-dotenv) ship aarch64 wheels — no compiler toolchain needed,
# so slim base stays slim. See infra/oracle/ARM64_COMPAT.md.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    CLOUDOS_ROOT=/app

# curl only — used by container healthchecks; kept tiny.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user (fixed uid/gid for predictable volume ownership).
RUN groupadd --gid 10001 cloudos \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin cloudos

WORKDIR /app

# Paths are relative to the repo-root build context.
COPY pyproject.toml ./
COPY src/ ./src/
COPY config/ ./config/
COPY db/ ./db/
COPY scripts/ ./scripts/

RUN pip install --no-cache-dir . \
    && rm -rf build/ src/*.egg-info

RUN chown -R cloudos:cloudos /app
USER cloudos

EXPOSE 8080

# Default command = API. The worker service overrides with:
#   python -m cloudos.worker
CMD ["uvicorn", "cloudos.api.app:app", "--host", "0.0.0.0", "--port", "8080"]
