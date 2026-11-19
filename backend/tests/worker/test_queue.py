import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Document, DocumentStatus, JobStatus, ProcessingJob
from app.processing.progress import ProgressSnapshot
from app.worker.queue import MAX_ERROR_LENGTH, JobQueue

Factory = async_sessionmaker[AsyncSession]


async def add_jobs(factory: Factory, count: int) -> list[uuid.UUID]:
    """Create documents with queued jobs, oldest first."""
    ids = []
    async with factory() as session:
        for i in range(count):
            document = Document(
                id=uuid.uuid4(),
                file_name=f"{i}.pdf",
                file_type="application/pdf",
                storage_path=f"{i}.pdf",
                status=DocumentStatus.UPLOADED,
            )
            job = ProcessingJob(
                document_id=document.id,
                created_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=i),
            )
            session.add_all([document, job])
            await session.flush()
            ids.append(job.id)
        await session.commit()
    return ids


async def get_job(factory: Factory, job_id: uuid.UUID) -> ProcessingJob:
    async with factory() as session:
        job = await session.get(ProcessingJob, job_id)
        assert job is not None
        return job


SNAPSHOT = ProgressSnapshot(stage="ocr", current=2, total=5, version=3)


@pytest.mark.asyncio
async def test_claim_takes_oldest_queued_job(session_factory: Factory) -> None:
    first, second = await add_jobs(session_factory, 2)
    queue = JobQueue(session_factory)

    claimed = await queue.claim("worker-a")

    assert claimed is not None
    assert (claimed.id, claimed.attempts) == (first, 1)
    job = await get_job(session_factory, first)
    assert job.status == JobStatus.RUNNING
    assert job.worker_id == "worker-a"
    assert job.started_at is not None and job.heartbeat_at is not None
    assert (await get_job(session_factory, second)).status == JobStatus.QUEUED


@pytest.mark.asyncio
async def test_claim_returns_none_when_queue_is_empty(session_factory: Factory) -> None:
    assert await JobQueue(session_factory).claim("w") is None


@pytest.mark.asyncio
async def test_progress_complete_and_fail(session_factory: Factory) -> None:
    ok, bad = await add_jobs(session_factory, 2)
    queue = JobQueue(session_factory)
    await queue.claim("w")
    await queue.claim("w")

    await queue.report_progress(ok, SNAPSHOT)
    job = await get_job(session_factory, ok)
    assert (job.stage, job.progress_current, job.progress_total) == ("ocr", 2, 5)

    await queue.complete(ok, SNAPSHOT)
    job = await get_job(session_factory, ok)
    assert job.status == JobStatus.SUCCEEDED
    assert (job.progress_current, job.progress_total) == (5, 5)
    assert job.finished_at is not None

    await queue.fail(bad, "x" * (MAX_ERROR_LENGTH + 50), SNAPSHOT)
    job = await get_job(session_factory, bad)
    assert job.status == JobStatus.FAILED
    assert job.error is not None and len(job.error) == MAX_ERROR_LENGTH
    assert job.progress_current == 2  # where it got to


@pytest.mark.asyncio
async def test_updates_ignore_jobs_that_are_not_running(
    session_factory: Factory,
) -> None:
    [job_id] = await add_jobs(session_factory, 1)
    queue = JobQueue(session_factory)

    await queue.complete(job_id, SNAPSHOT)  # never claimed

    assert (await get_job(session_factory, job_id)).status == JobStatus.QUEUED


@pytest.mark.asyncio
async def test_recover_stale_requeues_dead_workers_jobs_only(
    session_factory: Factory,
) -> None:
    stale, fresh = await add_jobs(session_factory, 2)
    queue = JobQueue(session_factory)
    await queue.claim("dead")
    await queue.claim("alive")
    async with session_factory() as session, session.begin():
        await session.execute(
            update(ProcessingJob)
            .where(ProcessingJob.id == stale)
            .values(heartbeat_at=datetime.now(UTC) - timedelta(minutes=10))
        )

    assert await queue.recover_stale(lease_seconds=300) == (1, 0)

    async with session_factory() as session:
        statuses = dict(
            (
                await session.execute(select(ProcessingJob.id, ProcessingJob.status))
            ).all()
        )
    assert statuses == {stale: JobStatus.QUEUED, fresh: JobStatus.RUNNING}
    reclaimed = await queue.claim("new")
    assert reclaimed is not None
    assert (reclaimed.id, reclaimed.attempts) == (stale, 2)


async def set_job(factory: Factory, job_id: uuid.UUID, **values: object) -> None:
    async with factory() as session, session.begin():
        await session.execute(
            update(ProcessingJob).where(ProcessingJob.id == job_id).values(**values)
        )


async def document_status(factory: Factory, job_id: uuid.UUID) -> str:
    async with factory() as session:
        job = await session.get(ProcessingJob, job_id)
        assert job is not None
        document = await session.get(Document, job.document_id)
        assert document is not None
        return document.status


@pytest.mark.asyncio
async def test_retry_later_requeues_with_backoff(session_factory: Factory) -> None:
    [job_id] = await add_jobs(session_factory, 1)
    queue = JobQueue(session_factory)
    await queue.claim("w")
    later = datetime.now(UTC) + timedelta(minutes=5)

    await queue.retry_later(job_id, "OcrError: timeout", SNAPSHOT, later)

    job = await get_job(session_factory, job_id)
    assert job.status == JobStatus.QUEUED
    assert job.error == "OcrError: timeout"
    assert job.worker_id is None
    assert job.next_run_at is not None
    # Not due yet, so it cannot be claimed.
    assert await queue.claim("w") is None

    await set_job(session_factory, job_id, next_run_at=datetime.now(UTC) - timedelta(1))
    reclaimed = await queue.claim("w")
    assert reclaimed is not None
    assert (reclaimed.attempts, reclaimed.is_last_attempt) == (2, False)
    job = await get_job(session_factory, job_id)
    assert job.next_run_at is None
    assert job.error == "OcrError: timeout"  # kept while retrying

    await queue.complete(job_id, SNAPSHOT)
    assert (await get_job(session_factory, job_id)).error is None


@pytest.mark.asyncio
async def test_fail_marks_document_failed(session_factory: Factory) -> None:
    [job_id] = await add_jobs(session_factory, 1)
    queue = JobQueue(session_factory)
    await queue.claim("w")

    await queue.fail(job_id, "FileNotFoundError: gone", SNAPSHOT)

    assert await document_status(session_factory, job_id) == DocumentStatus.FAILED


@pytest.mark.asyncio
async def test_stale_job_on_final_attempt_is_failed_not_requeued(
    session_factory: Factory,
) -> None:
    """A job that keeps killing its worker must not loop forever."""
    [job_id] = await add_jobs(session_factory, 1)
    queue = JobQueue(session_factory)
    await queue.claim("w")
    await set_job(
        session_factory,
        job_id,
        attempts=3,
        heartbeat_at=datetime.now(UTC) - timedelta(minutes=10),
    )

    assert await queue.recover_stale(lease_seconds=300) == (0, 1)

    job = await get_job(session_factory, job_id)
    assert job.status == JobStatus.FAILED
    assert job.error is not None and "final attempt" in job.error
    assert await document_status(session_factory, job_id) == DocumentStatus.FAILED


@pytest.mark.asyncio
async def test_only_one_active_job_per_document(session_factory: Factory) -> None:
    from sqlalchemy.exc import IntegrityError

    [job_id] = await add_jobs(session_factory, 1)
    job = await get_job(session_factory, job_id)

    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            session.add(ProcessingJob(document_id=job.document_id))

    # Once the first job is finished, a new one is allowed.
    await set_job(session_factory, job_id, status=JobStatus.FAILED)
    async with session_factory() as session, session.begin():
        session.add(ProcessingJob(document_id=job.document_id))
