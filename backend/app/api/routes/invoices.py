"""
Cross-document invoice routes.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_document_repository
from app.repositories.document import DocumentRepository
from app.schema.document import ReviewQueueItem, ValidationErrorRead

router = APIRouter(prefix="/invoices", tags=["Invoices"])


@router.get("/review", summary="List extractions that need human review")
async def list_review_queue(
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ReviewQueueItem]:
    """Invoices with unresolved validation errors, most recently extracted
    first, each with those errors.

    Errors are raised when amounts do not add up, key fields are missing or
    the invoice duplicates another upload (severity `error`), and for
    possible duplicates, low OCR confidence, model uncertainty or documents
    that are not invoices (`warning`). Resolve them with
    `PATCH /documents/{id}/validation-errors/{error_id}`.
    """
    rows = await repository.list_for_review(offset, limit)
    return [
        ReviewQueueItem(
            document_id=invoice.document_id,
            file_name=document.file_name,
            invoice_number=invoice.invoice_number,
            vendor_name=invoice.vendor_name,
            invoice_date=invoice.invoice_date,
            total_amount=invoice.total_amount,
            currency=invoice.currency,
            errors=[ValidationErrorRead.model_validate(error) for error in errors],
            extracted_at=invoice.updated_at,
        )
        for invoice, document, errors in rows
    ]
