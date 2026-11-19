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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
