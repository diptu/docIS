"""
Document ingestion and retrieval routes.
"""

import uuid
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Query,
    UploadFile,
    status,
)

from app.api.dependencies import get_document_repository, get_document_service
from app.core.exceptions import NotFoundError
from app.models.document import Document
from app.repositories.document import DocumentRepository
from app.schema.document import (
    DocumentPageRead,
    DocumentRead,
    DocumentSheetRows,
    DocumentSheetSummary,
    ProcessingJobRead,
)
from app.services.document import DocumentService

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
