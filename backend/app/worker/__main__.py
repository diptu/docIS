"""
Run the processing worker: ``python -m app.worker``.

Stops on SIGTERM/SIGINT after finishing the job in progress.
"""

import logging
import signal

import anyio
import anyio.abc

from app.core.config import get_settings
from app.db.session import AsyncSessionLocal, close_db
from app.processing.ocr import OcrConfig
from app.processing.processor import DocumentProcessor
from app.storage.local import LocalStorage
from app.worker.queue import JobQueue
from app.worker.runner import Worker


def build_worker() -> Worker:
    settings = get_settings()
    processor = DocumentProcessor(
        session_factory=AsyncSessionLocal,
        storage=LocalStorage(settings.storage_dir),
        ocr_config=OcrConfig(
            languages=settings.ocr_languages,
            dpi=settings.ocr_dpi,
            timeout_seconds=settings.ocr_timeout_seconds,
        ),
    )
    return Worker(
        JobQueue(AsyncSessionLocal),
        processor,
        poll_interval=settings.worker_poll_interval_seconds,
        lease_seconds=settings.job_lease_seconds,
        progress_interval=settings.progress_interval_seconds,
        heartbeat_file=settings.worker_heartbeat_file,
        retry_base_seconds=settings.job_retry_base_seconds,
        retry_max_seconds=settings.job_retry_max_seconds,
    )


async def main() -> None:
    worker = build_worker()
    stop = anyio.Event()

    async def watch_signals(
        *, task_status: anyio.abc.TaskStatus[None] = anyio.TASK_STATUS_IGNORED
    ) -> None:
        with anyio.open_signal_receiver(signal.SIGTERM, signal.SIGINT) as signals:
            # Only report "started" once the handlers are installed, so a
            # signal arriving during startup cannot hard-kill the process.
            task_status.started()
            async for received in signals:
                logging.getLogger(__name__).info(
                    "Received %s; finishing current job", received.name
                )
                stop.set()
                return

    try:
        async with anyio.create_task_group() as task_group:
            await task_group.start(watch_signals)
            await worker.run_forever(stop)
            task_group.cancel_scope.cancel()
    finally:
        await close_db()


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    anyio.run(main)
