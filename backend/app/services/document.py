"""
Document ingestion business logic.
"""

import uuid
from pathlib import PurePosixPath

from fastapi import UploadFile

from app.core.exceptions import (
    CorruptedFileError,
    EmptyFileError,
    FileTooLargeError,
    TooManyFilesError,
    UnsupportedFileTypeError,
)
from app.models.document import Document
from app.repositories.document import DocumentRepository
from app.services.file_types import (
    SIGNATURE_LENGTH,
    accepted_extensions,
    type_for_file_name,
)
from app.services.validators import ContentMismatchError, CorruptedContentError
from app.storage.local import LocalStorage, StorageLimitExceededError

STATUS_UPLOADED = "uploaded"
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
    ) -> None:
        self.repository = repository
        self.storage = storage
        self.max_file_size = max_file_size
        self.max_files = max_files

    async def upload(self, files: list[UploadFile]) -> list[Document]:
        """Store a batch of files and create their records.

        The batch is all-or-nothing: if any file is rejected, or the database
        write fails, every file already written by this call is removed.
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
            return await self.repository.add_all(documents)
        except BaseException:
            for key in stored_keys:
                await self.storage.delete(key)
            raise

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
            status=STATUS_UPLOADED,
        )
