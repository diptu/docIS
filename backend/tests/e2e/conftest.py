"""E2E layer: client workflows through the whole application.

Every request goes through the real routes, services, repositories and
storage, on a fresh SQLite database per test (``session_factory`` from the
root conftest) and a temp storage directory. Background work runs
in-process: ``await run_worker()`` executes every queued job the way a
Celery worker would (OCR, parsing, search indexing, then invoice
extraction when an LLM double is given).

Two things are replaced, because they are external services: the Celery
broker (jobs stay queued until ``run_worker``) and Claude (a FakeInvoiceLLM
passed to ``run_worker``). Search embeddings use the deterministic
FakeEmbedder instead of downloading the real model.
"""

from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import get_job_dispatcher
from app.api.routes.search import get_search_embedder
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.main import app
from app.processing.ocr import OcrConfig
from app.processing.processor import DocumentProcessor
from app.storage.local import LocalStorage
from app.worker.dispatch import no_dispatch
from app.worker.runtime import Runtime
from tests.support.fakes import FakeEmbedder
from tests.support.worker import Worker, run_due_jobs


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url="sqlite+aiosqlite://",
        storage_dir=tmp_path / "storage",
        # The fake embedder's similarities are lower than the real model's.
        semantic_min_score=0.1,
    )


@pytest.fixture
def overrides(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> Iterator[dict[object, object]]:
    async def test_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = test_db
    app.dependency_overrides[get_settings] = lambda: settings
    # No broker: jobs stay queued until run_worker executes them.
    app.dependency_overrides[get_job_dispatcher] = lambda: no_dispatch
    app.dependency_overrides[get_search_embedder] = FakeEmbedder
    try:
        yield app.dependency_overrides  # type: ignore[misc]
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
async def client(overrides: dict[object, object]) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


@pytest.fixture
def run_worker(
    session_factory: async_sessionmaker[AsyncSession],
    make_runtime: Callable[..., Runtime],
    settings: Settings,
) -> Worker:
    """``await run_worker(llm=None)``: run all due jobs; returns how many ran.

    With ``llm`` (e.g. FakeInvoiceLLM), successful content extraction is
    followed by AI invoice extraction, as in production.
    """

    async def run(llm: object | None = None) -> int:
        processor = DocumentProcessor(
            session_factory,
            LocalStorage(settings.storage_dir),
            OcrConfig(timeout_seconds=20),
            embedder=FakeEmbedder(),  # type: ignore[arg-type]
        )
        runtime = make_runtime(processor=processor, invoice_llm=llm)
        return await run_due_jobs(runtime, session_factory)

    return run
