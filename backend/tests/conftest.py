"""Shared fixtures."""

import asyncio
import os

# Tesseract's OpenMP threads busy-wait; several OCR processes at once (tests
# plus a local worker) can slow each other to a near-standstill. Tesseract
# recommends one thread per process when running in parallel. Child
# processes inherit this, so it must be set before any OCR runs.
os.environ.setdefault("OMP_THREAD_LIMIT", "1")

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401  (registers all tables on Base.metadata)
from app.db.base import Base

if TYPE_CHECKING:
    from app.worker.runtime import Runtime


@pytest.fixture
def db_engine(tmp_path: Path) -> Iterator[AsyncEngine]:
    """A fresh SQLite database file with the full schema.

    NullPool opens a new connection per use, so the engine works from any
    event loop (pytest-asyncio's or TestClient's).
    """
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'test.db'}", poolclass=NullPool
    )

    # SQLite ignores foreign keys (and ON DELETE CASCADE) unless enabled per
    # connection; turn them on so tests see PostgreSQL's behaviour.
    @event.listens_for(engine.sync_engine, "connect")
    def enable_foreign_keys(dbapi_connection: Any, _: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async def create() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(create())
    yield engine
    asyncio.run(engine.dispose())


@pytest.fixture
def session_factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False, autoflush=False)


@pytest.fixture
def make_runtime(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> Callable[..., "Runtime"]:
    """Build a worker Runtime on the test database and storage."""
    from app.processing.processor import DocumentProcessor
    from app.storage.local import LocalStorage
    from app.worker.queue import JobQueue
    from app.worker.runtime import Runtime

    def build(
        processor: object | None = None,
        invoice_llm: object | None = None,
        **overrides: object,
    ) -> Runtime:
        from app.extraction.service import InvoiceExtractor
        from app.processing.ocr import OcrConfig

        if invoice_llm is not None:
            overrides.setdefault(
                "invoice_extractor",
                InvoiceExtractor(session_factory, invoice_llm),  # type: ignore[arg-type]
            )
        options: dict[str, object] = {
            "progress_interval": 0.01,
            "retry_base_seconds": 0.0,
            "retry_jitter": 0.0,
            **overrides,
        }
        return Runtime(
            queue=JobQueue(session_factory),
            processor=processor  # type: ignore[arg-type]
            or DocumentProcessor(
                session_factory,
                LocalStorage(tmp_path / "storage"),
                # Fail fast if Tesseract stalls, instead of 120 s per attempt.
                OcrConfig(timeout_seconds=20),
            ),
            **options,  # type: ignore[arg-type]
        )

    return build


async def run_due_jobs(
    runtime: "Runtime", session_factory: async_sessionmaker[AsyncSession]
) -> int:
    """Run every queued job that is due, until none are left (like workers).

    With zero retry backoff, retries are due immediately and run too.
    """
    from sqlalchemy import or_, select

    from app.models import JobStatus, ProcessingJob
    from app.worker.jobs import execute_job
    from app.worker.queue import utcnow

    runs = 0
    while True:
        async with session_factory() as session:
            due = list(
                await session.scalars(
                    select(ProcessingJob.id)
                    .where(
                        ProcessingJob.status == JobStatus.QUEUED,
                        or_(
                            ProcessingJob.next_run_at.is_(None),
                            ProcessingJob.next_run_at <= utcnow(),
                        ),
                    )
                    .order_by(ProcessingJob.created_at)
                )
            )
        if not due:
            return runs
        for job_id in due:
            await execute_job(runtime, job_id, "test-worker")
            runs += 1
