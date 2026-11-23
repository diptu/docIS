"""
Application configuration.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Allowance per file for multipart framing (boundary, part headers).
MULTIPART_OVERHEAD_PER_FILE = 16 * 1024


class Settings(BaseSettings):
    """Centralized application settings."""

    app_name: str = "docIS"
    database_url: str

    storage_dir: Path = Path("storage")
    # Upload limits (MAX_UPLOAD_SIZE_MB, MAX_FILES_PER_UPLOAD). The size is
    # per file, in MiB, and may be fractional (e.g. 0.5).
    max_upload_size_mb: float = Field(default=50, gt=0)
    max_files_per_upload: int = Field(default=20, gt=0)

    # Rate limiting per client IP (app/api/rate_limit.py). The processing
    # limit covers uploads and retries, which start OCR and paid AI calls.
    # Behind proxies, set the number that append to X-Forwarded-For.
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = Field(default=600, gt=0)
    rate_limit_processing_per_minute: int = Field(default=20, gt=0)
    rate_limit_trusted_proxy_hops: int = Field(default=0, ge=0)

    # Tesseract language codes joined with "+", e.g. "eng+deu".
    ocr_languages: str = "eng"
    ocr_dpi: int = Field(default=300, ge=72, le=600)
    ocr_timeout_seconds: int = Field(default=120, gt=0)

    # AI invoice extraction (Claude). Credentials come from ANTHROPIC_API_KEY.
    invoice_extraction_enabled: bool = True
    llm_model: str = "claude-opus-5-5"
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    llm_max_tokens: int = Field(default=32000, gt=0)
    llm_timeout_seconds: float = Field(default=600.0, gt=0)
    # Long documents are extracted in parts of about this many characters
    # (split at page/sheet boundaries) and merged; see app.extraction.chunked.
    # Documents needing more than llm_max_parts parts fail (a cost guard);
    # llm_part_concurrency parts are extracted at the same time.
    llm_chunk_chars: int = Field(default=50_000, ge=1_000)
    llm_max_parts: int = Field(default=120, gt=0)
    llm_part_concurrency: int = Field(default=4, gt=0)
    # Validation and review: rounding allowance for amount checks (currency
    # units), and the OCR confidence below which a page is flagged.
    validation_tolerance: float = Field(default=0.01, ge=0)
    review_min_ocr_confidence: float = Field(default=80.0, ge=0, le=100)

    # Search. Semantic search embeds text locally with bge-small-en-v1.5;
    # the model (~64 MB) is downloaded into ``embedding_cache_dir`` on first
    # use (the Docker image ships it). Keyword search always works.
    semantic_search_enabled: bool = True
    embedding_cache_dir: Path | None = None
    # Semantic hits scoring below this cosine similarity are dropped.
    semantic_min_score: float = Field(default=0.5, ge=-1, le=1)

    # Background processing (Celery)
    celery_broker_url: str = "redis://localhost:6379/0"
    # Hard limit for one processing task; the task is killed beyond it.
    celery_task_time_limit_seconds: int = Field(default=1800, gt=0)
    # Redis redelivers unacknowledged tasks after this; must exceed the task
    # time limit and the longest retry countdown.
    celery_visibility_timeout_seconds: int = Field(default=3600, gt=0)
    # A running job whose heartbeat is older than this is considered dead.
    # Must comfortably exceed the longest single step (one OCR page,
    # bounded by ocr_timeout_seconds).
    job_lease_seconds: int = Field(default=300, gt=0)
    progress_interval_seconds: float = Field(default=1.0, gt=0)
    # How often beat runs recovery, and how long a queued job may wait
    # before recovery assumes its task message was lost.
    job_recovery_interval_seconds: float = Field(default=60.0, gt=0)
    job_redispatch_after_seconds: float = Field(default=600.0, gt=0)
    # Retries: total attempts per job, and exponential backoff between them
    # (base * 2^(attempt-1), capped, with +/-20% jitter).
    job_max_attempts: int = Field(default=3, ge=1)
    job_retry_base_seconds: float = Field(default=30.0, ge=0)
    job_retry_max_seconds: float = Field(default=900.0, ge=0)

    @property
    def max_upload_size_bytes(self) -> int:
        """The per-file limit in bytes."""
        return int(self.max_upload_size_mb * 1024 * 1024)

    @property
    def max_upload_request_bytes(self) -> int:
        """The largest upload request body accepted before it is parsed:
        the maximum number of files, each at the size limit, plus room for
        multipart part headers and boundaries."""
        return self.max_files_per_upload * (
            self.max_upload_size_bytes + MULTIPART_OVERHEAD_PER_FILE
        )

    @field_validator("database_url")
    @classmethod
    def _use_asyncpg(cls, url: str) -> str:
        """Accept the plain URLs hosts hand out (Render, Heroku, ...):
        ``postgres://`` and ``postgresql://`` get the asyncpg driver the app
        needs. URLs that already name a driver are left alone."""
        for scheme in ("postgres://", "postgresql://"):
            if url.startswith(scheme):
                return "postgresql+asyncpg://" + url.removeprefix(scheme)
        return url

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
