"""
Document ingestion business logic.
"""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
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
from app.models.processing_job import JobKind, JobStatus, ProcessingJob
from app.processing.extract import PROCESSABLE_TYPES
from app.repositories.document import DocumentRepository
from app.services.file_types import (
    SIGNATURE_LENGTH,
    accepted_extensions,
    type_for_file_name,
)
from app.services.validators import ContentMismatchError, CorruptedContentError
from app.storage.local import LocalStorage, StorageLimitExceededError
from app.worker.dispatch import JobDispatcher, no_dispatch

logger = logging.getLogger(__name__)

ORIGINALS_PREFIX = "originals"


@dataclass(frozen=True)
class DeleteResult:
    """Outcome of a delete request, per document ID (in request order)."""

    deleted: list[uuid.UUID]
    in_progress: list[uuid.UUID]
    not_found: list[uuid.UUID]


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
        dispatcher: JobDispatcher = no_dispatch,
    ) -> None:
        self.repository = repository
        self.storage = storage
        self.max_file_size = max_file_size
        self.max_files = max_files
        self.max_attempts = max_attempts
        self.dispatcher = dispatcher

    async def upload(self, files: list[UploadFile]) -> list[Document]:
        """Store a batch of files and create their records.

        Each document whose type can be processed gets a queued processing
        job in the same transaction; after commit the jobs are sent to the
        background workers. The batch is all-or-nothing: if any file
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
            saved = await self.repository.add_all(documents, jobs)
        except BaseException:
            for key in stored_keys:
                await self.storage.delete(key)
            raise
        # Only after commit: a worker must never receive a job it cannot see.
        await self.dispatcher([job.id for job in jobs])
        return saved

    def _new_job(
        self, document_id: uuid.UUID, kind: str = JobKind.EXTRACT_CONTENT
    ) -> ProcessingJob:
        return ProcessingJob(
            id=uuid.uuid4(),
            document_id=document_id,
            kind=kind,
            status=JobStatus.QUEUED,
            max_attempts=self.max_attempts,
        )

    async def retry_processing(self, document_id: uuid.UUID) -> ProcessingJob:
        """Queue a fresh processing job for a document whose last job failed.

        A new job of the same kind (with a full set of attempts) is created
        rather than resetting the old one, so the failure history is kept. A
        failed invoice extraction is retried without re-running OCR.
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
            kind = latest.kind if latest is not None else JobKind.EXTRACT_CONTENT
            job = await self.repository.add_job(self._new_job(document_id, kind))
        except IntegrityError:
            # Another request queued a job concurrently (unique active-job
            # index); the document is being processed either way.
            raise ConflictError(
                f"Document {document_id} already has a queued or running job."
            ) from None
        await self.dispatcher([job.id])
        return job

    async def delete(self, document_ids: Sequence[uuid.UUID]) -> DeleteResult:
        """Delete documents with everything extracted from them, and their
        stored originals.

        Documents whose processing job is running are skipped (retry once it
        has finished); unknown IDs are reported as not found. A queued job of
        a deleted document is dropped: the worker finds nothing to claim.
        """
        ids = list(dict.fromkeys(document_ids))
        documents, busy = await self.repository.delete_documents(ids)
        # Files go only after commit: a failed delete must keep them.
        for document in documents:
            try:
                await self.storage.delete(document.storage_path)
            except OSError:
                logger.exception(
                    "Could not remove stored file %s of deleted document %s",
                    document.storage_path,
                    document.id,
                )
        deleted = {document.id for document in documents}
        return DeleteResult(
            deleted=[i for i in ids if i in deleted],
            in_progress=[i for i in ids if i in busy],
            not_found=[i for i in ids if i not in deleted and i not in busy],
        )

    async def delete_one(self, document_id: uuid.UUID) -> None:
        """Delete one document; 404 if unknown, 409 while it is processed."""
        result = await self.delete([document_id])
        if result.not_found:
            raise NotFoundError(f"Document {document_id} not found.")
        if result.in_progress:
            raise ConflictError(
                f"Document {document_id} is being processed; "
                "delete it once processing has finished."
            )

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
