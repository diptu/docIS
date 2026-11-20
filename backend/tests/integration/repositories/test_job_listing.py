"""DocumentRepository job listing on SQLite: the job monitor query and a
document's job history."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import JobKind
from app.repositories.document import DocumentRepository
from tests.support.factories import get_job, seed_job_history

Factory = async_sessionmaker[AsyncSession]


async def test_all_jobs_newest_first_with_document_and_latest_flag(
    session_factory: Factory,
) -> None:
    jobs = await seed_job_history(session_factory)

    async with session_factory() as session:
        entries, total, counts = await DocumentRepository(session).list_all_jobs(0, 50)

    assert [e.job.id for e in entries] == [
        jobs["retried"],
        jobs["queued"],
        jobs["running"],
        jobs["failed"],
    ]
    assert [e.is_latest for e in entries] == [True, True, True, False]
    assert [e.file_name for e in entries] == ["0.pdf", "2.pdf", "1.pdf", "0.pdf"]
    assert total == 4
    assert counts == {"succeeded": 1, "queued": 1, "running": 1, "failed": 1}


async def test_status_filter_pages_but_counts_cover_every_status(
    session_factory: Factory,
) -> None:
    jobs = await seed_job_history(session_factory)

    async with session_factory() as session:
        repository = DocumentRepository(session)
        failed, total, counts = await repository.list_all_jobs(0, 50, status="failed")
        page, paged_total, _ = await repository.list_all_jobs(1, 2)

    assert ([e.job.id for e in failed], total) == ([jobs["failed"]], 1)
    assert sum(counts.values()) == 4
    assert [e.job.id for e in page] == [jobs["queued"], jobs["running"]]
    assert paged_total == 4


async def test_latest_only_hides_runs_that_were_retried(
    session_factory: Factory,
) -> None:
    jobs = await seed_job_history(session_factory)

    async with session_factory() as session:
        repository = DocumentRepository(session)
        entries, total, counts = await repository.list_all_jobs(0, 50, latest_only=True)
        failed, failed_total, _ = await repository.list_all_jobs(
            0, 50, status="failed", latest_only=True
        )

    assert jobs["failed"] not in {e.job.id for e in entries}
    assert total == 3
    assert counts == {"succeeded": 1, "queued": 1, "running": 1}
    assert (failed, failed_total) == ([], 0)


async def test_kind_filter_applies_to_counts(session_factory: Factory) -> None:
    await seed_job_history(session_factory)

    async with session_factory() as session:
        entries, total, counts = await DocumentRepository(session).list_all_jobs(
            0, 50, kind=JobKind.EXTRACT_INVOICE
        )

    assert (entries, total, counts) == ([], 0, {})


async def test_document_history_lists_every_job_newest_first(
    session_factory: Factory,
) -> None:
    jobs = await seed_job_history(session_factory)
    document = (await get_job(session_factory, jobs["failed"])).document_id

    async with session_factory() as session:
        history = await DocumentRepository(session).list_jobs(document)

    assert [job.id for job in history] == [jobs["retried"], jobs["failed"]]
    assert history[1].error == "boom"
