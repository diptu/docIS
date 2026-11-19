"""
Celery tasks.
"""

import asyncio
import logging
import uuid
from collections.abc import Iterable

from celery import Task

from app.worker.celery_app import PROCESS_TASK, RECOVER_TASK, celery_app
from app.worker.jobs import JobOutcome, execute_job, recover
from app.worker.runtime import get_runtime

logger = logging.getLogger(__name__)

# Publishing must not hang an upload request when the broker is down; the
# job row is already committed and documents.recover will dispatch it.
PUBLISH_RETRY_POLICY = {
    "max_retries": 2,
    "interval_start": 0,
    "interval_step": 0.5,
    "interval_max": 1,
}


@celery_app.task(bind=True, name=PROCESS_TASK)
def process_document(self: Task, job_id: str) -> str:  # type: ignore[type-arg]
    """Process one job; schedules a Celery retry after a retryable failure."""
    worker_id = self.request.hostname or "celery"
    return _settle(
        self, asyncio.run(execute_job(get_runtime(), uuid.UUID(job_id), worker_id))
    )


def _settle(task: Task, outcome: JobOutcome) -> str:  # type: ignore[type-arg]
    """Dispatch any follow-up job, then hand a retry back to Celery or
    report the final status."""
    if outcome.follow_up is not None:
        dispatch_jobs([outcome.follow_up])
    if outcome.retry_in is not None:
        # Attempts are counted and limited in the job row, so Celery's own
        # retry limit is disabled.
        raise task.retry(countdown=outcome.retry_in, max_retries=None)
    return outcome.status


@celery_app.task(name=RECOVER_TASK)
def recover_jobs() -> int:
    """Periodic safety net: re-dispatch lost or orphaned jobs."""
    job_ids = asyncio.run(recover(get_runtime()))
    dispatch_jobs(job_ids)
    return len(job_ids)


def dispatch_jobs(job_ids: Iterable[uuid.UUID]) -> None:
    """Publish a processing task per job. Never raises.

    A job that cannot be published stays queued in the database and is
    picked up by ``documents.recover``.
    """
    for job_id in job_ids:
        try:
            process_document.apply_async(
                args=(str(job_id),), retry=True, retry_policy=PUBLISH_RETRY_POLICY
            )
        except Exception:
            logger.exception(
                "Could not publish job %s; it will be recovered later", job_id
            )
