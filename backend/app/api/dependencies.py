"""
Shared FastAPI dependencies.
"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.repositories.document import DocumentRepository
from app.services.document import DocumentService
from app.storage.local import LocalStorage


def get_storage(settings: Annotated[Settings, Depends(get_settings)]) -> LocalStorage:
    """Provide the configured file storage."""
    return LocalStorage(settings.storage_dir)


def get_document_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    storage: Annotated[LocalStorage, Depends(get_storage)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DocumentService:
    """Provide a DocumentService wired to the request's session."""
    return DocumentService(
        repository=DocumentRepository(db),
        storage=storage,
        max_file_size=settings.max_upload_size_bytes,
        max_files=settings.max_files_per_upload,
    )
