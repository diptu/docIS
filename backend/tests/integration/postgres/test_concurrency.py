"""Row locking under real concurrency (separate connections, one event loop)."""

import asyncio
import datetime as dt

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import JobStatus, ProcessingJob
from app.repositories.document import DocumentRepository
from app.worker.queue import JobQueue
from tests.support.factories import add_jobs, get_job

Factory = async_sessionmaker[AsyncSession]


async def test_concurrent_deliveries_claim_a_job_exactly_once(
    pg_factory: Factory,
) -> None:
    """Celery may deliver a task twice; SELECT ... FOR UPDATE lets only one
    worker claim the job."""
    [job_id] = await add_jobs(pg_factory, 1)
    queue = JobQueue(pg_factory)

    claims = await asyncio.gather(
        *(queue.claim(job_id, f"worker-{i}", lease_seconds=300) for i in range(5))
    )

    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    job = await get_job(pg_factory, job_id)
    assert (job.status, job.attempts) == (JobStatus.RUNNING, 1)


async def test_delete_waits_for_a_claim_in_progress_and_keeps_the_job(
    pg_factory: Factory,
) -> None:
    """A delete that races a worker's claim blocks on the job's row lock,
    then sees the job running and keeps the document."""
    [job_id] = await add_jobs(pg_factory, 1)
    async with pg_factory() as session:
        document_id = (await session.get(ProcessingJob, job_id)).document_id  # type: ignore[union-attr]

    async with pg_factory() as worker:
        # The "worker" locks the job and marks it running, but has not
        # committed yet.
        await worker.execute(
            select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update()
        )
        await worker.execute(
            update(ProcessingJob)
            .where(ProcessingJob.id == job_id)
            .values(status=JobStatus.RUNNING, started_at=dt.datetime.now(dt.UTC))
        )

        async def delete() -> set[object]:
            async with pg_factory() as session:
                _, busy = await DocumentRepository(session).delete_documents(
                    [document_id]
                )
                return busy  # type: ignore[return-value]

        deleting = asyncio.create_task(delete())
        await asyncio.sleep(0.3)
        assert not deleting.done(), "delete must wait for the worker's lock"

        await worker.commit()

    assert await asyncio.wait_for(deleting, timeout=5) == {document_id}
    async with pg_factory() as session:
        assert await DocumentRepository(session).get(document_id) is not None
