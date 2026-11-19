"""
Document request and response schemas.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field


class DocumentBase(BaseModel):
    """Fields shared by document schemas."""

    file_name: str = Field(min_length=1, max_length=255)
    file_type: str = Field(min_length=1, max_length=100)
    storage_path: str = Field(min_length=1, max_length=1024)
    status: str = Field(min_length=1, max_length=50)


class DocumentCreate(DocumentBase):
    """Payload for creating a document."""


class DocumentUpdate(BaseModel):
    """Payload for partially updating a document."""

    file_name: str | None = Field(default=None, min_length=1, max_length=255)
    file_type: str | None = Field(default=None, min_length=1, max_length=100)
    storage_path: str | None = Field(default=None, min_length=1, max_length=1024)
    status: str | None = Field(default=None, min_length=1, max_length=50)


class DocumentRead(DocumentBase):
    """Document returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    size_bytes: int | None = None
    sha256: str | None = None
    created_at: datetime
    updated_at: datetime


class ProcessingJobRead(BaseModel):
    """Progress of a document's background processing job."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    stage: str | None
    progress_current: int
    progress_total: int | None
    attempts: int
    max_attempts: int
    error: str | None
    next_run_at: datetime | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def retry_scheduled(self) -> bool:
        """True while a failed attempt is waiting to be retried."""
        return self.status == "queued" and self.attempts > 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def percent(self) -> float | None:
        """Completion of the current stage, 0-100; None if unknown."""
        if not self.progress_total:
            return 100.0 if self.status == "succeeded" else None
        return round(100 * self.progress_current / self.progress_total, 1)


class DocumentPageRead(BaseModel):
    """Extracted content of one document page."""

    model_config = ConfigDict(from_attributes=True)

    page_number: int
    text: str
    char_count: int
    extraction_method: str
    needs_ocr: bool
    ocr_confidence: float | None = None


class DocumentSheetSummary(BaseModel):
    """A parsed worksheet without its cell data."""

    model_config = ConfigDict(from_attributes=True)

    sheet_number: int
    name: str
    row_count: int
    column_count: int
    is_hidden: bool
    truncated: bool


class DocumentSheetRows(DocumentSheetSummary):
    """A slice of a worksheet's rows.

    ``rows`` holds rows ``offset`` to ``offset + len(rows) - 1`` (0-based)
    as lists of JSON scalars; dates are ISO 8601 strings, empty cells null.
    """

    offset: int
    rows: list[list[Any]]
