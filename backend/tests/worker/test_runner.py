import uuid
from pathlib import Path

import anyio
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import JobStatus, ProcessingJob
from app.processing.progress import ProgressCallback, ignore_progress
from app.worker.queue import JobQueue
from app.worker.runner import Worker, default_worker_id
from tests.worker.test_queue import add_jobs, get_job

Factory = async_sessionmaker[AsyncSession]


class FakeProcessor:
    """Reports progress in steps, optionally failing at the end."""

    def __init__(self, steps: int = 3, fail: bool = False, delay: float = 0) -> None:
        self.steps = steps
        self.fail = fail
        self.delay = delay
        self.seen: list[uuid.UUID] = []
        self.observed: list[tuple[str | None, int, int | None]] = []
        self.factory: Factory | None = None
        self.job_id: uuid.UUID | None = None

    async def process(
        self, document_id: uuid.UUID, progress: ProgressCallback = ignore_progress
    ) -> None:
        self.seen.append(document_id)
        for step in range(1, self.steps + 1):
            progress("ocr", step, self.steps)
            await anyio.sleep(self.delay)
            if self.factory and self.job_id:
                job = await get_job(self.factory, self.job_id)
                self.observed.append(
                    (job.stage, job.progress_current, job.progress_total)
                )
        if self.fail:
            raise RuntimeError("tesseract exploded")


def make_worker(factory: Factory, processor: FakeProcessor, **kwargs: object) -> Worker:
    return Worker(
        JobQueue(factory),
        processor,  # type: ignore[arg-type]
        worker_id="test",
        progress_interval=0.01,
        poll_interval=0.01,
        **kwargs,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_run_until_idle_processes_every_job(session_factory: Factory) -> None:
    job_ids = await add_jobs(session_factory, 3)
    processor = FakeProcessor()

    assert await make_worker(session_factory, processor).run_until_idle() == 3

    for job_id in job_ids:
        job = await get_job(session_factory, job_id)
        assert job.status == JobStatus.SUCCEEDED
        assert (job.stage, job.progress_current, job.progress_total) == ("ocr", 3, 3)
    assert len(processor.seen) == 3


@pytest.mark.asyncio
async def test_progress_is_visible_while_running(session_factory: Factory) -> None:
    [job_id] = await add_jobs(session_factory, 1)
    processor = FakeProcessor(steps=3, delay=0.1)
    processor.factory, processor.job_id = session_factory, job_id

    await make_worker(session_factory, processor).run_until_idle()

    # Readers saw the job advance through the steps before it finished.
    assert processor.observed[-1] == ("ocr", 3, 3)
    assert [current for _, current, _ in processor.observed] == sorted(
        current for _, current, _ in processor.observed
    )
    assert any(current < 3 for _, current, _ in processor.observed)


@pytest.mark.asyncio
async def test_failing_job_is_retried_until_attempts_run_out(
    session_factory: Factory, caplog: pytest.LogCaptureFixture
) -> None:
    [job_id] = await add_jobs(session_factory, 1)
    processor = FakeProcessor(fail=True)
    # Zero backoff: each retry is due immediately, so run_until_idle keeps going.
    worker = make_worker(session_factory, processor, retry_base_seconds=0)

    assert await worker.run_until_idle() == 3

    job = await get_job(session_factory, job_id)
    assert job.status == JobStatus.FAILED
    assert (job.attempts, job.max_attempts) == (3, 3)
    assert job.error == "RuntimeError: tesseract exploded"
    assert job.finished_at is not None
    assert len(processor.seen) == 3
    assert "attempt 1/3 failed" in caplog.text
    assert "attempts exhausted, attempt 3/3" in caplog.text


@pytest.mark.asyncio
async def test_progress_write_errors_do_not_stop_the_job(
    session_factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    [job_id] = await add_jobs(session_factory, 1)
    worker = make_worker(session_factory, FakeProcessor(steps=2, delay=0.05))

    async def broken(*_: object) -> None:
        raise ConnectionError("db blip")

    monkeypatch.setattr(worker.queue, "report_progress", broken)
    await worker.run_until_idle()

    assert (await get_job(session_factory, job_id)).status == JobStatus.SUCCEEDED
    assert "Could not report progress" in caplog.text


@pytest.mark.asyncio
async def test_heartbeat_is_written_even_without_progress(
    session_factory: Factory,
) -> None:
    [job_id] = await add_jobs(session_factory, 1)
    processor = FakeProcessor(steps=1, delay=0.2)
    processor.factory, processor.job_id = session_factory, job_id
    # lease/3 = 0.03s, so the reporter heartbeats repeatedly during the step.
    worker = make_worker(session_factory, processor, lease_seconds=0.09)
    calls = 0
    original = worker.queue.report_progress

    async def counting(*args: object) -> None:
        nonlocal calls
        calls += 1
        await original(*args)  # type: ignore[arg-type]

    worker.queue.report_progress = counting  # type: ignore[method-assign]
    await worker.run_once()

    assert calls >= 3


@pytest.mark.asyncio
async def test_run_forever_stops_and_touches_heartbeat_file(
    session_factory: Factory, tmp_path: Path
) -> None:
    [job_id] = await add_jobs(session_factory, 1)
    heartbeat = tmp_path / "heartbeat"
    worker = make_worker(session_factory, FakeProcessor(), heartbeat_file=heartbeat)
    stop = anyio.Event()

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(worker.run_forever, stop)
        with anyio.fail_after(5):
            while (
                await get_job(session_factory, job_id)
            ).status != JobStatus.SUCCEEDED:
                await anyio.sleep(0.01)
        stop.set()

    assert heartbeat.exists()


@pytest.mark.asyncio
async def test_run_forever_survives_loop_errors(
    session_factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    worker = make_worker(session_factory, FakeProcessor())
    stop = anyio.Event()
    calls = 0

    async def flaky_claim(_: str) -> None:
        nonlocal calls
        calls += 1
        if calls >= 3:
            stop.set()
        raise ConnectionError("database restarting")

    monkeypatch.setattr(worker.queue, "claim", flaky_claim)
    with anyio.fail_after(5):
        await worker.run_forever(stop)

    assert calls >= 3
    assert "Worker loop error" in caplog.text


@pytest.mark.asyncio
async def test_stale_jobs_are_recovered_and_logged(
    session_factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    worker = make_worker(session_factory, FakeProcessor())

    async def counts(_: float) -> tuple[int, int]:
        return 2, 1

    monkeypatch.setattr(worker.queue, "recover_stale", counts)
    await worker.recover_stale()

    assert "Requeued 2 job(s)" in caplog.text
    assert "Failed 1 job(s) whose worker died" in caplog.text


def test_default_worker_id_is_unique() -> None:
    assert default_worker_id() != default_worker_id()


def test_job_model_defaults() -> None:
    job = ProcessingJob(document_id=uuid.uuid4())
    assert job.document_id is not None


@pytest.mark.asyncio
async def test_retry_is_scheduled_with_backoff(session_factory: Factory) -> None:
    [job_id] = await add_jobs(session_factory, 1)
    worker = make_worker(
        session_factory,
        FakeProcessor(fail=True),
        retry_base_seconds=60,
        retry_jitter=0,
    )

    # Only the first attempt runs; the retry is not due for a minute.
    assert await worker.run_until_idle() == 1

    job = await get_job(session_factory, job_id)
    assert job.status == JobStatus.QUEUED
    assert job.attempts == 1
    assert job.next_run_at is not None
    wait = job.next_run_at.replace(tzinfo=None) - job.updated_at.replace(tzinfo=None)
    assert 55 <= wait.total_seconds() <= 65


@pytest.mark.asyncio
async def test_permanent_errors_are_not_retried(
    session_factory: Factory, caplog: pytest.LogCaptureFixture
) -> None:
    [job_id] = await add_jobs(session_factory, 1)

    class MissingOriginal(FakeProcessor):
        async def process(
            self, document_id: uuid.UUID, progress: ProgressCallback = ignore_progress
        ) -> None:
            self.seen.append(document_id)
            raise FileNotFoundError("original is gone")

    processor = MissingOriginal()
    worker = make_worker(session_factory, processor, retry_base_seconds=0)

    assert await worker.run_until_idle() == 1

    job = await get_job(session_factory, job_id)
    assert (job.status, job.attempts) == (JobStatus.FAILED, 1)
    assert "permanent error" in caplog.text
