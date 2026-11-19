"""
Persistence operations for documents.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentStatus
from app.models.document_page import DocumentPage
from app.models.document_sheet import DocumentSheet
from app.models.processing_job import ProcessingJob


class DocumentRepository:
    """Data access for documents and their extracted pages and sheets."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_all(
        self,
        documents: Sequence[Document],
        jobs: Sequence[ProcessingJob] = (),
    ) -> list[Document]:
        """Insert documents and their processing jobs in one transaction.

        Committing both together guarantees every stored document that
        needs processing has a queued job.
        """
        self.session.add_all(documents)
        self.session.add_all(jobs)
        await self.session.commit()
        for document in documents:
            await self.session.refresh(document)
        return list(documents)

    async def get(self, document_id: uuid.UUID) -> Document | None:
        """Return a document by ID, or None."""
        return await self.session.get(Document, document_id)

    async def set_status(self, document_id: uuid.UUID, status: DocumentStatus) -> None:
        """Update a document's status and commit."""
        await self.session.execute(
            update(Document).where(Document.id == document_id).values(status=status)
        )
        await self.session.commit()

    async def replace_pages(
        self, document_id: uuid.UUID, pages: Sequence[DocumentPage]
    ) -> None:
        """Replace all extracted pages of a document (without committing).

        Deleting first makes reprocessing a document idempotent.
        """
        await self.session.execute(
            delete(DocumentPage).where(DocumentPage.document_id == document_id)
        )
        self.session.add_all(pages)

    async def list_pages(self, document_id: uuid.UUID) -> list[DocumentPage]:
        """Return a document's pages in page order."""
        result = await self.session.scalars(
            select(DocumentPage)
            .where(DocumentPage.document_id == document_id)
            .order_by(DocumentPage.page_number)
        )
        return list(result)

    async def replace_sheets(
        self, document_id: uuid.UUID, sheets: Sequence[DocumentSheet]
    ) -> None:
        """Replace all parsed sheets of a document (without committing)."""
        await self.session.execute(
            delete(DocumentSheet).where(DocumentSheet.document_id == document_id)
        )
        self.session.add_all(sheets)

    async def list_sheets(self, document_id: uuid.UUID) -> list[DocumentSheet]:
        """Return a document's sheets in workbook order."""
        result = await self.session.scalars(
            select(DocumentSheet)
            .where(DocumentSheet.document_id == document_id)
            .order_by(DocumentSheet.sheet_number)
        )
        return list(result)

    async def get_sheet(
        self, document_id: uuid.UUID, sheet_number: int
    ) -> DocumentSheet | None:
        """Return one sheet of a document, or None."""
        return await self.session.scalar(
            select(DocumentSheet).where(
                DocumentSheet.document_id == document_id,
                DocumentSheet.sheet_number == sheet_number,
            )
        )

    async def latest_job(self, document_id: uuid.UUID) -> ProcessingJob | None:
        """Return the most recent processing job of a document, or None."""
        return await self.session.scalar(
            select(ProcessingJob)
            .where(ProcessingJob.document_id == document_id)
            .order_by(ProcessingJob.created_at.desc(), ProcessingJob.id.desc())
            .limit(1)
        )

    async def add_job(self, job: ProcessingJob) -> ProcessingJob:
        """Queue a job and reset its document to ``uploaded``, atomically."""
        try:
            self.session.add(job)
            await self.session.execute(
                update(Document)
                .where(Document.id == job.document_id)
                .values(status=DocumentStatus.UPLOADED)
            )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        await self.session.refresh(job)
        return job
