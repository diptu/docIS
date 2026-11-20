"""Celery tasks, run locally with ``Task.apply`` (no broker needed)."""

import uuid
from collections.abc import Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import JobStatus
from app.worker import tasks
from app.worker.celery_app import PROCESS_TASK, QUEUE, RECOVER_TASK, celery_app
from app.worker.runtime import Runtime
from tests.support.factories import add_jobs, ago, get_job, set_job
from tests.support.fakes import FakeProcessor

Factory = async_sessionmaker[AsyncSession]


def test_celery_configuration() -> None:
    conf = celery_app.conf
    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    assert conf.worker_prefetch_multiplier == 1
    assert conf.task_default_queue == QUEUE
    assert conf.broker_transport_options["visibility_timeout"] > conf.task_time_limit
    assert conf.beat_schedule["recover-processing-jobs"]["task"] == RECOVER_TASK
    assert {PROCESS_TASK, RECOVER_TASK} <= set(celery_app.tasks)


@pytest.fixture
def use_runtime(monkeypatch: pytest.MonkeyPatch) -> Callable[[Runtime], None]:
    return lambda runtime: monkeypatch.setattr(tasks, "get_runtime", lambda: runtime)


async def test_process_task_succeeds(
    session_factory: Factory,
    make_runtime: Callable[..., Runtime],
    use_runtime: Callable[[Runtime], None],
) -> None:
    [job_id] = await add_jobs(session_factory, 1)
    use_runtime(make_runtime(FakeProcessor()))

    result = await _apply(tasks.process_document, str(job_id))

    assert result == "succeeded"
    assert (await get_job(session_factory, job_id)).status == JobStatus.SUCCEEDED


async def test_process_task_retries_via_celery_until_exhausted(
    session_factory: Factory,
    make_runtime: Callable[..., Runtime],
    use_runtime: Callable[[Runtime], None],
) -> None:
    """Celery's retry re-runs the task; the job row limits the attempts."""
    [job_id] = await add_jobs(session_factory, 1)
    processor = FakeProcessor(error=RuntimeError("transient"))
    use_runtime(make_runtime(processor))

    await _apply(tasks.process_document, str(job_id))

    job = await get_job(session_factory, job_id)
    assert (job.status, job.attempts) == (JobStatus.FAILED, 3)
    assert len(processor.seen) == 3


async def test_recover_task_dispatches_recovered_jobs(
    session_factory: Factory,
    make_runtime: Callable[..., Runtime],
    use_runtime: Callable[[Runtime], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    [job_id] = await add_jobs(session_factory, 1)
    await set_job(session_factory, job_id, updated_at=ago(minutes=20))
    use_runtime(make_runtime(FakeProcessor()))
    sent: list[uuid.UUID] = []
    monkeypatch.setattr(tasks, "dispatch_jobs", lambda ids: sent.extend(ids))

    assert await _apply(tasks.recover_jobs) == 1
    assert sent == [job_id]


def test_dispatch_publishes_one_task_per_job(monkeypatch: pytest.MonkeyPatch) -> None:
    published: list[tuple[object, ...]] = []

    def fake_apply_async(args: tuple[object, ...], **options: object) -> None:
        assert options["retry"] is True
        published.append(args)

    monkeypatch.setattr(tasks.process_document, "apply_async", fake_apply_async)
    ids = [uuid.uuid4(), uuid.uuid4()]

    tasks.dispatch_jobs(ids)

    assert published == [(str(ids[0]),), (str(ids[1]),)]


def test_dispatch_never_raises_when_the_broker_is_down(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broker_down(*_: object, **__: object) -> None:
        raise ConnectionError("Redis unavailable")

    monkeypatch.setattr(tasks.process_document, "apply_async", broker_down)

    tasks.dispatch_jobs([uuid.uuid4()])

    assert "will be recovered later" in caplog.text


async def _apply(task: object, *args: object) -> object:
    """Run a Celery task locally in a worker thread (it calls asyncio.run)."""
    from anyio import to_thread

    return await to_thread.run_sync(lambda: task.apply(args=args).get())  # type: ignore[attr-defined]


def test_settle_dispatches_follow_up_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.worker.jobs import JobOutcome

    sent: list[uuid.UUID] = []
    monkeypatch.setattr(tasks, "dispatch_jobs", lambda ids: sent.extend(ids))
    follow_up = uuid.uuid4()

    status = tasks._settle(
        tasks.process_document, JobOutcome("succeeded", follow_up=follow_up)
    )

    assert (status, sent) == ("succeeded", [follow_up])
