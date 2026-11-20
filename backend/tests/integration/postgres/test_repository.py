"""Repository queries whose SQL differs on PostgreSQL."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import JobKind, JobStatus, ProcessingJob
from app.repositories.document import DocumentRepository
from tests.support.factories import add_jobs, get_job, seed_job_history, set_job

Factory = async_sessionmaker[AsyncSession]
START = datetime(2026, 1, 1, tzinfo=UTC)


async def _finish(
    factory: Factory, job_id: uuid.UUID, seconds: float, status: str
) -> None:
    await set_job(
        factory,
        job_id,
        status=status,
        started_at=START,
        finished_at=START + timedelta(seconds=seconds),
    )


async def test_stats_on_postgres_use_epoch_durations(pg_factory: Factory) -> None:
    """Counts follow each document's latest job; the average sums every
    successful job of a completed document (content + invoice)."""
    fast, slow, broken, _queued = await add_jobs(pg_factory, 4)
    await _finish(pg_factory, fast, 1.5, JobStatus.SUCCEEDED)
    await _finish(pg_factory, slow, 4.0, JobStatus.SUCCEEDED)
    await _finish(pg_factory, broken, 60.0, JobStatus.FAILED)
    # The slow document also had an invoice job: 2.5 s more.
    slow_document = (await get_job(pg_factory, slow)).document_id
    async with pg_factory() as session:
        session.add(
            ProcessingJob(
                document_id=slow_document,
                kind=JobKind.EXTRACT_INVOICE,
                status=JobStatus.SUCCEEDED,
                created_at=START + timedelta(minutes=1),
                started_at=START,
                finished_at=START + timedelta(seconds=2.5),
            )
        )
        await session.commit()

    async with pg_factory() as session:
        stats = await DocumentRepository(session).stats()

    assert (stats.total, stats.completed, stats.failed, stats.in_progress) == (
        4,
        2,
        1,
        1,
    )
    assert stats.avg_processing_seconds is not None
    assert abs(stats.avg_processing_seconds - (1.5 + 4.0 + 2.5) / 2) < 1e-6


async def test_stats_of_an_empty_database(pg_factory: Factory) -> None:
    async with pg_factory() as session:
        stats = await DocumentRepository(session).stats()

    assert stats.total == 0 and stats.avg_processing_seconds is None


async def test_job_monitor_ranks_latest_jobs_on_postgres(pg_factory: Factory) -> None:
    """The window-function query behaves the same as on SQLite."""
    jobs = await seed_job_history(pg_factory)

    async with pg_factory() as session:
        repository = DocumentRepository(session)
        entries, total, counts = await repository.list_all_jobs(0, 50)
        latest, latest_total, _ = await repository.list_all_jobs(
            0, 50, latest_only=True
        )

    assert [(e.job.id, e.is_latest) for e in entries] == [
        (jobs["retried"], True),
        (jobs["queued"], True),
        (jobs["running"], True),
        (jobs["failed"], False),
    ]
    assert (total, counts["failed"]) == (4, 1)
    assert (latest_total, {e.job.id for e in latest}) == (
        3,
        {jobs["retried"], jobs["queued"], jobs["running"]},
    )
