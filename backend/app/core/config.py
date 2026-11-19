"""
Application configuration.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralized application settings."""

    app_name: str = "docIS"
    database_url: str

    storage_dir: Path = Path("storage")
    max_upload_size_bytes: int = Field(default=25 * 1024 * 1024, gt=0)
    max_files_per_upload: int = Field(default=20, gt=0)

    # Tesseract language codes joined with "+", e.g. "eng+deu".
    ocr_languages: str = "eng"
    ocr_dpi: int = Field(default=300, ge=72, le=600)
    ocr_timeout_seconds: int = Field(default=120, gt=0)

    # Background worker
    worker_poll_interval_seconds: float = Field(default=1.0, gt=0)
    # A running job whose heartbeat is older than this is considered dead
    # and requeued. Must comfortably exceed the longest single step
    # (e.g. one OCR page, bounded by ocr_timeout_seconds).
    job_lease_seconds: int = Field(default=300, gt=0)
    progress_interval_seconds: float = Field(default=1.0, gt=0)
    # Retries: total attempts per job, and exponential backoff between them
    # (base * 2^(attempt-1), capped, with +/-20% jitter).
    job_max_attempts: int = Field(default=3, ge=1)
    job_retry_base_seconds: float = Field(default=30.0, ge=0)
    job_retry_max_seconds: float = Field(default=900.0, ge=0)
    # File touched on every loop; used by the container health check.
    worker_heartbeat_file: Path | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
