"""
Persistence operations for documents.
"""

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.extraction.review import (
    POSSIBLE_DUPLICATE_MESSAGE,
    DuplicateMatch,
    ErrorSpec,
    ReasonCode,
    duplicate_code,
    duplicate_severity,
)
from app.models.document import Document, DocumentStatus
from app.models.document_chunk import DocumentChunk
from app.models.document_page import DocumentPage
from app.models.document_sheet import DocumentSheet
from app.models.invoice import DocumentValidationError, Invoice, InvoiceCorrection
from app.models.processing_job import JobKind, JobStatus, ProcessingJob


@dataclass(frozen=True)
class DocumentStats:
    """Document counts by processing outcome, and average processing time."""

    total: int
    completed: int
    failed: int
    in_progress: int
    avg_processing_seconds: float | None


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

    async def replace_chunks(
        self, document_id: uuid.UUID, chunks: Sequence[DocumentChunk]
    ) -> None:
        """Replace a document's search chunks (without committing)."""
        await self.session.execute(
            delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
        )
        self.session.add_all(chunks)

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
        """Queue a job; a content job also resets its document to ``uploaded``."""
        try:
            self.session.add(job)
            if job.kind == JobKind.EXTRACT_CONTENT:
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

    async def stats(self) -> DocumentStats:
        """Counts by each document's latest job, and the average time spent
        processing a completed document (queue waits excluded)."""
        total = await self.session.scalar(select(func.count()).select_from(Document))
        latest = select(
            ProcessingJob.document_id,
            ProcessingJob.status,
            func.row_number()
            .over(
                partition_by=ProcessingJob.document_id,
                order_by=(ProcessingJob.created_at.desc(), ProcessingJob.id.desc()),
            )
            .label("rank"),
        ).subquery()
        latest_only = latest.c.rank == 1
        by_status: dict[str, int] = {
            status: int(count)
            for status, count in (
                await self.session.execute(
                    select(latest.c.status, func.count())
                    .where(latest_only)
                    .group_by(latest.c.status)
                )
            ).all()
        }

        completed = select(latest.c.document_id).where(
            latest_only, latest.c.status == JobStatus.SUCCEEDED
        )
        # Every successful job of a completed document (content, then
        # invoice extraction) adds to its processing time.
        seconds, timed = (
            await self.session.execute(
                select(
                    func.sum(
                        self._seconds_between(
                            ProcessingJob.started_at, ProcessingJob.finished_at
                        )
                    ),
                    func.count(func.distinct(ProcessingJob.document_id)),
                ).where(
                    ProcessingJob.document_id.in_(completed),
                    ProcessingJob.status == JobStatus.SUCCEEDED,
                    ProcessingJob.started_at.is_not(None),
                    ProcessingJob.finished_at.is_not(None),
                )
            )
        ).one()
        return DocumentStats(
            total=int(total or 0),
            completed=by_status.get(JobStatus.SUCCEEDED, 0),
            failed=by_status.get(JobStatus.FAILED, 0),
            in_progress=by_status.get(JobStatus.QUEUED, 0)
            + by_status.get(JobStatus.RUNNING, 0),
            avg_processing_seconds=float(seconds) / timed if timed else None,
        )

    def _seconds_between(
        self,
        start: InstrumentedAttribute[dt.datetime | None],
        end: InstrumentedAttribute[dt.datetime | None],
    ) -> ColumnElement[Any]:
        """``end - start`` in seconds, in the session's SQL dialect."""
        if self.session.get_bind().dialect.name == "sqlite":
            return (func.julianday(end) - func.julianday(start)) * 86400.0
        return func.extract("epoch", end - start)

    async def delete_documents(
        self, document_ids: Sequence[uuid.UUID]
    ) -> tuple[list[Document], set[uuid.UUID]]:
        """Delete documents (pages, sheets, chunks, jobs and invoices cascade).

        Documents with a running job are kept, since a worker is writing
        their results. Their jobs are locked first, so a worker cannot claim
        one between this check and the delete. Duplicate flags on other
        documents that pointed only at deleted documents are resolved.

        Returns the deleted documents (to remove their files) and the IDs
        kept because they are being processed. Unknown IDs are in neither.
        """
        ids = list(dict.fromkeys(document_ids))
        try:
            jobs = await self.session.execute(
                select(ProcessingJob.document_id, ProcessingJob.status)
                .where(ProcessingJob.document_id.in_(ids))
                .with_for_update()
            )
            busy = {
                document_id
                for document_id, status in jobs.all()
                if status == JobStatus.RUNNING
            }
            documents = list(
                await self.session.scalars(
                    select(Document).where(
                        Document.id.in_(ids), Document.id.not_in(busy)
                    )
                )
            )
            deleted = [document.id for document in documents]
            if deleted:
                await self.session.execute(
                    delete(Document).where(Document.id.in_(deleted))
                )
                await self._drop_duplicate_references(deleted)
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        return documents, busy

    async def _drop_duplicate_references(self, deleted: Sequence[uuid.UUID]) -> None:
        """Remove deleted documents from other documents' duplicate errors;
        an error left without any duplicate is resolved."""
        removed = {str(document_id) for document_id in deleted}
        errors = await self.session.scalars(
            select(DocumentValidationError).where(
                DocumentValidationError.code.in_(
                    [ReasonCode.DUPLICATE, ReasonCode.POSSIBLE_DUPLICATE]
                ),
                DocumentValidationError.resolved.is_(False),
            )
        )
        for error in errors:
            ids = list(error.details.get("document_ids", []))
            remaining = [i for i in ids if i not in removed]
            if remaining == ids:
                continue
            # Reassign (not mutate) so the JSON change is detected.
            error.details = {**error.details, "document_ids": remaining}
            if not remaining:
                error.resolved = True
                error.resolved_at = dt.datetime.now(dt.UTC)

    # ---------------------------------------------------------------- invoices

    async def save_invoice(
        self,
        invoice: Invoice,
        errors: Sequence[ErrorSpec],
        flag_documents: Sequence[tuple[uuid.UUID, DuplicateMatch]] = (),
        correction: InvoiceCorrection | None = None,
    ) -> None:
        """Store a document's invoice and validation errors, replacing any
        earlier extraction (its line items and errors, resolved or not).

        ``flag_documents`` are earlier documents this one duplicates; each
        gets (or extends) an unresolved duplicate error in the same
        transaction, so both copies show up for review. ``correction`` is a
        manual-correction audit record saved with the change.
        """
        document_id = invoice.document_id
        await self.session.execute(
            delete(Invoice).where(Invoice.document_id == document_id)
        )
        await self.session.execute(
            delete(DocumentValidationError).where(
                DocumentValidationError.document_id == document_id
            )
        )
        self.session.add(invoice)
        self.session.add_all(
            DocumentValidationError(
                document_id=document_id,
                code=str(error.code),
                severity=error.severity,
                message=error.message,
                details=error.details,
            )
            for error in errors
        )
        if correction is not None:
            self.session.add(correction)
        for other_id, match in flag_documents:
            await self._flag_duplicate(other_id, document_id, match)
        await self.session.commit()

    async def _flag_duplicate(
        self, document_id: uuid.UUID, duplicate_id: uuid.UUID, match: DuplicateMatch
    ) -> None:
        code = duplicate_code(match)
        existing = await self.session.scalar(
            select(DocumentValidationError).where(
                DocumentValidationError.document_id == document_id,
                DocumentValidationError.code == code,
                DocumentValidationError.resolved.is_(False),
            )
        )
        if existing is not None:
            ids = list(existing.details.get("document_ids", []))
            if str(duplicate_id) not in ids:
                ids.append(str(duplicate_id))
            # Reassign (not mutate) so the JSON change is detected.
            existing.details = {**existing.details, "document_ids": ids}
            return
        self.session.add(
            DocumentValidationError(
                document_id=document_id,
                code=code,
                severity=duplicate_severity(match),
                message=(
                    POSSIBLE_DUPLICATE_MESSAGE
                    if code == ReasonCode.POSSIBLE_DUPLICATE
                    else "This invoice was uploaded again as another document."
                ),
                details={"document_ids": [str(duplicate_id)]},
            )
        )

    async def get_invoice(self, document_id: uuid.UUID) -> Invoice | None:
        """Return a document's invoice (with line items), or None."""
        return await self.session.scalar(
            select(Invoice).where(Invoice.document_id == document_id)
        )

    async def find_duplicates(
        self,
        document_id: uuid.UUID,
        sha256: str | None,
        number_key: str | None,
        vendor_key: str | None,
        invoice_date: dt.date | None,
        total_amount: Decimal | None,
    ) -> list[tuple[uuid.UUID, DuplicateMatch]]:
        """Other documents that duplicate this one, strongest match first.

        * same file: identical SHA-256 of the stored original;
        * same invoice number: same vendor and normalised invoice number;
        * possible: same vendor, invoice date and total, other number.
        """
        found: dict[uuid.UUID, DuplicateMatch] = {}
        if sha256:
            same_file = await self.session.scalars(
                select(Document.id).where(
                    Document.sha256 == sha256, Document.id != document_id
                )
            )
            for other in same_file:
                found.setdefault(other, DuplicateMatch.SAME_FILE)

        if vendor_key:
            conditions = []
            if number_key:
                conditions.append(Invoice.invoice_number_key == number_key)
            if invoice_date is not None and total_amount is not None:
                conditions.append(
                    and_(
                        Invoice.invoice_date == invoice_date,
                        Invoice.total_amount == total_amount,
                    )
                )
            if conditions:
                rows = await self.session.execute(
                    select(Invoice.document_id, Invoice.invoice_number_key).where(
                        Invoice.document_id != document_id,
                        Invoice.is_invoice.is_(True),
                        Invoice.vendor_key == vendor_key,
                        or_(*conditions),
                    )
                )
                for other, other_number in rows.all():
                    match = (
                        DuplicateMatch.SAME_INVOICE_NUMBER
                        if number_key and other_number == number_key
                        else DuplicateMatch.POSSIBLE
                    )
                    found.setdefault(other, match)

        order = list(DuplicateMatch)
        return sorted(
            found.items(), key=lambda item: (order.index(item[1]), str(item[0]))
        )

    # ------------------------------------------------------- validation errors

    async def list_validation_errors(
        self, document_id: uuid.UUID
    ) -> list[DocumentValidationError]:
        """A document's validation errors: unresolved first, errors before
        warnings, then oldest first (by code when raised together)."""
        result = await self.session.scalars(
            select(DocumentValidationError)
            .where(DocumentValidationError.document_id == document_id)
            .order_by(
                DocumentValidationError.resolved,
                DocumentValidationError.severity,
                DocumentValidationError.created_at,
                # Errors raised together share a timestamp: order by code.
                DocumentValidationError.code,
                DocumentValidationError.id,
            )
        )
        return list(result)

    async def get_validation_error(
        self, document_id: uuid.UUID, error_id: uuid.UUID
    ) -> DocumentValidationError | None:
        return await self.session.scalar(
            select(DocumentValidationError).where(
                DocumentValidationError.id == error_id,
                DocumentValidationError.document_id == document_id,
            )
        )

    async def set_resolved(
        self, error: DocumentValidationError, resolved: bool
    ) -> DocumentValidationError:
        """Mark a validation error resolved (stamping the time) or reopen it."""
        error.resolved = resolved
        error.resolved_at = dt.datetime.now(dt.UTC) if resolved else None
        await self.session.commit()
        await self.session.refresh(error)
        return error

    async def list_for_review(
        self, offset: int, limit: int
    ) -> list[tuple[Invoice, Document, list[DocumentValidationError]]]:
        """Invoices with unresolved validation errors, newest first."""
        unresolved = (
            select(DocumentValidationError.document_id)
            .where(DocumentValidationError.resolved.is_(False))
            .distinct()
        )
        rows = (
            await self.session.execute(
                select(Invoice, Document)
                .join(Document, Document.id == Invoice.document_id)
                .where(Invoice.document_id.in_(unresolved))
                .order_by(Invoice.updated_at.desc(), Invoice.id)
                .offset(offset)
                .limit(limit)
            )
        ).all()
        if not rows:
            return []
        errors = await self.session.scalars(
            select(DocumentValidationError)
            .where(
                DocumentValidationError.document_id.in_(
                    [i.document_id for i, _ in rows]
                ),
                DocumentValidationError.resolved.is_(False),
            )
            .order_by(
                DocumentValidationError.severity,
                DocumentValidationError.created_at,
                # Errors raised together share a timestamp: order by code.
                DocumentValidationError.code,
                DocumentValidationError.id,
            )
        )
        by_document: dict[uuid.UUID, list[DocumentValidationError]] = {}
        for error in errors:
            by_document.setdefault(error.document_id, []).append(error)
        return [
            (invoice, document, by_document.get(invoice.document_id, []))
            for invoice, document in rows
        ]

    # ----------------------------------------------------- listing & history

    async def list_documents(
        self,
        offset: int,
        limit: int,
        status: str | None = None,
        file_type: str | None = None,
        needs_review: bool | None = None,
    ) -> tuple[list[Document], int]:
        """A page of documents, newest first, and the total matching count."""
        conditions = []
        if status is not None:
            conditions.append(Document.status == status)
        if file_type is not None:
            conditions.append(Document.file_type == file_type)
        if needs_review is not None:
            unresolved = select(DocumentValidationError.document_id).where(
                DocumentValidationError.resolved.is_(False)
            )
            condition = Document.id.in_(unresolved)
            conditions.append(condition if needs_review else ~condition)
        total = await self.session.scalar(
            select(func.count()).select_from(Document).where(*conditions)
        )
        documents = await self.session.scalars(
            select(Document)
            .where(*conditions)
            .order_by(Document.created_at.desc(), Document.id)
            .offset(offset)
            .limit(limit)
        )
        return list(documents), int(total or 0)

    async def summaries(
        self, document_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[ProcessingJob | None, Invoice | None, int]]:
        """For each document: latest job, invoice, unresolved error count."""
        if not document_ids:
            return {}
        jobs = await self.session.scalars(
            select(ProcessingJob)
            .where(ProcessingJob.document_id.in_(document_ids))
            .order_by(ProcessingJob.created_at.desc(), ProcessingJob.id.desc())
        )
        latest: dict[uuid.UUID, ProcessingJob] = {}
        for job in jobs:
            latest.setdefault(job.document_id, job)
        invoices = {
            invoice.document_id: invoice
            for invoice in await self.session.scalars(
                select(Invoice).where(Invoice.document_id.in_(document_ids))
            )
        }
        counts = dict(
            (
                await self.session.execute(
                    select(DocumentValidationError.document_id, func.count())
                    .where(
                        DocumentValidationError.document_id.in_(document_ids),
                        DocumentValidationError.resolved.is_(False),
                    )
                    .group_by(DocumentValidationError.document_id)
                )
            ).all()
        )
        return {
            document_id: (
                latest.get(document_id),
                invoices.get(document_id),
                int(counts.get(document_id, 0)),
            )
            for document_id in document_ids
        }

    async def list_corrections(self, document_id: uuid.UUID) -> list[InvoiceCorrection]:
        """Manual corrections of a document's invoice, newest first."""
        result = await self.session.scalars(
            select(InvoiceCorrection)
            .where(InvoiceCorrection.document_id == document_id)
            .order_by(InvoiceCorrection.created_at.desc(), InvoiceCorrection.id)
        )
        return list(result)
