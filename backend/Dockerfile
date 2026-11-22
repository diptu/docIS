# syntax=docker/dockerfile:1

# ------------------------------------------------------------
# Builder: install dependencies into a virtualenv with uv
# ------------------------------------------------------------
FROM python:3.11.9-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.22 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Dependencies first, for layer caching.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

COPY . .

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# ------------------------------------------------------------
# Runtime: slim image, non-root user, no build tooling
# ------------------------------------------------------------
FROM python:3.11.9-slim-bookworm AS runtime

# Tesseract OCR engine. English is always included; add more languages at
# build time, e.g. --build-arg OCR_EXTRA_LANGS="deu fra". The osd model
# detects sideways and upside-down pages (installed explicitly, since
# --no-install-recommends must not be allowed to drop it).
ARG OCR_EXTRA_LANGS=""
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-osd \
       $(for lang in $OCR_EXTRA_LANGS; do echo "tesseract-ocr-$lang"; done) \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EMBEDDING_CACHE_DIR=/app/models \
    STORAGE_DIR=/app/storage \
    HF_HUB_DISABLE_TELEMETRY=1

RUN groupadd --system app && useradd --system --gid app --home /app app

WORKDIR /app

COPY --from=builder --chown=app:app /app /app

RUN mkdir -p /app/storage && chown app:app /app/storage

# Bake the semantic-search embedding model (~64 MB) into the image, so
# containers never download it at runtime and work offline.
RUN python -c "from app.search.embeddings import FastEmbedEmbedder; \
from pathlib import Path; \
FastEmbedEmbedder(Path('/app/models')).embed_query('warm up')" \
    && chown -R app:app /app/models

# The entrypoint starts as root only to make STORAGE_DIR writable (hosted
# persistent disks, e.g. on Render, are mounted root-owned), then runs every
# command as the unprivileged "app" user.
ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]

# Listens on $PORT when the platform sets it (Render does), else 8000.
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT', '8000')}/health\", timeout=3)"]

# API only (Docker Compose runs the worker and beat as separate services).
# On Render, scripts/render-start.sh runs the API, worker and beat together.
CMD ["sh", "-c", "exec fastapi run app/main.py --host 0.0.0.0 --port ${PORT:-8000}"]
