"""
Running one processing job: claim it, process with live progress, record
the outcome (success, scheduled retry, or failure).

Celery-independent, so it can be tested and reused directly.
"""

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta

import anyio

from app.models.processing_job import JobKind
from app.processing.processor import DocumentNotProcessableError
from app.processing.progress import ProgressCallback, ProgressTracker
from app.worker.queue import ClaimedJob, utcnow
from app.worker.retry import is_retryable, retry_delay
from app.worker.runtime import Runtime

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JobOutcome:
    """What happened to a job run. ``retry_in`` is set when a retry is due."""

    status: str  # "skipped", "succeeded", "retrying" or "failed"
    retry_in: float | None = None
    # A job queued by this one's success, to be dispatched by the caller.
    follow_up: uuid.UUID | None = None


SKIPPED = JobOutcome("skipped")


async def execute_job(
    runtime: Runtime, job_id: uuid.UUID, worker_id: str
) -> JobOutcome:
    """Run a job if it can be claimed; otherwise do nothing.

    A job cannot be claimed if it is already running elsewhere, finished, or
    waiting out a retry backoff, which makes duplicate deliveries harmless.
    """
    job = await runtime.queue.claim(job_id, worker_id, runtime.lease_seconds)
    if job is None:
        logger.info("Job %s not claimable (done, running or not due); skipping", job_id)
        return SKIPPED

    logger.info(
        "Running %s for document %s (job %s, attempt %d/%d)",
        job.kind,
        job.document_id,
        job.id,
        job.attempts,
        job.max_attempts,
    )
    tracker = ProgressTracker()
    error: Exception | None = None
    done = anyio.Event()
    async with anyio.create_task_group() as task_group:
        task_group.start_soon(_report_progress, runtime, job, tracker, done)
        try:
            await _run(runtime, job, tracker.update)
        except Exception as exc:
            error = exc
        finally:
            # Stop the reporter gracefully rather than cancelling it: a
            # cancel landing mid-write would abort a database operation and
            # leave a connection-termination task that can block the event
            # loop's shutdown (asyncio.run) indefinitely.
            done.set()

    return await _finish(runtime, job, tracker, error)


async def _run(runtime: Runtime, job: ClaimedJob, progress: ProgressCallback) -> None:
    """Run the work for the job's kind."""
    if job.kind == JobKind.EXTRACT_CONTENT:
        await runtime.processor.process(job.document_id, progress)
    elif job.kind == JobKind.EXTRACT_INVOICE:
        if runtime.invoice_extractor is None:
            raise DocumentNotProcessableError("Invoice extraction is disabled.")
        await runtime.invoice_extractor.extract(job.document_id, progress)
    else:
        raise DocumentNotProcessableError(f"Unknown job kind {job.kind!r}.")


def _follow_up(runtime: Runtime, job: ClaimedJob) -> JobKind | None:
    """The job to queue after this one succeeds."""
    if job.kind == JobKind.EXTRACT_CONTENT and runtime.invoice_extractor is not None:
        return JobKind.EXTRACT_INVOICE
    return None


async def _finish(
    runtime: Runtime, job: ClaimedJob, tracker: ProgressTracker, error: Exception | None
) -> JobOutcome:
    progress = tracker.snapshot()
    if error is None:
        next_job = await runtime.queue.complete(
            job.id, progress, _follow_up(runtime, job)
        )
        logger.info("Job %s succeeded", job.id)
        return JobOutcome("succeeded", follow_up=next_job)

    message = f"{type(error).__name__}: {error}"
    if is_retryable(error) and not job.is_last_attempt:
        delay = retry_delay(
            job.attempts,
            runtime.retry_base_seconds,
            runtime.retry_max_seconds,
            runtime.retry_jitter,
        )
        logger.warning(
            "Job %s attempt %d/%d failed (%s); retrying in %.0fs",
            job.id,
            job.attempts,
            job.max_attempts,
            message,
            delay,
            exc_info=error,
        )
        retry_at = utcnow() + timedelta(seconds=delay)
        await runtime.queue.retry_later(job.id, message, progress, retry_at)
        return JobOutcome("retrying", retry_in=delay)

    reason = "permanent error" if not is_retryable(error) else "attempts exhausted"
    logger.error(
        "Job %s failed (%s, attempt %d/%d): %s",
        job.id,
        reason,
        job.attempts,
        job.max_attempts,
        message,
        exc_info=error,
    )
    await runtime.queue.fail(job.id, message, progress)
    return JobOutcome("failed")


async def _report_progress(
    runtime: Runtime, job: ClaimedJob, tracker: ProgressTracker, done: anyio.Event
) -> None:
    """Persist progress when it changes, and heartbeat at least per lease/3.

    Runs until ``done`` is set; it only stops between writes, never during
    one. (The final state is written by the job's outcome afterwards.)
    """
    written = 0
    last_write = time.monotonic()
    while True:
        with anyio.move_on_after(runtime.progress_interval):
            await done.wait()
        if done.is_set():
            return
        snapshot = tracker.snapshot()
        heartbeat_due = time.monotonic() - last_write >= runtime.lease_seconds / 3
        if snapshot.version == written and not heartbeat_due:
            continue
        try:
            await runtime.queue.report_progress(job.id, snapshot)
        except Exception:
            logger.exception("Could not report progress for job %s", job.id)
            continue
        written = snapshot.version
        last_write = time.monotonic()


async def recover(runtime: Runtime) -> list[uuid.UUID]:
    """Find jobs that need (re-)dispatching; returns their IDs.

    * Running jobs whose heartbeat expired (their whole worker died) are
      requeued, or failed if that was their last attempt.
    * Queued jobs that have been due for a long time were probably never
      delivered (e.g. the broker was down at upload) and are sent again.
    """
    requeued, failed = await runtime.queue.recover_stale(runtime.lease_seconds)
    if requeued:
        logger.warning("Requeued %d job(s) with expired leases", len(requeued))
    if failed:
        logger.error(
            "Failed %d job(s) whose worker died on their final attempt", failed
        )
    undelivered = await runtime.queue.take_undelivered(runtime.redispatch_after_seconds)
    if undelivered:
        logger.warning(
            "Re-dispatching %d queued job(s) never picked up", len(undelivered)
        )
    return [*requeued, *undelivered]
