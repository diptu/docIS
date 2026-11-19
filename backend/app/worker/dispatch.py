"""
Sending queued jobs to Celery from the API.
"""

import uuid
from collections.abc import Awaitable, Callable, Sequence

from anyio import to_thread

JobDispatcher = Callable[[Sequence[uuid.UUID]], Awaitable[None]]


async def celery_dispatcher(job_ids: Sequence[uuid.UUID]) -> None:
    """Publish tasks for committed jobs without blocking the event loop."""
    if not job_ids:
        return
    from app.worker.tasks import dispatch_jobs

    await to_thread.run_sync(dispatch_jobs, list(job_ids))


async def no_dispatch(job_ids: Sequence[uuid.UUID]) -> None:
    """Dispatcher that does nothing (jobs wait for recovery)."""
