"""
Document ingestion routes.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile, status

from app.api.dependencies import get_document_service
from app.schema.document import DocumentRead
from app.services.document import DocumentService

router = APIRouter(prefix="/documents", tags=["Documents"])


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
    """Upload one or more files. If any file is rejected, none are stored."""
    documents = await service.upload(files)
    return [DocumentRead.model_validate(document) for document in documents]
