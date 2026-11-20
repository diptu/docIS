"""
Processing job monitor: background jobs across all documents.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_document_repository
from app.models.processing_job import JobKind, JobStatus
from app.repositories.document import DocumentRepository
from app.schema.document import (
    ProcessingJobList,
    ProcessingJobListItem,
    ProcessingJobRead,
)

router = APIRouter(prefix="/processing", tags=["Processing"])


@router.get("/jobs", summary="List processing jobs across documents")
async def list_processing_jobs(
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    status_filter: Annotated[
        JobStatus | None, Query(alias="status", description="Only jobs in this status")
    ] = None,
    kind: Annotated[JobKind | None, Query(description="Only jobs of this kind")] = None,
    latest_only: Annotated[
        bool,
        Query(description="Only each document's most recent job (hide retried runs)"),
    ] = False,
) -> ProcessingJobList:
    """Jobs newest first, each with its document, progress and last error,
    plus job counts per status for the same `kind` and `latest_only`."""
    entries, total, counts = await repository.list_all_jobs(
        offset, limit, status_filter, kind, latest_only
    )
    items = [
        ProcessingJobListItem(
            **ProcessingJobRead.model_validate(entry.job).model_dump(
                exclude={"retry_scheduled", "percent"}
            ),
            document_id=entry.job.document_id,
            file_name=entry.file_name,
            is_latest=entry.is_latest,
        )
        for entry in entries
    ]
    return ProcessingJobList(
        items=items, total=total, offset=offset, limit=limit, counts=counts
    )
