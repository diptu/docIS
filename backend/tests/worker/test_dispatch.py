import uuid

import pytest

from app.worker import dispatch, tasks


@pytest.mark.asyncio
async def test_celery_dispatcher_publishes_in_a_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[uuid.UUID] = []
    monkeypatch.setattr(tasks, "dispatch_jobs", lambda ids: sent.extend(ids))
    job_ids = [uuid.uuid4()]

    await dispatch.celery_dispatcher(job_ids)
    await dispatch.celery_dispatcher([])  # nothing to send

    assert sent == job_ids


@pytest.mark.asyncio
async def test_no_dispatch_does_nothing() -> None:
    await dispatch.no_dispatch([uuid.uuid4()])
