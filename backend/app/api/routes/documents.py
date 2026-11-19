"""
Document ingestion and retrieval routes.
"""

import uuid
from decimal import Decimal
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse

from app.api.dependencies import (
    get_document_repository,
    get_document_service,
    get_storage,
)
from app.core.config import Settings, get_settings
from app.core.exceptions import NotFoundError
from app.extraction.records import data_from_invoice
from app.extraction.schema import InvoiceData
from app.extraction.validation import check_to_dict, validate_amounts
from app.models.document import Document, DocumentStatus
from app.repositories.document import DocumentRepository
from app.schema.document import (
    DocumentList,
    DocumentListItem,
    DocumentPageRead,
    DocumentRead,
    DocumentSheetRows,
    DocumentSheetSummary,
    DuplicateRead,
    InvoiceCorrectionRead,
    InvoiceExtractionUsage,
    InvoiceRead,
    InvoiceSummary,
    ProcessingJobRead,
    ProcessingSummary,
    ReviewRead,
    ValidationCheckRead,
    ValidationErrorRead,
    ValidationErrorUpdate,
    ValidationRead,
)
from app.services.document import DocumentService
from app.services.invoice_review import InvoiceCorrectionService
from app.storage.local import LocalStorage

router = APIRouter(prefix="/documents", tags=["Documents"])

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {"description": "Document not found"}
}


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Upload documents (PDF, images, spreadsheets)",
    responses={
        status.HTTP_413_CONTENT_TOO_LARGE: {"description": "A file is too large"},
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: {
            "description": "Unsupported type, or content does not match extension"
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": (
                "Corrupted, password-protected or empty file; "
                "too many files; or no files"
            )
        },
    },
)
async def upload_documents(
    files: Annotated[
        list[UploadFile],
        File(description="One or more PDF, JPG, PNG, TIFF, XLSX, XLS or CSV files."),
    ],
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> list[DocumentRead]:
    """Upload one or more files. If any file is rejected, none are stored.

    Each file is queued for background extraction (PDF text layer, OCR for
    scans and images, sheet parsing for XLSX/XLS/CSV). Track it with
    `GET /documents/{id}/processing`.
    """
    documents = await service.upload(files)
    return [DocumentRead.model_validate(document) for document in documents]


@router.get("", summary="List uploaded documents")
async def list_documents(
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    status_filter: Annotated[
        DocumentStatus | None,
        Query(alias="status", description="Only documents in this status"),
    ] = None,
    file_type: Annotated[
        str | None, Query(description="Only this media type, e.g. application/pdf")
    ] = None,
    needs_review: Annotated[
        bool | None,
        Query(description="Only documents with (true) or without (false) open errors"),
    ] = None,
) -> DocumentList:
    """Documents, newest first, each with its latest processing job, key
    invoice fields (once extracted) and number of unresolved validation
    errors."""
    documents, total = await repository.list_documents(
        offset, limit, status_filter, file_type, needs_review
    )
    summaries = await repository.summaries([document.id for document in documents])
    items = []
    for document in documents:
        job, invoice, open_errors = summaries[document.id]
        items.append(
            DocumentListItem(
                **DocumentRead.model_validate(document).model_dump(),
                processing=(
                    ProcessingSummary(kind=job.kind, status=job.status, stage=job.stage)
                    if job is not None
                    else None
                ),
                invoice=(
                    InvoiceSummary(
                        is_invoice=invoice.is_invoice,
                        invoice_number=invoice.invoice_number,
                        vendor_name=invoice.vendor_name,
                        invoice_date=invoice.invoice_date,
                        total_amount=invoice.total_amount,
                        currency=invoice.currency,
                        corrected=invoice.corrected_at is not None,
                    )
                    if invoice is not None
                    else None
                ),
                unresolved_errors=open_errors,
            )
        )
    return DocumentList(items=items, total=total, offset=offset, limit=limit)


async def _get_document_or_404(
    repository: DocumentRepository, document_id: uuid.UUID
) -> Document:
    document = await repository.get(document_id)
    if document is None:
        raise NotFoundError(f"Document {document_id} not found.")
    return document


@router.get(
    "/{document_id}",
    summary="Get a document and its processing status",
    responses=_NOT_FOUND,
)
async def get_document(
    document_id: uuid.UUID,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
) -> DocumentRead:
    """Return a document's metadata, including its processing `status`."""
    document = await _get_document_or_404(repository, document_id)
    return DocumentRead.model_validate(document)


@router.get(
    "/{document_id}/pages",
    summary="Get a document's extracted text, page by page",
    responses=_NOT_FOUND,
)
async def get_document_pages(
    document_id: uuid.UUID,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
) -> list[DocumentPageRead]:
    """Return extracted text per page.

    `extraction_method` is `text_layer` for digital PDF pages and `ocr` for
    scanned pages and images, which also report `ocr_confidence` (0-100).
    The list is empty until processing has completed.
    """
    await _get_document_or_404(repository, document_id)
    pages = await repository.list_pages(document_id)
    return [DocumentPageRead.model_validate(page) for page in pages]


@router.get(
    "/{document_id}/sheets",
    summary="List a spreadsheet's parsed sheets",
    responses=_NOT_FOUND,
)
async def list_document_sheets(
    document_id: uuid.UUID,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
) -> list[DocumentSheetSummary]:
    """Return every parsed sheet (name, size, flags) without cell data.

    Hidden sheets are included and marked `is_hidden`. The list is empty
    until processing has completed, and for non-spreadsheet documents.
    """
    await _get_document_or_404(repository, document_id)
    sheets = await repository.list_sheets(document_id)
    return [DocumentSheetSummary.model_validate(sheet) for sheet in sheets]


@router.get(
    "/{document_id}/sheets/{sheet_number}",
    summary="Get rows of one parsed sheet",
    responses=_NOT_FOUND,
)
async def get_document_sheet(
    document_id: uuid.UUID,
    sheet_number: int,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    offset: Annotated[int, Query(ge=0, description="First row (0-based)")] = 0,
    limit: Annotated[int, Query(ge=1, le=10_000, description="Max rows")] = 1_000,
) -> DocumentSheetRows:
    """Return a window of a sheet's rows as a grid of cell values.

    `sheet_number` is 1-based, in workbook order. Use `offset` and `limit`
    to page through large sheets; `row_count` gives the total.
    """
    await _get_document_or_404(repository, document_id)
    sheet = await repository.get_sheet(document_id, sheet_number)
    if sheet is None:
        raise NotFoundError(
            f"Sheet {sheet_number} of document {document_id} not found."
        )
    summary = DocumentSheetSummary.model_validate(sheet)
    return DocumentSheetRows(
        **summary.model_dump(),
        offset=offset,
        rows=sheet.rows[offset : offset + limit],
    )


@router.get(
    "/{document_id}/processing",
    summary="Get background processing progress",
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Document not found, or it has no processing job"
        }
    },
)
async def get_document_processing(
    document_id: uuid.UUID,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
) -> ProcessingJobRead:
    """Return the status and progress of the document's latest job.

    `status` is `queued`, `running`, `succeeded` or `failed`. While running,
    `stage` names the current step (`extracting_text`, `ocr`,
    `parsing_sheets`, `parsing_csv`, `saving`) and `progress_current` of
    `progress_total` (with `percent`) tracks it.

    Failed attempts are retried automatically with backoff up to
    `max_attempts`; meanwhile the job is `queued` with `retry_scheduled`
    true, `next_run_at` set and `error` describing the last failure. A job
    that is `failed` has used its attempts (or hit a permanent error).
    """
    await _get_document_or_404(repository, document_id)
    job = await repository.latest_job(document_id)
    if job is None:
        raise NotFoundError(f"Document {document_id} has no processing job.")
    return ProcessingJobRead.model_validate(job)


@router.post(
    "/{document_id}/processing/retry",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Retry failed processing",
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Document not found"},
        status.HTTP_409_CONFLICT: {
            "description": "The latest job is not failed (queued, running or done)"
        },
    },
)
async def retry_document_processing(
    document_id: uuid.UUID,
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> ProcessingJobRead:
    """Queue a new processing job for a document whose processing failed.

    The new job starts with a full set of attempts; earlier failed jobs are
    kept for history. Returns the queued job; follow it with
    `GET /documents/{id}/processing`.
    """
    job = await service.retry_processing(document_id)
    return ProcessingJobRead.model_validate(job)


@router.get(
    "/{document_id}/invoice",
    summary="Get extracted invoice fields and line items",
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Document not found, or not extracted (yet)"
        }
    },
)
async def get_document_invoice(
    document_id: uuid.UUID,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> InvoiceRead:
    """Return the invoice data extracted from the document by Claude.

    Every schema field is present; anything the document does not state is
    `null`. `invoice.is_invoice` is false for documents that are not
    invoices. Extraction runs as an `extract_invoice` job after content
    extraction; follow it with `GET /documents/{id}/processing`.

    `validation` holds the arithmetic checks (line amounts, subtotal, tax,
    total, amount due). `duplicates` lists other documents that are the same
    file, have the same vendor and invoice number, or (`possible`) the same
    vendor, date and total. `review` lists the document's validation errors;
    it needs review while any is unresolved.
    """
    document = await _get_document_or_404(repository, document_id)
    return await _invoice_response(repository, document, settings)


async def _invoice_response(
    repository: DocumentRepository, document: Document, settings: Settings
) -> InvoiceRead:
    document_id = document.id
    invoice = await repository.get_invoice(document_id)
    if invoice is None:
        raise NotFoundError(
            f"Document {document_id} has no invoice extraction yet; "
            "check GET /documents/{id}/processing."
        )
    data = data_from_invoice(invoice)
    # Looked up live, so documents uploaded after this extraction count too.
    found = await repository.find_duplicates(
        document_id,
        sha256=document.sha256,
        number_key=invoice.invoice_number_key,
        vendor_key=invoice.vendor_key,
        invoice_date=invoice.invoice_date,
        total_amount=invoice.total_amount,
    )
    duplicates = []
    for other_id, match in found:
        other = await repository.get(other_id)
        if other is not None:
            duplicates.append(
                DuplicateRead(
                    document_id=other_id, file_name=other.file_name, match=match
                )
            )
    checks = [
        ValidationCheckRead.model_validate(check_to_dict(check))
        for check in validate_amounts(data, Decimal(str(settings.validation_tolerance)))
    ]
    errors = await repository.list_validation_errors(document_id)
    return InvoiceRead(
        document_id=document_id,
        invoice=data,
        corrected_at=invoice.corrected_at,
        validation=ValidationRead(status=validation_status(checks), checks=checks),
        duplicates=duplicates,
        review=ReviewRead(
            needs_review=any(not error.resolved for error in errors),
            errors=[ValidationErrorRead.model_validate(error) for error in errors],
        ),
        extraction=InvoiceExtractionUsage(
            model=invoice.model,
            input_tokens=invoice.input_tokens,
            output_tokens=invoice.output_tokens,
            cache_read_tokens=invoice.cache_read_tokens,
        ),
        extracted_at=invoice.updated_at,
    )


def validation_status(checks: list[ValidationCheckRead]) -> str:
    """Overall result of the amount checks."""
    statuses = {check.status for check in checks}
    if "failed" in statuses:
        return "failed"
    if "passed" in statuses:
        return "passed"
    return "not_checked"


@router.get(
    "/{document_id}/validation-errors",
    summary="List a document's validation errors",
    responses=_NOT_FOUND,
)
async def list_document_validation_errors(
    document_id: uuid.UUID,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
) -> list[ValidationErrorRead]:
    """Problems found in the document's extraction: unresolved first, errors
    before warnings. Empty when the extraction found no problems."""
    await _get_document_or_404(repository, document_id)
    errors = await repository.list_validation_errors(document_id)
    return [ValidationErrorRead.model_validate(error) for error in errors]


@router.patch(
    "/{document_id}/validation-errors/{error_id}",
    summary="Resolve or reopen a validation error",
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Document or error not found"}
    },
)
async def update_document_validation_error(
    document_id: uuid.UUID,
    error_id: uuid.UUID,
    update: ValidationErrorUpdate,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
) -> ValidationErrorRead:
    """Set `resolved` to true once a person has checked the problem (it
    leaves the review queue when all of the document's errors are resolved),
    or back to false to reopen it. Re-extracting a document replaces its
    errors."""
    await _get_document_or_404(repository, document_id)
    error = await repository.get_validation_error(document_id, error_id)
    if error is None:
        raise NotFoundError(
            f"Validation error {error_id} of document {document_id} not found."
        )
    error = await repository.set_resolved(error, update.resolved)
    return ValidationErrorRead.model_validate(error)


@router.get(
    "/{document_id}/file",
    summary="Download or display the original file",
    response_class=FileResponse,
    responses=_NOT_FOUND,
)
async def get_document_file(
    document_id: uuid.UUID,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    storage: Annotated[LocalStorage, Depends(get_storage)],
    download: Annotated[
        bool, Query(description="Send as an attachment instead of inline")
    ] = False,
) -> FileResponse:
    """The stored original, byte-for-byte, with its validated media type.

    Shown inline by default (so a browser can display PDFs and images);
    `?download=true` asks the browser to save it. Non-PDF responses are
    sandboxed, so a browser will not run scripts from them. PDFs are not:
    browsers' built-in PDF viewers refuse sandboxed documents, and uploads
    are verified as genuine PDFs (the viewers run isolated anyway).
    """
    document = await _get_document_or_404(repository, document_id)
    path = storage.path_for(document.storage_path)
    if not path.is_file():
        raise NotFoundError(f"The stored file of document {document_id} is missing.")
    return FileResponse(
        path,
        media_type=document.file_type,
        filename=document.file_name,
        content_disposition_type="attachment" if download else "inline",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=3600",
            **(
                {}
                if document.file_type == "application/pdf"
                else {"Content-Security-Policy": "sandbox"}
            ),
        },
    )


@router.put(
    "/{document_id}/invoice",
    summary="Correct extracted invoice fields",
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Document or invoice not found"}
    },
)
async def correct_document_invoice(
    document_id: uuid.UUID,
    corrected: InvoiceData,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> InvoiceRead:
    """Replace the extracted invoice with corrected values.

    Send the complete invoice (as returned in `invoice` by
    `GET /documents/{id}/invoice`) with your changes. The result is
    re-validated and re-checked for duplicates; the document's validation
    errors are replaced accordingly, and the change is recorded in
    `GET /documents/{id}/invoice/corrections`. Sending unchanged values is a
    no-op.
    """
    service = InvoiceCorrectionService(
        repository, Decimal(str(settings.validation_tolerance))
    )
    await service.correct(document_id, corrected)
    document = await _get_document_or_404(repository, document_id)
    return await _invoice_response(repository, document, settings)


@router.get(
    "/{document_id}/invoice/corrections",
    summary="History of manual corrections",
    responses=_NOT_FOUND,
)
async def list_invoice_corrections(
    document_id: uuid.UUID,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
) -> list[InvoiceCorrectionRead]:
    """Every manual correction of the document's invoice, newest first, with
    the old and new value of each changed field."""
    await _get_document_or_404(repository, document_id)
    corrections = await repository.list_corrections(document_id)
    return [InvoiceCorrectionRead.model_validate(c) for c in corrections]
