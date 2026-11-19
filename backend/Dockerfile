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
# build time, e.g. --build-arg OCR_EXTRA_LANGS="deu fra".
ARG OCR_EXTRA_LANGS=""
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
       $(for lang in $OCR_EXTRA_LANGS; do echo "tesseract-ocr-$lang"; done) \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EMBEDDING_CACHE_DIR=/app/models \
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

USER app

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]

CMD ["fastapi", "run", "app/main.py", "--host", "0.0.0.0", "--port", "8000"]
