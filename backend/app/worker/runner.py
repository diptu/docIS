"""
The worker loop: claim a job, process it while reporting progress, finish it.
"""

import logging
import os
import socket
import time
import uuid
from datetime import timedelta
from pathlib import Path

import anyio

from app.processing.processor import DocumentProcessor
from app.processing.progress import ProgressTracker
from app.worker.queue import ClaimedJob, JobQueue, utcnow
from app.worker.retry import is_retryable, retry_delay

logger = logging.getLogger(__name__)


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


class Worker:
    """Runs processing jobs one at a time.

    Scale out by running more worker processes; the queue's row locking
    keeps them from taking the same job.
    """

    def __init__(
        self,
        queue: JobQueue,
        processor: DocumentProcessor,
        *,
        worker_id: str | None = None,
        poll_interval: float = 1.0,
        lease_seconds: float = 300.0,
        progress_interval: float = 1.0,
        heartbeat_file: Path | None = None,
        retry_base_seconds: float = 30.0,
        retry_max_seconds: float = 900.0,
        retry_jitter: float = 0.2,
    ) -> None:
        self.queue = queue
        self.processor = processor
        self.worker_id = worker_id or default_worker_id()
        self.poll_interval = poll_interval
        self.lease_seconds = lease_seconds
        self.progress_interval = progress_interval
        self.heartbeat_file = heartbeat_file
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds
        self.retry_jitter = retry_jitter

    async def run_once(self) -> bool:
        """Claim and run one job. Returns False if the queue was empty."""
        job = await self.queue.claim(self.worker_id)
        if job is None:
            return False
        await self._execute(job)
        return True

    async def run_until_idle(self) -> int:
        """Recover stale jobs, then run jobs until none are due.

        Jobs waiting out a retry backoff are not waited for.
        """
        await self.recover_stale()
        count = 0
        while await self.run_once():
            count += 1
        return count

    async def run_forever(self, stop: anyio.Event) -> None:
        """Poll for jobs until ``stop`` is set; finishes the current job first."""
        logger.info("Worker %s started", self.worker_id)
        next_recovery = 0.0
        while not stop.is_set():
            self._touch_heartbeat()
            if time.monotonic() >= next_recovery:
                await self.recover_stale()
                next_recovery = time.monotonic() + self.lease_seconds / 2
            try:
                worked = await self.run_once()
            except Exception:
                logger.exception("Worker loop error; retrying after a pause")
                worked = False
            if not worked:
                with anyio.move_on_after(self.poll_interval):
                    await stop.wait()
        logger.info("Worker %s stopped", self.worker_id)

    async def recover_stale(self) -> None:
        requeued, failed = await self.queue.recover_stale(self.lease_seconds)
        if requeued:
            logger.warning("Requeued %d job(s) with expired leases", requeued)
        if failed:
            logger.error(
                "Failed %d job(s) whose worker died on their final attempt", failed
            )

    async def _execute(self, job: ClaimedJob) -> None:
        logger.info(
            "Processing document %s (job %s, attempt %d)",
            job.document_id,
            job.id,
            job.attempts,
        )
        tracker = ProgressTracker()
        error: Exception | None = None

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(self._report_progress, job, tracker)
            try:
                await self.processor.process(job.document_id, tracker.update)
            except Exception as exc:
                error = exc
            task_group.cancel_scope.cancel()

        await self._finish(job, tracker, error)

    async def _finish(
        self, job: ClaimedJob, tracker: ProgressTracker, error: Exception | None
    ) -> None:
        """Record the job's outcome: success, a scheduled retry, or failure."""
        progress = tracker.snapshot()
        if error is None:
            await self.queue.complete(job.id, progress)
            logger.info("Job %s succeeded", job.id)
            return

        message = f"{type(error).__name__}: {error}"
        if is_retryable(error) and not job.is_last_attempt:
            delay = retry_delay(
                job.attempts,
                self.retry_base_seconds,
                self.retry_max_seconds,
                self.retry_jitter,
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
            await self.queue.retry_later(job.id, message, progress, retry_at)
            return

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
        await self.queue.fail(job.id, message, progress)

    async def _report_progress(self, job: ClaimedJob, tracker: ProgressTracker) -> None:
        """Persist progress when it changes, and heartbeat at least per lease/3."""
        written = 0
        last_write = time.monotonic()
        while True:
            await anyio.sleep(self.progress_interval)
            snapshot = tracker.snapshot()
            heartbeat_due = time.monotonic() - last_write >= self.lease_seconds / 3
            if snapshot.version == written and not heartbeat_due:
                continue
            try:
                await self.queue.report_progress(job.id, snapshot)
            except Exception:
                logger.exception("Could not report progress for job %s", job.id)
                continue
            written = snapshot.version
            last_write = time.monotonic()
            self._touch_heartbeat()

    def _touch_heartbeat(self) -> None:
        if self.heartbeat_file is not None:
            self.heartbeat_file.touch()
