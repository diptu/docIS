"""Settings parsing."""

import pytest

from app.core.config import Settings


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        # Render / Heroku style URLs get the asyncpg driver.
        (
            "postgresql://u:p@dpg-x-a:5432/docis",
            "postgresql+asyncpg://u:p@dpg-x-a:5432/docis",
        ),
        ("postgres://u:p@host/db", "postgresql+asyncpg://u:p@host/db"),
        # Explicit drivers and other databases are kept as given.
        ("postgresql+asyncpg://u:p@host/db", "postgresql+asyncpg://u:p@host/db"),
        ("sqlite+aiosqlite://", "sqlite+aiosqlite://"),
    ],
)
def test_database_url_uses_the_asyncpg_driver(given: str, expected: str) -> None:
    """Hosted Postgres URLs work without hand-editing the scheme."""
    assert Settings(database_url=given).database_url == expected


def test_upload_limit_defaults_to_50_mb_per_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Large scanned PDFs (hundreds of pages) fit under the default."""
    monkeypatch.delenv("MAX_UPLOAD_SIZE_MB", raising=False)
    monkeypatch.delenv("MAX_FILES_PER_UPLOAD", raising=False)
    # Ignore a developer's local .env, which may set its own limits.
    settings = Settings(database_url="sqlite+aiosqlite://", _env_file=None)

    assert settings.max_upload_size_mb == 50
    assert settings.max_upload_size_bytes == 50 * 1024 * 1024
    assert settings.max_files_per_upload == 20


def test_upload_limits_come_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "0.5")
    monkeypatch.setenv("MAX_FILES_PER_UPLOAD", "4")

    settings = Settings(database_url="sqlite+aiosqlite://")

    assert settings.max_upload_size_bytes == 512 * 1024
    # Four files at the limit, plus multipart framing for each.
    assert settings.max_upload_request_bytes == 4 * (512 * 1024 + 16 * 1024)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MAX_UPLOAD_SIZE_MB", "0"),
        ("MAX_UPLOAD_SIZE_MB", "-1"),
        ("MAX_FILES_PER_UPLOAD", "0"),
    ],
)
def test_upload_limits_must_be_positive(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=name.lower()):
        Settings(database_url="sqlite+aiosqlite://")
