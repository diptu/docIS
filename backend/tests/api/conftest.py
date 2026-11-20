"""API layer: the HTTP contract of every route, with no real database.

Requests go through ``httpx.AsyncClient`` over ``ASGITransport`` (in the
test's event loop, no server). Collaborators are replaced through FastAPI
dependency overrides:

* ``repository`` / ``service``: autospecced doubles of DocumentRepository /
  DocumentService. Autospec keeps them honest: calling a method that does
  not exist, or with the wrong arguments, fails the test.
* ``get_db`` is overridden to fail loudly, so an API test can never reach a
  real database by accident; tests that need a session supply their own.

``app.dependency_overrides`` is cleared after every test.
"""

from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import create_autospec

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import get_document_repository, get_document_service
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.main import app
from app.repositories.document import DocumentRepository
from app.services.document import DocumentService

Overrides = dict[Callable[..., Any], Callable[..., Any]]


async def _no_database() -> AsyncIterator[None]:
    raise AssertionError(
        "API tests must not use a real database session; override the "
        "repository/service (or get_db) in the test."
    )
    yield  # pragma: no cover


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings with small limits, so size checks are cheap to exercise."""
    return Settings(
        database_url="postgresql+asyncpg://unused@localhost/unused",
        storage_dir=tmp_path / "storage",
        max_upload_size_mb=0.0625,  # 64 KiB
        max_files_per_upload=3,
    )


@pytest.fixture
def overrides(settings: Settings) -> Iterator[Overrides]:
    """``app.dependency_overrides``, pre-set for isolation and always cleared."""
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = _no_database
    try:
        yield app.dependency_overrides
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
async def client(overrides: Overrides) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


@pytest.fixture
def repository(overrides: Overrides) -> Any:
    """An autospecced DocumentRepository behind every route that reads data."""
    double = create_autospec(DocumentRepository, instance=True)
    overrides[get_document_repository] = lambda: double
    return double


@pytest.fixture
def service(overrides: Overrides) -> Any:
    """An autospecced DocumentService behind upload, retry and delete routes."""
    double = create_autospec(DocumentService, instance=True)
    overrides[get_document_service] = lambda: double
    return double
