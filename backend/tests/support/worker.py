"""Running queued jobs in-process, the way Celery workers would."""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import JobStatus, ProcessingJob
from app.worker.jobs import execute_job
from app.worker.queue import utcnow

if TYPE_CHECKING:
    from app.worker.runtime import Runtime

# ``await run_worker(llm=None)`` in E2E tests: runs due jobs, returns the count.
Worker = Callable[..., Awaitable[int]]


async def run_due_jobs(
    runtime: "Runtime", session_factory: async_sessionmaker[AsyncSession]
) -> int:
    """Run every queued job that is due, until none are left (like workers).

    With zero retry backoff, retries are due immediately and run too.
    """
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
