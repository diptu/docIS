"""
Job state transitions in the ``processing_jobs`` table.

Celery delivers task messages; this table decides what actually runs.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.document import Document, DocumentStatus
from app.models.processing_job import JobKind, JobStatus, ProcessingJob
from app.processing.progress import ProgressSnapshot

MAX_ERROR_LENGTH = 2000
# A retry message may arrive slightly before ``next_run_at``.
DUE_TOLERANCE = timedelta(seconds=5)
FINAL_ATTEMPT_LOST = (
    "Worker stopped responding on the final attempt "
    "(the document may be too large or crash the worker)."
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    """Treat naive timestamps (SQLite) as UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


@dataclass(frozen=True)
class ClaimedJob:
    """A job a worker has taken ownership of."""

    id: uuid.UUID
    document_id: uuid.UUID
    attempts: int
    max_attempts: int
    kind: str = JobKind.EXTRACT_CONTENT

    @property
    def is_last_attempt(self) -> bool:
        return self.attempts >= self.max_attempts


class JobQueue:
    """Claim, update and finish processing jobs.

    Every operation is its own short transaction, so progress writes are
    visible to API readers immediately.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def claim(
        self, job_id: uuid.UUID, worker_id: str, lease_seconds: float
    ) -> ClaimedJob | None:
        """Take ownership of a job, or return None if it must not run now.

        Claimable: a queued job that is due, or a running job whose heartbeat
        expired (its worker died and the task was redelivered). A stale job
        that already used its last attempt is failed instead. The row lock
        makes concurrent deliveries of the same job claim it at most once.
        """
        now = utcnow()
        async with self.session_factory() as session, session.begin():
            job = await session.scalar(
                select(ProcessingJob)
                .where(ProcessingJob.id == job_id)
                .with_for_update()
            )
            if job is None:
                return None

            if job.status == JobStatus.QUEUED:
                next_run_at = _aware(job.next_run_at)
                if next_run_at is not None and next_run_at > now + DUE_TOLERANCE:
                    return None
            elif job.status == JobStatus.RUNNING:
                heartbeat = _aware(job.heartbeat_at)
                if heartbeat is not None and heartbeat > now - timedelta(
                    seconds=lease_seconds
                ):
                    return None  # alive elsewhere
                if job.attempts >= job.max_attempts:
                    job.status = JobStatus.FAILED
                    job.error = FINAL_ATTEMPT_LOST
                    job.finished_at = now
                    if job.kind == JobKind.EXTRACT_CONTENT:
                        await _mark_documents_failed(session, [job.document_id])
                    return None
            else:
                return None  # succeeded or failed

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
                kind=job.kind,
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

    async def complete(
        self,
        job_id: uuid.UUID,
        progress: ProgressSnapshot,
        follow_up: JobKind | None = None,
    ) -> uuid.UUID | None:
        """Mark a running job succeeded, optionally queueing the next job.

        The follow-up job is created in the same transaction, so a crash can
        never leave a finished job without its successor. Returns the
        follow-up job's ID (to be dispatched by the caller), if any.
        """
        async with self.session_factory() as session, session.begin():
            result = await session.execute(
                update(ProcessingJob)
                .where(
                    ProcessingJob.id == job_id,
                    ProcessingJob.status == JobStatus.RUNNING,
                )
                .values(
                    status=JobStatus.SUCCEEDED,
                    stage=progress.stage,
                    progress_current=progress.total or progress.current,
                    progress_total=progress.total,
                    error=None,
                    heartbeat_at=utcnow(),
                    finished_at=utcnow(),
                )
                .returning(ProcessingJob.document_id, ProcessingJob.max_attempts)
            )
            row = result.one_or_none()
            if row is None or follow_up is None:
                return None
            next_job = ProcessingJob(
                id=uuid.uuid4(),
                document_id=row.document_id,
                kind=follow_up,
                status=JobStatus.QUEUED,
                max_attempts=row.max_attempts,
            )
            session.add(next_job)
            return next_job.id

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
        """Mark a running job permanently failed.

        A failed content job also fails its document. A failed invoice
        extraction does not: the document's text and sheets are still valid.
        """
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
                .returning(ProcessingJob.document_id, ProcessingJob.kind)
            )
            row = result.one_or_none()
            if row is not None and row.kind == JobKind.EXTRACT_CONTENT:
                await _mark_documents_failed(session, [row.document_id])

    async def recover_stale(self, lease_seconds: float) -> tuple[list[uuid.UUID], int]:
        """Handle running jobs whose heartbeat expired (their worker died).

        Jobs with attempts left are requeued (and must be re-dispatched).
        Jobs already on their last attempt are failed: a job that repeatedly
        kills its worker must not be retried forever.
        Returns ``(requeued job IDs, number failed)``.
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
            requeued_ids = list(requeued.scalars())
            failed = await session.execute(
                update(ProcessingJob)
                .where(*stale, ProcessingJob.attempts >= ProcessingJob.max_attempts)
                .values(
                    status=JobStatus.FAILED,
                    finished_at=utcnow(),
                    error=FINAL_ATTEMPT_LOST,
                )
                .returning(ProcessingJob.document_id, ProcessingJob.kind)
            )
            failed_rows = failed.all()
            await _mark_documents_failed(
                session,
                [
                    r.document_id
                    for r in failed_rows
                    if r.kind == JobKind.EXTRACT_CONTENT
                ],
            )
        return requeued_ids, len(failed_rows)

    async def take_undelivered(self, older_than_seconds: float) -> list[uuid.UUID]:
        """Return queued jobs that have been due for too long, and touch them.

        Such jobs were most likely never delivered (e.g. the broker was down
        when they were created). ``updated_at`` is bumped so a job is not
        re-sent on every recovery run while its new message waits in line.
        """
        cutoff = utcnow() - timedelta(seconds=older_than_seconds)
        due_since = func.coalesce(ProcessingJob.next_run_at, ProcessingJob.updated_at)
        async with self.session_factory() as session, session.begin():
            result = await session.execute(
                update(ProcessingJob)
                .where(ProcessingJob.status == JobStatus.QUEUED, due_since < cutoff)
                .values(updated_at=utcnow(), next_run_at=None)
                .returning(ProcessingJob.id)
            )
            return list(result.scalars())


async def _mark_documents_failed(
    session: AsyncSession, document_ids: list[uuid.UUID]
) -> None:
    if document_ids:
        await session.execute(
            update(Document)
            .where(Document.id.in_(document_ids))
            .values(status=DocumentStatus.FAILED)
        )
