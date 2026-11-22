"""
Running invoice extraction for a document and storing the result.

Pipeline: render text -> Claude (structured output; long documents in
parts, merged, see ``chunked``) -> normalise -> validate
amounts -> find duplicates -> validation errors -> save invoice, line items
and errors in one transaction.
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.extraction.chunked import DEFAULT_CHUNKING, ChunkingConfig, extract_document
from app.extraction.document_text import Section, document_sections
from app.extraction.llm import InvoiceLLM
from app.extraction.normalize import invoice_number_key, normalize_invoice, vendor_key
from app.extraction.records import invoice_from_data
from app.extraction.review import Duplicate, build_validation_errors
from app.extraction.validation import DEFAULT_TOLERANCE, validate_amounts
from app.processing.processor import DocumentNotProcessableError
from app.processing.progress import ProgressCallback, Stage, ignore_progress
from app.repositories.document import DocumentRepository


def _decimal(value: float | None) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


@dataclass(frozen=True)
class _Source:
    """What extraction needs from the stored document."""

    sections: list[Section]
    sha256: str | None
    ocr_confidences: list[tuple[int, float]]


class InvoiceExtractor:
    """Extracts, validates and stores invoice data for processed documents."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        llm: InvoiceLLM,
        chunking: ChunkingConfig = DEFAULT_CHUNKING,
        tolerance: Decimal = DEFAULT_TOLERANCE,
        min_ocr_confidence: float = 80.0,
    ) -> None:
        self.session_factory = session_factory
        self.llm = llm
        self.chunking = chunking
        self.tolerance = tolerance
        self.min_ocr_confidence = min_ocr_confidence

    async def _load(self, document_id: uuid.UUID) -> _Source:
        async with self.session_factory() as session:
            repository = DocumentRepository(session)
            document = await repository.get(document_id)
            if document is None:
                raise DocumentNotProcessableError(f"Document {document_id} not found.")
            pages = await repository.list_pages(document_id)
            sections = document_sections(
                pages, await repository.list_sheets(document_id)
            )
            return _Source(
                sections=sections,
                sha256=document.sha256,
                ocr_confidences=[
                    (page.page_number, page.ocr_confidence)
                    for page in pages
                    if page.ocr_confidence is not None
                ],
            )

    async def extract(
        self, document_id: uuid.UUID, progress: ProgressCallback = ignore_progress
    ) -> None:
        """Extract and store a document's invoice data (replacing any earlier).

        The model call happens outside any database transaction, since it can
        take minutes; reading and saving each use their own short session.
        """
        source = await self._load(document_id)

        result = await extract_document(
            self.llm,
            source.sections,
            self.chunking,
            lambda done, total: progress(Stage.EXTRACTING_INVOICE, done, total),
        )

        progress(Stage.SAVING, 0, 1)
        data = normalize_invoice(result.data)
        checks = validate_amounts(data, self.tolerance)
        async with self.session_factory() as session:
            repository = DocumentRepository(session)
            duplicates = await repository.find_duplicates(
                document_id,
                sha256=source.sha256,
                number_key=invoice_number_key(data.invoice_number),
                vendor_key=vendor_key(data.vendor),
                invoice_date=data.invoice_date,
                total_amount=_decimal(data.total_amount),
                document_type=data.document_type,
            )
            errors = build_validation_errors(
                data,
                checks,
                [Duplicate(str(other), match) for other, match in duplicates],
                source.ocr_confidences,
                self.min_ocr_confidence,
            )
            await repository.save_invoice(
                invoice_from_data(document_id, data, result),
                errors,
                flag_documents=duplicates if data.is_invoice else (),
            )
        progress(Stage.SAVING, 1, 1)
