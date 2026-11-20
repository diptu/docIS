"""DocumentService business rules in isolation (repository, storage and the
Celery dispatcher are autospecced doubles)."""

import uuid
from typing import Any
from unittest.mock import AsyncMock, create_autospec

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import ConflictError, NotFoundError, TooManyFilesError
from app.models import JobKind, JobStatus
from app.repositories.document import DocumentRepository
from app.services.document import DeleteResult, DocumentService
from app.storage.local import LocalStorage
from tests.support.builders import build_document, build_job


@pytest.fixture
def repository() -> Any:
    return create_autospec(DocumentRepository, instance=True)


@pytest.fixture
def storage() -> Any:
    return create_autospec(LocalStorage, instance=True)


@pytest.fixture
def dispatcher() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def service(repository: Any, storage: Any, dispatcher: AsyncMock) -> DocumentService:
    return DocumentService(
        repository=repository,
        storage=storage,
        max_file_size=1024,
        max_files=2,
        max_attempts=3,
        dispatcher=dispatcher,
    )


# ------------------------------------------------------------------ delete


async def test_delete_classifies_each_id_in_request_order(
    service: DocumentService, repository: Any
) -> None:
    """Duplicates collapse; each ID is deleted, in progress or not found."""
    a, b, busy, missing = (uuid.uuid4() for _ in range(4))
    repository.delete_documents.return_value = (
        [build_document(id=b), build_document(id=a)],
        {busy},
    )

    result = await service.delete([missing, a, busy, a, b])

    assert result == DeleteResult(
        deleted=[a, b], in_progress=[busy], not_found=[missing]
    )
    repository.delete_documents.assert_awaited_once_with([missing, a, busy, b])


async def test_delete_removes_files_only_of_deleted_documents(
    service: DocumentService, repository: Any, storage: Any
) -> None:
    gone = build_document()
    repository.delete_documents.return_value = ([gone], {uuid.uuid4()})

    await service.delete([gone.id])

    storage.delete.assert_awaited_once_with(gone.storage_path)


async def test_delete_survives_a_file_that_cannot_be_removed(
    service: DocumentService, repository: Any, storage: Any
) -> None:
    """The record is already gone (committed); a leftover file is logged and
    the remaining files are still removed."""
    first, second = build_document(), build_document()
    repository.delete_documents.return_value = ([first, second], set())
    storage.delete.side_effect = [PermissionError("read-only"), None]

    result = await service.delete([first.id, second.id])

    assert result.deleted == [first.id, second.id]
    assert storage.delete.await_count == 2


@pytest.mark.parametrize(
    ("outcome", "error"),
    [
        (([], set()), NotFoundError),
        (([], {"self"}), ConflictError),
    ],
)
async def test_delete_one_raises_for_missing_or_busy_documents(
    service: DocumentService, repository: Any, outcome: Any, error: type
) -> None:
    document_id = uuid.uuid4()
    documents, busy = outcome
    repository.delete_documents.return_value = (
        documents,
        {document_id} if busy else set(),
    )

    with pytest.raises(error):
        await service.delete_one(document_id)


async def test_delete_one_succeeds_silently(
    service: DocumentService, repository: Any
) -> None:
    document = build_document()
    repository.delete_documents.return_value = ([document], set())

    assert await service.delete_one(document.id) is None


# ------------------------------------------------------------------- retry


async def test_retry_queues_a_fresh_job_of_the_failed_kind_and_dispatches_it(
    service: DocumentService, repository: Any, dispatcher: AsyncMock
) -> None:
    """A failed invoice extraction is retried as invoice extraction (no
    re-OCR), with a full set of attempts, dispatched after it is stored."""
    document = build_document()
    repository.get.return_value = document
    repository.latest_job.return_value = build_job(
        document.id, kind=JobKind.EXTRACT_INVOICE, status=JobStatus.FAILED
    )
    repository.add_job.side_effect = lambda job: job

    job = await service.retry_processing(document.id)

    assert (job.kind, job.status, job.max_attempts) == (
        JobKind.EXTRACT_INVOICE,
        JobStatus.QUEUED,
        3,
    )
    dispatcher.assert_awaited_once_with([job.id])


async def test_retry_of_a_document_without_jobs_runs_content_extraction(
    service: DocumentService, repository: Any
) -> None:
    repository.get.return_value = build_document()
    repository.latest_job.return_value = None
    repository.add_job.side_effect = lambda job: job

    job = await service.retry_processing(uuid.uuid4())

    assert job.kind == JobKind.EXTRACT_CONTENT


@pytest.mark.parametrize("status", [JobStatus.QUEUED, JobStatus.RUNNING, "succeeded"])
async def test_only_failed_jobs_can_be_retried(
    service: DocumentService, repository: Any, dispatcher: AsyncMock, status: str
) -> None:
    document = build_document()
    repository.get.return_value = document
    repository.latest_job.return_value = build_job(document.id, status=status)

    with pytest.raises(ConflictError, match=str(status)):
        await service.retry_processing(document.id)

    repository.add_job.assert_not_awaited()
    dispatcher.assert_not_awaited()


async def test_retry_unknown_or_unprocessable_documents(
    service: DocumentService, repository: Any
) -> None:
    repository.get.return_value = None
    with pytest.raises(NotFoundError):
        await service.retry_processing(uuid.uuid4())

    repository.get.return_value = build_document(file_type="application/zip")
    with pytest.raises(ConflictError, match="not processed"):
        await service.retry_processing(uuid.uuid4())


async def test_concurrent_retry_lost_race_is_a_conflict(
    service: DocumentService, repository: Any, dispatcher: AsyncMock
) -> None:
    """The unique active-job index rejecting the insert means another
    request already queued a job: report a conflict, dispatch nothing."""
    document = build_document()
    repository.get.return_value = document
    repository.latest_job.return_value = build_job(document.id, status=JobStatus.FAILED)
    repository.add_job.side_effect = IntegrityError("insert", {}, Exception("dup"))

    with pytest.raises(ConflictError, match="already has a queued or running job"):
        await service.retry_processing(document.id)

    dispatcher.assert_not_awaited()


# ------------------------------------------------------------------ upload


async def test_too_many_files_are_rejected_before_anything_is_stored(
    service: DocumentService, repository: Any, storage: Any
) -> None:
    with pytest.raises(TooManyFilesError, match="maximum is 2"):
        await service.upload([object(), object(), object()])  # type: ignore[list-item]

    storage.save.assert_not_awaited()
    repository.add_all.assert_not_awaited()
