import uuid
from collections.abc import Callable

import anyio
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import JobStatus
from app.processing.progress import ProgressCallback, ignore_progress
from app.worker.jobs import execute_job, recover
from app.worker.runtime import Runtime
from tests.conftest import run_due_jobs
from tests.worker.test_queue import add_jobs, ago, get_job, set_job

Factory = async_sessionmaker[AsyncSession]
MakeRuntime = Callable[..., Runtime]


class FakeProcessor:
    """Reports progress in steps; optionally fails."""

    def __init__(
        self,
        steps: int = 3,
        delay: float = 0,
        error: Exception | None = None,
    ) -> None:
        self.steps, self.delay, self.error = steps, delay, error
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
        if self.error:
            raise self.error


@pytest.mark.asyncio
async def test_successful_job(
    session_factory: Factory, make_runtime: MakeRuntime
) -> None:
    [job_id] = await add_jobs(session_factory, 1)

    outcome = await execute_job(make_runtime(FakeProcessor()), job_id, "w")

    assert (outcome.status, outcome.retry_in) == ("succeeded", None)
    job = await get_job(session_factory, job_id)
    assert job.status == JobStatus.SUCCEEDED
    assert (job.stage, job.progress_current, job.progress_total) == ("ocr", 3, 3)


@pytest.mark.asyncio
async def test_unclaimable_job_is_skipped(
    session_factory: Factory, make_runtime: MakeRuntime
) -> None:
    processor = FakeProcessor()

    outcome = await execute_job(make_runtime(processor), uuid.uuid4(), "w")

    assert outcome.status == "skipped"
    assert processor.seen == []


@pytest.mark.asyncio
async def test_progress_is_visible_while_running(
    session_factory: Factory, make_runtime: MakeRuntime
) -> None:
    [job_id] = await add_jobs(session_factory, 1)
    processor = FakeProcessor(steps=3, delay=0.1)
    processor.factory, processor.job_id = session_factory, job_id

    await execute_job(make_runtime(processor), job_id, "w")

    currents = [current for _, current, _ in processor.observed]
    assert processor.observed[-1] == ("ocr", 3, 3)
    assert currents == sorted(currents) and min(currents) < 3


@pytest.mark.asyncio
async def test_retryable_failure_schedules_a_retry(
    session_factory: Factory, make_runtime: MakeRuntime
) -> None:
    [job_id] = await add_jobs(session_factory, 1)
    runtime = make_runtime(
        FakeProcessor(error=RuntimeError("ocr crashed")), retry_base_seconds=60
    )

    outcome = await execute_job(runtime, job_id, "w")

    assert (outcome.status, outcome.retry_in) == ("retrying", 60)
    job = await get_job(session_factory, job_id)
    assert (job.status, job.attempts, job.error) == (
        JobStatus.QUEUED,
        1,
        "RuntimeError: ocr crashed",
    )
    assert job.next_run_at is not None


@pytest.mark.asyncio
async def test_retries_until_attempts_run_out(
    session_factory: Factory,
    make_runtime: MakeRuntime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    [job_id] = await add_jobs(session_factory, 1)
    processor = FakeProcessor(error=RuntimeError("ocr crashed"))

    assert await run_due_jobs(make_runtime(processor), session_factory) == 3

    job = await get_job(session_factory, job_id)
    assert (job.status, job.attempts) == (JobStatus.FAILED, 3)
    assert len(processor.seen) == 3
    assert "attempts exhausted, attempt 3/3" in caplog.text


@pytest.mark.asyncio
async def test_permanent_error_is_not_retried(
    session_factory: Factory,
    make_runtime: MakeRuntime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    [job_id] = await add_jobs(session_factory, 1)
    runtime = make_runtime(FakeProcessor(error=FileNotFoundError("original is gone")))

    outcome = await execute_job(runtime, job_id, "w")

    assert outcome.status == "failed"
    assert (await get_job(session_factory, job_id)).attempts == 1
    assert "permanent error" in caplog.text


@pytest.mark.asyncio
async def test_progress_write_errors_do_not_stop_the_job(
    session_factory: Factory,
    make_runtime: MakeRuntime,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    [job_id] = await add_jobs(session_factory, 1)
    runtime = make_runtime(FakeProcessor(steps=2, delay=0.05))

    async def broken(*_: object) -> None:
        raise ConnectionError("db blip")

    monkeypatch.setattr(runtime.queue, "report_progress", broken)
    await execute_job(runtime, job_id, "w")

    assert (await get_job(session_factory, job_id)).status == JobStatus.SUCCEEDED
    assert "Could not report progress" in caplog.text


@pytest.mark.asyncio
async def test_heartbeat_is_written_even_without_progress(
    session_factory: Factory, make_runtime: MakeRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    [job_id] = await add_jobs(session_factory, 1)
    # lease/3 = 0.03s, so the reporter heartbeats repeatedly during the step.
    runtime = make_runtime(FakeProcessor(steps=1, delay=0.2), lease_seconds=0.09)
    calls = 0
    original = runtime.queue.report_progress

    async def counting(*args: object) -> None:
        nonlocal calls
        calls += 1
        await original(*args)  # type: ignore[arg-type]

    monkeypatch.setattr(runtime.queue, "report_progress", counting)
    await execute_job(runtime, job_id, "w")

    assert calls >= 3


@pytest.mark.asyncio
async def test_recover_collects_jobs_to_dispatch(
    session_factory: Factory,
    make_runtime: MakeRuntime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime = make_runtime(FakeProcessor())
    stale, final, undelivered = await add_jobs(session_factory, 3)
    for job_id in (stale, final):
        await runtime.queue.claim(job_id, "dead", 300)
    await set_job(session_factory, stale, heartbeat_at=ago(minutes=10))
    await set_job(session_factory, final, heartbeat_at=ago(minutes=10), attempts=3)
    await set_job(session_factory, undelivered, updated_at=ago(minutes=20))

    assert sorted(await recover(runtime)) == sorted([stale, undelivered])

    assert "Requeued 1 job(s)" in caplog.text
    assert "Failed 1 job(s)" in caplog.text
    assert "Re-dispatching 1 queued job(s)" in caplog.text
    assert await recover(runtime) == []


# ------------------------------------------------------------ job kinds


async def _kinds(factory: Factory) -> list[tuple[str, str]]:
    from sqlalchemy import select

    from app.models import ProcessingJob

    async with factory() as session:
        rows = await session.execute(
            select(ProcessingJob.kind, ProcessingJob.status).order_by(
                ProcessingJob.created_at
            )
        )
        return [(kind, status) for kind, status in rows.all()]


@pytest.mark.asyncio
async def test_content_success_queues_invoice_extraction(
    session_factory: Factory, make_runtime: MakeRuntime
) -> None:
    from tests.fakes import FakeInvoiceLLM

    [job_id] = await add_jobs(session_factory, 1)
    runtime = make_runtime(FakeProcessor(), invoice_llm=FakeInvoiceLLM())

    outcome = await execute_job(runtime, job_id, "w")

    assert outcome.follow_up is not None
    assert await _kinds(session_factory) == [
        ("extract_content", "succeeded"),
        ("extract_invoice", "queued"),
    ]
    follow_up = await get_job(session_factory, outcome.follow_up)
    assert follow_up.max_attempts == 3


@pytest.mark.asyncio
async def test_no_follow_up_when_extraction_is_disabled(
    session_factory: Factory, make_runtime: MakeRuntime
) -> None:
    [job_id] = await add_jobs(session_factory, 1)

    outcome = await execute_job(make_runtime(FakeProcessor()), job_id, "w")

    assert outcome.follow_up is None
    assert await _kinds(session_factory) == [("extract_content", "succeeded")]


@pytest.mark.asyncio
async def test_invoice_job_runs_the_extractor(
    session_factory: Factory, make_runtime: MakeRuntime
) -> None:
    [job_id] = await add_jobs(session_factory, 1)
    await set_job(session_factory, job_id, kind="extract_invoice")
    calls: list[uuid.UUID] = []

    class RecordingExtractor:
        async def extract(
            self, document_id: uuid.UUID, progress: ProgressCallback = ignore_progress
        ) -> None:
            calls.append(document_id)

    runtime = make_runtime(FakeProcessor(), invoice_extractor=RecordingExtractor())
    outcome = await execute_job(runtime, job_id, "w")

    assert (outcome.status, outcome.follow_up) == ("succeeded", None)
    assert calls == [(await get_job(session_factory, job_id)).document_id]


@pytest.mark.asyncio
async def test_invoice_job_fails_cleanly_when_extraction_is_disabled(
    session_factory: Factory, make_runtime: MakeRuntime
) -> None:
    [job_id] = await add_jobs(session_factory, 1)
    await set_job(session_factory, job_id, kind="extract_invoice")

    outcome = await execute_job(make_runtime(FakeProcessor()), job_id, "w")

    assert outcome.status == "failed"
    assert "disabled" in ((await get_job(session_factory, job_id)).error or "")


@pytest.mark.asyncio
async def test_unknown_job_kind_fails(
    session_factory: Factory, make_runtime: MakeRuntime
) -> None:
    [job_id] = await add_jobs(session_factory, 1)
    await set_job(session_factory, job_id, kind="mystery")

    assert (await execute_job(make_runtime(FakeProcessor()), job_id, "w")).status == (
        "failed"
    )


@pytest.mark.asyncio
async def test_failed_invoice_job_does_not_fail_the_document(
    session_factory: Factory, make_runtime: MakeRuntime
) -> None:
    from app.extraction.llm import ExtractionRefusedError
    from tests.fakes import FakeInvoiceLLM
    from tests.worker.test_queue import document_status

    [job_id] = await add_jobs(session_factory, 1)
    await set_job(session_factory, job_id, kind="extract_invoice")
    llm = FakeInvoiceLLM(error=ExtractionRefusedError("declined"))

    outcome = await execute_job(
        make_runtime(FakeProcessor(), invoice_llm=llm), job_id, "w"
    )

    assert outcome.status == "failed"
    assert await document_status(session_factory, job_id) == "uploaded"


@pytest.mark.asyncio
async def test_job_leaves_no_background_tasks_behind(
    session_factory: Factory, make_runtime: MakeRuntime
) -> None:
    """Regression: cancelling the progress reporter mid-write left a SQLAlchemy
    connection-termination task pending, which could hang asyncio.run() at
    shutdown (the Celery task and the tests both use asyncio.run)."""
    import asyncio

    job_ids = await add_jobs(session_factory, 5)
    # A tiny interval keeps the reporter writing constantly, so the job's end
    # is very likely to coincide with an in-flight write.
    runtime = make_runtime(FakeProcessor(steps=5, delay=0.002), progress_interval=0.001)
    before = asyncio.all_tasks()

    for job_id in job_ids:
        await execute_job(runtime, job_id, "w")
    await asyncio.sleep(0)

    leftover = asyncio.all_tasks() - before - {asyncio.current_task()}
    assert leftover == set(), [task.get_coro() for task in leftover]
