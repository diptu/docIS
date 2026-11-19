"""
Database-backed job queue.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.document import Document, DocumentStatus
from app.models.processing_job import JobStatus, ProcessingJob
from app.processing.progress import ProgressSnapshot

MAX_ERROR_LENGTH = 2000


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ClaimedJob:
    """A job a worker has taken ownership of."""

    id: uuid.UUID
    document_id: uuid.UUID
    attempts: int
    max_attempts: int

    @property
    def is_last_attempt(self) -> bool:
        return self.attempts >= self.max_attempts


class JobQueue:
    """Claim, update and finish processing jobs.

    Every operation uses its own short transaction, so progress writes are
    visible to API readers immediately.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def claim(self, worker_id: str) -> ClaimedJob | None:
        """Take the oldest due queued job, or return None if there is none.

        Jobs waiting out a retry backoff (``next_run_at`` in the future) are
        skipped until they are due.

        ``FOR UPDATE SKIP LOCKED`` lets several workers claim concurrently
        without blocking each other or taking the same job (PostgreSQL).
        """
        async with self.session_factory() as session, session.begin():
            job = await session.scalar(
                select(ProcessingJob)
                .where(
                    ProcessingJob.status == JobStatus.QUEUED,
                    or_(
                        ProcessingJob.next_run_at.is_(None),
                        ProcessingJob.next_run_at <= utcnow(),
                    ),
                )
                .order_by(ProcessingJob.created_at, ProcessingJob.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                return None
            now = utcnow()
            job.status = JobStatus.RUNNING
            job.attempts += 1
            job.worker_id = worker_id
            job.started_at = now
            job.heartbeat_at = now
            job.finished_at = None
            job.next_run_at = None
            # ``error`` is kept: during a retry it shows why the last attempt
            # failed. It is cleared when the job succeeds.
            job.stage = None
            job.progress_current = 0
            job.progress_total = None
            return ClaimedJob(
                id=job.id,
                document_id=job.document_id,
                attempts=job.attempts,
                max_attempts=job.max_attempts,
            )

    async def _update(self, job_id: uuid.UUID, **values: object) -> None:
        async with self.session_factory() as session, session.begin():
            await session.execute(
                update(ProcessingJob)
                .where(
                    ProcessingJob.id == job_id,
                    ProcessingJob.status == JobStatus.RUNNING,
                )
                .values(heartbeat_at=utcnow(), **values)
            )

    async def report_progress(
        self, job_id: uuid.UUID, progress: ProgressSnapshot
    ) -> None:
        """Persist progress and refresh the job's heartbeat."""
        await self._update(
            job_id,
            stage=progress.stage,
            progress_current=progress.current,
            progress_total=progress.total,
        )

    async def complete(self, job_id: uuid.UUID, progress: ProgressSnapshot) -> None:
        """Mark a running job succeeded."""
        await self._update(
            job_id,
            status=JobStatus.SUCCEEDED,
            stage=progress.stage,
            progress_current=progress.total or progress.current,
            progress_total=progress.total,
            error=None,
            finished_at=utcnow(),
        )

    async def retry_later(
        self,
        job_id: uuid.UUID,
        error: str,
        progress: ProgressSnapshot,
        retry_at: datetime,
    ) -> None:
        """Return a failed attempt to the queue, due again at ``retry_at``."""
        await self._update(
            job_id,
            status=JobStatus.QUEUED,
            stage=progress.stage,
            progress_current=progress.current,
            progress_total=progress.total,
            error=error[:MAX_ERROR_LENGTH],
            next_run_at=retry_at,
            worker_id=None,
        )

    async def fail(
        self, job_id: uuid.UUID, error: str, progress: ProgressSnapshot
    ) -> None:
        """Mark a running job permanently failed, and its document too."""
        async with self.session_factory() as session, session.begin():
            result = await session.execute(
                update(ProcessingJob)
                .where(
                    ProcessingJob.id == job_id,
                    ProcessingJob.status == JobStatus.RUNNING,
                )
                .values(
                    status=JobStatus.FAILED,
                    stage=progress.stage,
                    progress_current=progress.current,
                    progress_total=progress.total,
                    error=error[:MAX_ERROR_LENGTH],
                    heartbeat_at=utcnow(),
                    finished_at=utcnow(),
                )
                .returning(ProcessingJob.document_id)
            )
            document_id = result.scalar_one_or_none()
            if document_id is not None:
                await _mark_documents_failed(session, [document_id])

    async def recover_stale(self, lease_seconds: float) -> tuple[int, int]:
        """Handle running jobs whose heartbeat expired (their worker died).

        Jobs with attempts left are requeued. Jobs already on their last
        attempt are failed: a job that repeatedly kills its worker (e.g. by
        exhausting memory) must not be retried forever.
        Returns ``(requeued, failed)`` counts.
        """
        cutoff = utcnow() - timedelta(seconds=lease_seconds)
        stale = (
            ProcessingJob.status == JobStatus.RUNNING,
            ProcessingJob.heartbeat_at < cutoff,
        )
        async with self.session_factory() as session, session.begin():
            requeued = await session.execute(
                update(ProcessingJob)
                .where(*stale, ProcessingJob.attempts < ProcessingJob.max_attempts)
                .values(
                    status=JobStatus.QUEUED,
                    worker_id=None,
                    next_run_at=None,
                    error="Worker stopped responding; job requeued.",
                )
                .returning(ProcessingJob.id)
            )
            requeued_count = len(requeued.all())
            failed = await session.execute(
                update(ProcessingJob)
                .where(*stale, ProcessingJob.attempts >= ProcessingJob.max_attempts)
                .values(
                    status=JobStatus.FAILED,
                    finished_at=utcnow(),
                    error=(
                        "Worker stopped responding on the final attempt "
                        "(the document may be too large or crash the worker)."
                    ),
                )
                .returning(ProcessingJob.document_id)
            )
            failed_documents = list(failed.scalars())
            await _mark_documents_failed(session, failed_documents)
        return requeued_count, len(failed_documents)


async def _mark_documents_failed(
    session: AsyncSession, document_ids: list[uuid.UUID]
) -> None:
    if document_ids:
        await session.execute(
            update(Document)
            .where(Document.id.in_(document_ids))
            .values(status=DocumentStatus.FAILED)
        )
