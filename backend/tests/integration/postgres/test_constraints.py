"""Database-enforced invariants: foreign keys, cascades, the one-active-job
rule, and transaction rollback."""

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
    Document,
    DocumentChunk,
    DocumentPage,
    DocumentSheet,
    DocumentValidationError,
    Invoice,
    InvoiceCorrection,
    InvoiceLineItem,
    JobStatus,
    ProcessingJob,
)
from app.repositories.document import DocumentRepository
from tests.support.factories import add_document, add_jobs, extract, set_job

Factory = async_sessionmaker[AsyncSession]
CHILD_TABLES = (
    DocumentPage,
    DocumentSheet,
    DocumentChunk,
    ProcessingJob,
    Invoice,
    InvoiceLineItem,
    DocumentValidationError,
    InvoiceCorrection,
)


async def _count(factory: Factory, model: type) -> int:
    async with factory() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def test_a_document_has_at_most_one_active_job(pg_factory: Factory) -> None:
    """The partial unique index rejects a second queued/running job, and
    allows a new one once the previous job has finished."""
    [job_id] = await add_jobs(pg_factory, 1)
    async with pg_factory() as session:
        document_id = (await session.get(ProcessingJob, job_id)).document_id  # type: ignore[union-attr]

    async with pg_factory() as session:
        session.add(ProcessingJob(document_id=document_id))
        with pytest.raises(IntegrityError, match="uq_processing_jobs_active_document"):
            await session.commit()

    await set_job(pg_factory, job_id, status=JobStatus.SUCCEEDED)
    async with pg_factory() as session:
        session.add(ProcessingJob(document_id=document_id))
        await session.commit()  # allowed: the earlier job is no longer active

    assert await _count(pg_factory, ProcessingJob) == 2


async def test_rows_cannot_reference_a_missing_document(pg_factory: Factory) -> None:
    async with pg_factory() as session:
        session.add(ProcessingJob(document_id=uuid.uuid4()))
        with pytest.raises(IntegrityError, match="foreign key"):
            await session.commit()


async def test_deleting_a_document_cascades_to_everything_derived(
    pg_factory: Factory,
) -> None:
    """Pages, sheets, chunks, jobs, invoice, line items, errors and
    corrections all go with their document (ON DELETE CASCADE)."""
    document_id = await add_document(pg_factory, "Invoice INV-2026-001")
    await extract(pg_factory, document_id, total_amount=1500.0)  # + validation error
    async with pg_factory() as session:
        session.add_all(
            [
                ProcessingJob(document_id=document_id, status=JobStatus.SUCCEEDED),
                DocumentSheet(
                    document_id=document_id,
                    sheet_number=1,
                    name="S",
                    row_count=1,
                    column_count=1,
                    rows=[["x"]],
                    is_hidden=False,
                    truncated=False,
                ),
                DocumentChunk(
                    document_id=document_id,
                    chunk_index=0,
                    page_number=1,
                    content="Invoice",
                    embedding=[0.0] * 383 + [1.0],
                ),
                InvoiceCorrection(document_id=document_id, changes={"x": [1, 2]}),
            ]
        )
        await session.commit()
    for model in CHILD_TABLES:
        assert await _count(pg_factory, model) >= 1, model.__name__

    async with pg_factory() as session:
        documents, busy = await DocumentRepository(session).delete_documents(
            [document_id]
        )

    assert ([d.id for d in documents], busy) == ([document_id], set())
    for model in (Document, *CHILD_TABLES):
        assert await _count(pg_factory, model) == 0, model.__name__


async def test_failed_write_rolls_back_and_leaves_the_session_usable(
    pg_factory: Factory,
) -> None:
    """A rejected job insert neither half-commits nor poisons the session."""
    [job_id] = await add_jobs(pg_factory, 1)
    async with pg_factory() as session:
        document_id = (await session.get(ProcessingJob, job_id)).document_id  # type: ignore[union-attr]
        repository = DocumentRepository(session)

        with pytest.raises(IntegrityError):
            await repository.add_job(ProcessingJob(document_id=document_id))

        assert await repository.get(document_id) is not None
    assert await _count(pg_factory, ProcessingJob) == 1
