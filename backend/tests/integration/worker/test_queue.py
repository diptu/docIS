import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import DocumentStatus, JobStatus, ProcessingJob
from app.processing.progress import ProgressSnapshot
from app.worker.queue import FINAL_ATTEMPT_LOST, MAX_ERROR_LENGTH, JobQueue
from tests.support.factories import (
    add_jobs,
    ago,
    document_status,
    get_job,
    set_job,
)

Factory = async_sessionmaker[AsyncSession]
LEASE = 300
SNAPSHOT = ProgressSnapshot(stage="ocr", current=2, total=5, version=3)


async def test_claim_takes_ownership(session_factory: Factory) -> None:
    [job_id] = await add_jobs(session_factory, 1)

    claimed = await JobQueue(session_factory).claim(job_id, "worker-a", LEASE)

    assert claimed is not None
    assert (claimed.id, claimed.attempts, claimed.is_last_attempt) == (job_id, 1, False)
    job = await get_job(session_factory, job_id)
    assert (job.status, job.worker_id) == (JobStatus.RUNNING, "worker-a")
    assert job.started_at is not None and job.heartbeat_at is not None


async def test_duplicate_delivery_is_not_claimed_twice(
    session_factory: Factory,
) -> None:
    [job_id] = await add_jobs(session_factory, 1)
    queue = JobQueue(session_factory)

    assert await queue.claim(job_id, "a", LEASE) is not None
    assert await queue.claim(job_id, "b", LEASE) is None  # alive elsewhere

    assert (await get_job(session_factory, job_id)).attempts == 1


@pytest.mark.parametrize("status", [JobStatus.SUCCEEDED, JobStatus.FAILED])
async def test_finished_jobs_are_not_claimed(
    session_factory: Factory, status: JobStatus
) -> None:
    [job_id] = await add_jobs(session_factory, 1)
    await set_job(session_factory, job_id, status=status)

    assert await JobQueue(session_factory).claim(job_id, "w", LEASE) is None


async def test_unknown_job_is_not_claimed(session_factory: Factory) -> None:
    assert await JobQueue(session_factory).claim(uuid.uuid4(), "w", LEASE) is None


async def test_job_waiting_for_backoff_is_not_claimed_early(
    session_factory: Factory,
) -> None:
    [job_id] = await add_jobs(session_factory, 1)
    queue = JobQueue(session_factory)
    await set_job(
        session_factory, job_id, next_run_at=datetime.now(UTC) + timedelta(minutes=5)
    )

    assert await queue.claim(job_id, "w", LEASE) is None

    # A retry message arriving a moment early (within tolerance) is accepted.
    await set_job(
        session_factory, job_id, next_run_at=datetime.now(UTC) + timedelta(seconds=2)
    )
    assert await queue.claim(job_id, "w", LEASE) is not None


async def test_redelivered_job_of_dead_worker_is_reclaimed(
    session_factory: Factory,
) -> None:
    [job_id] = await add_jobs(session_factory, 1)
    queue = JobQueue(session_factory)
    await queue.claim(job_id, "dead", LEASE)
    await set_job(session_factory, job_id, heartbeat_at=ago(minutes=10))

    reclaimed = await queue.claim(job_id, "new", LEASE)

    assert reclaimed is not None and reclaimed.attempts == 2
    assert (await get_job(session_factory, job_id)).worker_id == "new"


async def test_redelivered_job_on_final_attempt_is_failed(
    session_factory: Factory,
) -> None:
    """A job that keeps killing its worker must not loop forever."""
    [job_id] = await add_jobs(session_factory, 1)
    queue = JobQueue(session_factory)
    await queue.claim(job_id, "dead", LEASE)
    await set_job(session_factory, job_id, attempts=3, heartbeat_at=ago(minutes=10))

    assert await queue.claim(job_id, "new", LEASE) is None

    job = await get_job(session_factory, job_id)
    assert (job.status, job.error) == (JobStatus.FAILED, FINAL_ATTEMPT_LOST)
    assert await document_status(session_factory, job_id) == DocumentStatus.FAILED


async def test_progress_complete_and_fail(session_factory: Factory) -> None:
    ok, bad = await add_jobs(session_factory, 2)
    queue = JobQueue(session_factory)
    await queue.claim(ok, "w", LEASE)
    await queue.claim(bad, "w", LEASE)

    await queue.report_progress(ok, SNAPSHOT)
    job = await get_job(session_factory, ok)
    assert (job.stage, job.progress_current, job.progress_total) == ("ocr", 2, 5)

    await queue.complete(ok, SNAPSHOT)
    job = await get_job(session_factory, ok)
    assert job.status == JobStatus.SUCCEEDED
    assert (job.progress_current, job.progress_total, job.error) == (5, 5, None)

    await queue.fail(bad, "x" * (MAX_ERROR_LENGTH + 50), SNAPSHOT)
    job = await get_job(session_factory, bad)
    assert job.status == JobStatus.FAILED
    assert job.error is not None and len(job.error) == MAX_ERROR_LENGTH
    assert job.progress_current == 2
    assert await document_status(session_factory, bad) == DocumentStatus.FAILED


async def test_updates_ignore_jobs_that_are_not_running(
    session_factory: Factory,
) -> None:
    [job_id] = await add_jobs(session_factory, 1)

    await JobQueue(session_factory).complete(job_id, SNAPSHOT)  # never claimed

    assert (await get_job(session_factory, job_id)).status == JobStatus.QUEUED


async def test_retry_later_requeues_with_backoff(session_factory: Factory) -> None:
    [job_id] = await add_jobs(session_factory, 1)
    queue = JobQueue(session_factory)
    await queue.claim(job_id, "w", LEASE)
    later = datetime.now(UTC) + timedelta(minutes=5)

    await queue.retry_later(job_id, "OcrError: timeout", SNAPSHOT, later)

    job = await get_job(session_factory, job_id)
    assert (job.status, job.error, job.worker_id) == (
        JobStatus.QUEUED,
        "OcrError: timeout",
        None,
    )
    assert job.next_run_at is not None


async def test_recover_stale(session_factory: Factory) -> None:
    stale, final, alive = await add_jobs(session_factory, 3)
    queue = JobQueue(session_factory)
    for job_id in (stale, final, alive):
        await queue.claim(job_id, "w", LEASE)
    await set_job(session_factory, stale, heartbeat_at=ago(minutes=10))
    await set_job(session_factory, final, heartbeat_at=ago(minutes=10), attempts=3)

    requeued, failed = await queue.recover_stale(LEASE)

    assert (requeued, failed) == ([stale], 1)
    assert (await get_job(session_factory, stale)).status == JobStatus.QUEUED
    assert (await get_job(session_factory, final)).status == JobStatus.FAILED
    assert (await get_job(session_factory, alive)).status == JobStatus.RUNNING


async def test_take_undelivered_returns_long_waiting_jobs_once(
    session_factory: Factory,
) -> None:
    old, fresh, backoff = await add_jobs(session_factory, 3)
    queue = JobQueue(session_factory)
    await set_job(session_factory, old, updated_at=ago(minutes=20))
    await set_job(
        session_factory,
        backoff,
        updated_at=ago(minutes=20),
        next_run_at=ago(minutes=-5),
    )

    assert await queue.take_undelivered(600) == [old]
    # Touched: not re-sent on the next recovery run.
    assert await queue.take_undelivered(600) == []
    assert (await get_job(session_factory, fresh)).status == JobStatus.QUEUED


async def test_only_one_active_job_per_document(session_factory: Factory) -> None:
    [job_id] = await add_jobs(session_factory, 1)
    job = await get_job(session_factory, job_id)

    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            session.add(ProcessingJob(document_id=job.document_id))

    await set_job(session_factory, job_id, status=JobStatus.FAILED)
    async with session_factory() as session, session.begin():
        session.add(ProcessingJob(document_id=job.document_id))


async def test_stale_final_invoice_job_leaves_document_status(
    session_factory: Factory,
) -> None:
    stale_content, stale_invoice = await add_jobs(session_factory, 2)
    queue = JobQueue(session_factory)
    await set_job(session_factory, stale_invoice, kind="extract_invoice")
    for job_id in (stale_content, stale_invoice):
        await queue.claim(job_id, "dead", LEASE)
        await set_job(session_factory, job_id, attempts=3, heartbeat_at=ago(minutes=10))

    assert await queue.recover_stale(LEASE) == ([], 2)

    assert (
        await document_status(session_factory, stale_content) == DocumentStatus.FAILED
    )
    assert (
        await document_status(session_factory, stale_invoice) == DocumentStatus.UPLOADED
    )


async def test_redelivered_final_invoice_job_leaves_document_status(
    session_factory: Factory,
) -> None:
    [job_id] = await add_jobs(session_factory, 1)
    queue = JobQueue(session_factory)
    await set_job(session_factory, job_id, kind="extract_invoice")
    await queue.claim(job_id, "dead", LEASE)
    await set_job(session_factory, job_id, attempts=3, heartbeat_at=ago(minutes=10))

    assert await queue.claim(job_id, "new", LEASE) is None
    assert await document_status(session_factory, job_id) == DocumentStatus.UPLOADED


async def test_complete_without_follow_up_on_a_job_not_running(
    session_factory: Factory,
) -> None:
    from app.models import JobKind

    [job_id] = await add_jobs(session_factory, 1)

    assert (
        await JobQueue(session_factory).complete(
            job_id, SNAPSHOT, JobKind.EXTRACT_INVOICE
        )
        is None
    )
