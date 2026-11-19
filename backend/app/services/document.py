"""
Document ingestion business logic.
"""

import uuid
from pathlib import PurePosixPath

from fastapi import UploadFile
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import (
    ConflictError,
    CorruptedFileError,
    EmptyFileError,
    FileTooLargeError,
    NotFoundError,
    TooManyFilesError,
    UnsupportedFileTypeError,
)
from app.models.document import Document, DocumentStatus
from app.models.processing_job import JobStatus, ProcessingJob
from app.processing.extract import PROCESSABLE_TYPES
from app.repositories.document import DocumentRepository
from app.services.file_types import (
    SIGNATURE_LENGTH,
    accepted_extensions,
    type_for_file_name,
)
from app.services.validators import ContentMismatchError, CorruptedContentError
from app.storage.local import LocalStorage, StorageLimitExceededError

ORIGINALS_PREFIX = "originals"
MAX_FILE_NAME_LENGTH = 255


def clean_file_name(raw: str | None) -> str:
    """Return the client's file name without any directory components."""
    name = PurePosixPath((raw or "").replace("\\", "/")).name.strip()
    if len(name) > MAX_FILE_NAME_LENGTH:
        stem, dot, ext = name.rpartition(".")
        keep = MAX_FILE_NAME_LENGTH - len(dot + ext)
        name = (stem[:keep] + dot + ext) if dot else name[:MAX_FILE_NAME_LENGTH]
    return name


def storage_key(document_id: uuid.UUID, extension: str) -> str:
    """Storage key for an original, sharded by ID prefix to keep folders small."""
    hex_id = document_id.hex
    return f"{ORIGINALS_PREFIX}/{hex_id[:2]}/{document_id}{extension}"


class DocumentService:
    """Validate, store and record uploaded documents."""

    def __init__(
        self,
        repository: DocumentRepository,
        storage: LocalStorage,
        max_file_size: int,
        max_files: int,
        max_attempts: int = 3,
    ) -> None:
        self.repository = repository
        self.storage = storage
        self.max_file_size = max_file_size
        self.max_files = max_files
        self.max_attempts = max_attempts

    async def upload(self, files: list[UploadFile]) -> list[Document]:
        """Store a batch of files and create their records.

        Each document whose type can be processed gets a queued processing
        job in the same transaction. The batch is all-or-nothing: if any file
        is rejected, or the database write fails, every file already written
        by this call is removed.
        """
        if len(files) > self.max_files:
            raise TooManyFilesError(
                f"Too many files: {len(files)} uploaded, maximum is {self.max_files}."
            )

        stored_keys: list[str] = []
        try:
            documents = []
            for upload in files:
                document = await self._store(upload)
                stored_keys.append(document.storage_path)
                documents.append(document)
            jobs = [
                self._new_job(document.id)
                for document in documents
                if document.file_type in PROCESSABLE_TYPES
            ]
            return await self.repository.add_all(documents, jobs)
        except BaseException:
            for key in stored_keys:
                await self.storage.delete(key)
            raise

    def _new_job(self, document_id: uuid.UUID) -> ProcessingJob:
        return ProcessingJob(
            document_id=document_id,
            status=JobStatus.QUEUED,
            max_attempts=self.max_attempts,
        )

    async def retry_processing(self, document_id: uuid.UUID) -> ProcessingJob:
        """Queue a fresh processing job for a document whose last job failed.

        A new job (with a full set of attempts) is created rather than
        resetting the old one, so the failure history is kept.
        """
        document = await self.repository.get(document_id)
        if document is None:
            raise NotFoundError(f"Document {document_id} not found.")
        if document.file_type not in PROCESSABLE_TYPES:
            raise ConflictError(
                f"Documents of type {document.file_type} are not processed."
            )

        latest = await self.repository.latest_job(document_id)
        if latest is not None and latest.status != JobStatus.FAILED:
            raise ConflictError(
                f"Document {document_id} cannot be retried: its processing job is "
                f"{latest.status}. Only failed jobs can be retried."
            )
        try:
            return await self.repository.add_job(self._new_job(document_id))
        except IntegrityError:
            # Another request queued a job concurrently (unique active-job
            # index); the document is being processed either way.
            raise ConflictError(
                f"Document {document_id} already has a queued or running job."
            ) from None

    async def _store(self, upload: UploadFile) -> Document:
        name = clean_file_name(upload.filename)
        label = name or "<unnamed>"

        file_type = type_for_file_name(name)
        if file_type is None:
            raise UnsupportedFileTypeError(
                f"{label}: unsupported file type. "
                f"Accepted extensions: {accepted_extensions()}."
            )

        header = await upload.read(SIGNATURE_LENGTH)
        if not header:
            raise EmptyFileError(f"{label}: file is empty.")
        if not file_type.matches(header):
            raise UnsupportedFileTypeError(
                f"{label}: content is not a valid {file_type.label} file."
            )
        await upload.seek(0)

        document_id = uuid.uuid4()
        try:
            stored = await self.storage.save(
                upload,
                storage_key(document_id, file_type.canonical_extension),
                self.max_file_size,
                verify=file_type.verify,
            )
        except StorageLimitExceededError:
            limit_mb = self.max_file_size / (1024 * 1024)
            raise FileTooLargeError(
                f"{label}: file exceeds the {limit_mb:g} MB limit."
            ) from None
        except ContentMismatchError as exc:
            raise UnsupportedFileTypeError(f"{label}: {exc.reason}") from None
        except CorruptedContentError as exc:
            raise CorruptedFileError(f"{label}: {exc.reason}") from None

        return Document(
            id=document_id,
            file_name=name,
            file_type=file_type.media_type,
            storage_path=stored.key,
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
            status=DocumentStatus.UPLOADED,
        )
