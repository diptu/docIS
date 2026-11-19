"""
Comparing two documents.
"""

import uuid
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import get_document_repository
from app.compare.service import (
    LinePair,
    compare_invoices,
    compare_text,
    reconcile_rows,
)
from app.core.exceptions import AppError, NotFoundError
from app.extraction.records import data_from_invoice
from app.models.document import Document
from app.repositories.document import DocumentRepository
from app.schema.document import (
    ComparisonRead,
    DocumentBrief,
    FieldComparisonRead,
    InvoiceComparisonRead,
    LinePairRead,
    RowCheckRead,
    RowReconciliationRead,
    TextComparisonRead,
)

router = APIRouter(prefix="/compare", tags=["Compare"])


class SameDocumentError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT


async def _load(repository: DocumentRepository, document_id: uuid.UUID) -> Document:
    document = await repository.get(document_id)
    if document is None:
        raise NotFoundError(f"Document {document_id} not found.")
    return document


def _line_read(pair: LinePair) -> LinePairRead:
    return LinePairRead(
        left_line=pair.left_index,
        right_line=pair.right_index,
        matched_by=pair.matched_by,
        status=pair.status,
        fields=[FieldComparisonRead(**asdict(f)) for f in pair.fields],
    )


@router.get(
    "",
    summary="Compare two documents",
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "A document was not found"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Same document twice"},
    },
)
async def compare_documents(
    left: Annotated[uuid.UUID, Query(description="First document")],
    right: Annotated[uuid.UUID, Query(description="Second document")],
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
) -> ComparisonRead:
    """Compare two documents, e.g. an invoice PDF against a revised PDF, or
    against an Excel export.

    * `invoice`: when both have an extracted invoice: every header field,
      party and total side by side, and line items paired by product code
      or description with per-field differences.
    * `text`: when both have page text (PDF, images): a line diff.
    * `rows`: when one is a spreadsheet and the other has page text: each
      sheet row checked against the text (numbers in any format, dates in
      common spellings). Works without AI extraction.
    """
    if left == right:
        raise SameDocumentError("Choose two different documents to compare.")
    documents = [await _load(repository, left), await _load(repository, right)]
    notes: list[str] = []

    invoices = [await repository.get_invoice(d.id) for d in documents]
    invoice_section = None
    if invoices[0] is not None and invoices[1] is not None:
        fields, lines = compare_invoices(
            data_from_invoice(invoices[0]), data_from_invoice(invoices[1])
        )
        invoice_section = InvoiceComparisonRead(
            fields=[FieldComparisonRead(**asdict(f)) for f in fields],
            line_items=[_line_read(pair) for pair in lines],
            fields_different=sum(
                f.status != "equal" and f.status != "both_missing" for f in fields
            ),
            lines_different=sum(pair.status == "different" for pair in lines),
            lines_unmatched=sum(
                pair.status in ("left_only", "right_only") for pair in lines
            ),
        )
    else:
        missing = [
            d.file_name for d, i in zip(documents, invoices, strict=True) if i is None
        ]
        notes.append(
            "No invoice comparison: no extracted invoice for "
            + ", ".join(missing)
            + "."
        )

    texts = [
        "\n\n".join(page.text for page in await repository.list_pages(d.id))
        for d in documents
    ]
    sheets = [await repository.list_sheets(d.id) for d in documents]

    text_section = None
    if texts[0].strip() and texts[1].strip():
        result = compare_text(texts[0], texts[1])
        text_section = TextComparisonRead(**asdict(result))
    elif not sheets[0] and not sheets[1]:
        notes.append("No text comparison: a document has no extracted text.")

    rows_section = None
    for side, (sheet_rows, other_text) in (
        ("left", (sheets[0], texts[1])),
        ("right", (sheets[1], texts[0])),
    ):
        if sheet_rows and other_text.strip():
            checks = reconcile_rows([(s.name, s.rows) for s in sheet_rows], other_text)
            rows_section = RowReconciliationRead(
                spreadsheet=side,
                found=sum(c.status == "found" for c in checks),
                partial=sum(c.status == "partial" for c in checks),
                missing=sum(c.status == "missing" for c in checks),
                rows=[RowCheckRead(**asdict(c)) for c in checks],
            )
            break
    if rows_section is None and (sheets[0] or sheets[1]):
        notes.append(
            "No row check: needs one spreadsheet and one document with page text."
        )

    return ComparisonRead(
        left=DocumentBrief.model_validate(documents[0]),
        right=DocumentBrief.model_validate(documents[1]),
        invoice=invoice_section,
        text=text_section,
        rows=rows_section,
        notes=notes,
    )
