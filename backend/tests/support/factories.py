"""Database factories and accessors shared by integration and E2E tests.

Each takes an ``async_sessionmaker`` and commits its own transaction, so
tests can mix them freely with the code under test.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.extraction.service import InvoiceExtractor
from app.models import (
    Document,
    DocumentChunk,
    DocumentPage,
    DocumentStatus,
    Invoice,
    ProcessingJob,
)
from app.repositories.document import DocumentRepository
from tests.support.fakes import FakeEmbedder, FakeInvoiceLLM, sample_invoice

Factory = async_sessionmaker[AsyncSession]


def ago(**delta: float) -> datetime:
    """An aware UTC timestamp ``delta`` in the past."""
    return datetime.now(UTC) - timedelta(**delta)


# ---------------------------------------------------------------- jobs


async def add_jobs(factory: Factory, count: int) -> list[uuid.UUID]:
    """Create documents with queued jobs, oldest first."""
    ids = []
    async with factory() as session:
        for i in range(count):
            document = Document(
                id=uuid.uuid4(),
                file_name=f"{i}.pdf",
                file_type="application/pdf",
                storage_path=f"{i}.pdf",
                status=DocumentStatus.UPLOADED,
            )
            job = ProcessingJob(
                id=uuid.uuid4(),
                document_id=document.id,
                created_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=i),
            )
            session.add_all([document, job])
            await session.flush()
            ids.append(job.id)
        await session.commit()
    return ids


async def get_job(factory: Factory, job_id: uuid.UUID) -> ProcessingJob:
    async with factory() as session:
        job = await session.get(ProcessingJob, job_id)
        assert job is not None
        return job


async def set_job(factory: Factory, job_id: uuid.UUID, **values: object) -> None:
    async with factory() as session, session.begin():
        await session.execute(
            update(ProcessingJob).where(ProcessingJob.id == job_id).values(**values)
        )


async def document_status(factory: Factory, job_id: uuid.UUID) -> str:
    async with factory() as session:
        job = await session.get(ProcessingJob, job_id)
        assert job is not None
        document = await session.get(Document, job.document_id)
        assert document is not None
        return document.status


# ------------------------------------------------- documents & invoices


async def add_document(
    factory: Factory, text: str = "Invoice INV-2026-001"
) -> uuid.UUID:
    document_id = uuid.uuid4()
    async with factory() as session:
        session.add(
            Document(
                id=document_id,
                file_name="inv.pdf",
                file_type="application/pdf",
                storage_path="x.pdf",
                status=DocumentStatus.PROCESSED,
            )
        )
        await session.flush()
        session.add(
            DocumentPage(
                document_id=document_id,
                page_number=1,
                text=text,
                char_count=len(text),
                extraction_method="text_layer",
                needs_ocr=False,
            )
        )
        await session.commit()
    return document_id


async def invoice_of(factory: Factory, document_id: uuid.UUID) -> Invoice:
    async with factory() as session:
        invoice = await DocumentRepository(session).get_invoice(document_id)
    assert invoice is not None
    return invoice


async def errors_of(
    factory: Factory, document_id: uuid.UUID
) -> list[tuple[str, str, dict[str, Any]]]:
    async with factory() as session:
        errors = await DocumentRepository(session).list_validation_errors(document_id)
    return [(e.code, e.severity, e.details) for e in errors]


async def extract(factory: Factory, document_id: uuid.UUID, **overrides: Any) -> None:
    llm = FakeInvoiceLLM(sample_invoice(**overrides))
    await InvoiceExtractor(factory, llm).extract(document_id)


# ---------------------------------------------------------------- search


async def add_indexed_document(
    factory: Factory, file_name: str, *texts: str, embedder: Any = None
) -> uuid.UUID:
    """A processed document whose pages ``texts`` are indexed for search
    (one chunk per page, embedded with FakeEmbedder unless given)."""
    embedder = embedder or FakeEmbedder()
    document_id = uuid.uuid4()
    async with factory() as session:
        session.add(
            Document(
                id=document_id,
                file_name=file_name,
                file_type="application/pdf",
                storage_path="x",
                status=DocumentStatus.PROCESSED,
            )
        )
        await session.flush()
        for index, text in enumerate(texts):
            session.add(
                DocumentChunk(
                    document_id=document_id,
                    chunk_index=index,
                    page_number=index + 1,
                    content=text,
                    embedding=embedder.embed_passages([text])[0],
                )
            )
        await session.commit()
    return document_id


async def seed_job_history(factory: Factory) -> dict[str, uuid.UUID]:
    """Three documents' jobs, created in this order:

    * ``failed``: document 0's first content job, failed with "boom";
    * ``running``: document 1, OCR at 2 of 5 pages;
    * ``queued``: document 2;
    * ``retried``: document 0's second content job, succeeded (its latest).
    """
    failed, running, queued = await add_jobs(factory, 3)
    await set_job(factory, failed, status="failed", attempts=3, error="boom")
    await set_job(
        factory,
        running,
        status="running",
        stage="ocr",
        progress_current=2,
        progress_total=5,
        attempts=1,
    )
    document = (await get_job(factory, failed)).document_id
    async with factory() as session:
        job = ProcessingJob(
            id=uuid.uuid4(),
            document_id=document,
            status="succeeded",
            attempts=1,
            created_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=5),
        )
        session.add(job)
        await session.commit()
        retried = job.id
    return {"failed": failed, "running": running, "queued": queued, "retried": retried}
