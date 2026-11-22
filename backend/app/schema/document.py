"""
Document request and response schemas.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.extraction.schema import InvoiceData


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
    kind: str
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


class ProcessingJobListItem(ProcessingJobRead):
    """A processing job in the job monitor, with its document."""

    document_id: uuid.UUID
    file_name: str
    is_latest: bool = Field(
        description="The document's most recent job; only it can be retried"
    )


class ProcessingJobList(BaseModel):
    """A page of processing jobs and job counts by status."""

    items: list[ProcessingJobListItem]
    total: int
    offset: int
    limit: int
    counts: dict[str, int] = Field(
        description="Jobs per status, ignoring the status filter"
    )


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


class InvoiceExtractionUsage(BaseModel):
    """Model and token usage of an extraction."""

    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int


class ValidationCheckRead(BaseModel):
    """One arithmetic check of the extracted amounts."""

    code: str
    status: str  # passed | failed | skipped
    message: str
    expected: str | None
    actual: str | None


class ValidationRead(BaseModel):
    """Amount validation, computed from the stored invoice and line items:
    ``failed`` if any check failed, ``passed`` if at least one passed and
    none failed, ``not_checked`` otherwise."""

    status: str
    checks: list[ValidationCheckRead]


class DuplicateRead(BaseModel):
    """Another document that duplicates this invoice."""

    document_id: uuid.UUID
    file_name: str
    match: str  # same_file | same_invoice_number | possible


class ValidationErrorRead(BaseModel):
    """A problem found in the extraction, to be reviewed and resolved."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    severity: str  # error | warning
    message: str
    details: dict[str, Any]
    resolved: bool
    resolved_at: datetime | None
    created_at: datetime


class ValidationErrorUpdate(BaseModel):
    """Resolve (or reopen) a validation error."""

    resolved: bool


class ReviewRead(BaseModel):
    """Whether a person should check this extraction: true while any
    validation error is unresolved."""

    needs_review: bool
    errors: list[ValidationErrorRead]


class InvoiceRead(BaseModel):
    """Extracted invoice data of a document.

    ``invoice`` always has every field; values not found in the document are
    ``null`` and ``line_items`` may be empty.
    """

    document_id: uuid.UUID
    invoice: InvoiceData
    corrected_at: datetime | None
    validation: ValidationRead
    duplicates: list[DuplicateRead]
    review: ReviewRead
    extraction: InvoiceExtractionUsage
    extracted_at: datetime


class ReviewQueueItem(BaseModel):
    """An extraction waiting for human review."""

    document_id: uuid.UUID
    file_name: str
    invoice_number: str | None
    vendor_name: str | None
    invoice_date: date | None
    total_amount: Decimal | None
    currency: str | None
    errors: list[ValidationErrorRead]  # unresolved only
    extracted_at: datetime


class ProcessingSummary(BaseModel):
    """The latest processing job of a document, in brief."""

    kind: str
    status: str
    stage: str | None
    percent: float | None = Field(
        default=None, description="Completion of the current stage, 0-100"
    )
    attempts: int = 0
    max_attempts: int = 1
    retry_scheduled: bool = False
    error: str | None = Field(
        default=None, description="Last failure (also while a retry is scheduled)"
    )

    @classmethod
    def of(cls, job: Any) -> "ProcessingSummary":
        read = ProcessingJobRead.model_validate(job)
        return cls(
            **read.model_dump(
                include={
                    "kind",
                    "status",
                    "stage",
                    "percent",
                    "attempts",
                    "max_attempts",
                    "retry_scheduled",
                    "error",
                }
            )
        )


class InvoiceSummary(BaseModel):
    """Key fields of a document's extracted invoice."""

    document_type: str
    is_invoice: bool
    invoice_number: str | None
    vendor_name: str | None
    invoice_date: date | None
    total_amount: Decimal | None
    currency: str | None
    corrected: bool


class DocumentListItem(DocumentRead):
    """A document in the list, with its processing and review state."""

    processing: ProcessingSummary | None
    invoice: InvoiceSummary | None
    unresolved_errors: int


class DocumentStatsRead(BaseModel):
    """Key figures for the documents overview.

    Outcomes follow each document's latest processing job, so a document
    whose invoice extraction failed counts as failed.
    """

    total: int
    completed: int = Field(description="Latest processing job succeeded")
    failed: int = Field(description="Latest processing job failed")
    in_progress: int = Field(description="Queued or running")
    avg_processing_seconds: float | None = Field(
        description=(
            "Mean time workers spent on a completed document (all its "
            "successful jobs, queue waits excluded); null until one completes"
        )
    )


class UploadLimitsRead(BaseModel):
    """Limits that apply to ``POST /documents`` (set in the environment)."""

    max_file_size_bytes: int
    max_file_size_mb: float
    max_files_per_upload: int


class DocumentDeleteRequest(BaseModel):
    """Documents to delete."""

    ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


class DocumentDeleteResult(BaseModel):
    """Which requested documents were deleted, and why others were not."""

    deleted: list[uuid.UUID]
    in_progress: list[uuid.UUID] = Field(
        description="Being processed right now; delete them once finished."
    )
    not_found: list[uuid.UUID]


class DocumentList(BaseModel):
    """A page of documents; ``total`` counts every match."""

    items: list[DocumentListItem]
    total: int
    offset: int
    limit: int


class InvoiceCorrectionRead(BaseModel):
    """One manual correction: each changed field's old and new value."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    changes: dict[str, dict[str, Any]]
    created_at: datetime


# ---------------------------------------------------------------- comparison


class DocumentBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    file_name: str
    file_type: str
    status: str


class FieldComparisonRead(BaseModel):
    field: str
    left: Any
    right: Any
    status: str  # equal | different | left_missing | right_missing | both_missing


class LinePairRead(BaseModel):
    left_line: int | None
    right_line: int | None
    matched_by: str | None  # product_code | description
    status: str  # equal | different | left_only | right_only
    fields: list[FieldComparisonRead]


class InvoiceComparisonRead(BaseModel):
    fields: list[FieldComparisonRead]
    line_items: list[LinePairRead]
    fields_different: int
    lines_different: int
    lines_unmatched: int


class TextComparisonRead(BaseModel):
    similarity: float
    added: int
    removed: int
    diff: list[str]
    truncated: bool


class RowCheckRead(BaseModel):
    sheet_name: str
    row_number: int
    status: str  # found | partial | missing
    cells: list[Any]
    missing: list[Any]


class RowReconciliationRead(BaseModel):
    """Spreadsheet rows checked against the other document's text."""

    spreadsheet: str  # left | right
    found: int
    partial: int
    missing: int
    rows: list[RowCheckRead]


class ComparisonRead(BaseModel):
    """Sections are null when not applicable; ``notes`` says why."""

    left: DocumentBrief
    right: DocumentBrief
    invoice: InvoiceComparisonRead | None
    text: TextComparisonRead | None
    rows: RowReconciliationRead | None
    notes: list[str]


class CompareCellRead(BaseModel):
    """One document's value for a field."""

    document_id: uuid.UUID
    presence: str  # present | missing | unavailable
    value: Any  # as extracted; exact decimals as strings
    normalized: str | None  # what was compared
    matches_baseline: bool | None
    difference: str | None  # numeric fields: value - baseline ("+12.50")


class CompareFieldRead(BaseModel):
    field: str  # "total_amount", "vendor.name", line: "unit_price"
    label: str
    group: str  # header | vendor | customer | totals | line
    kind: str  # text | identifier | name | code | date | money | quantity | rate
    status: str  # match | mismatch | missing | unavailable
    baseline_document_id: uuid.UUID | None
    cells: list[CompareCellRead]  # in document order


class CompareLineRefRead(BaseModel):
    document_id: uuid.UUID
    line_number: int | None  # None: this document lacks the line
    matched_by: str | None  # reference | product_code | description


class CompareLineGroupRead(BaseModel):
    position: int
    status: str  # match | mismatch | missing | unavailable
    lines: list[CompareLineRefRead]
    fields: list[CompareFieldRead]


class ComparedDocumentRead(DocumentBrief):
    reference: bool
    invoice_available: bool
    unavailable_reason: str | None


class StatusCounts(BaseModel):
    match: int
    mismatch: int
    missing: int
    unavailable: int


class MultiComparisonRead(BaseModel):
    """Two or more documents' extracted invoices, field by field."""

    documents: list[ComparedDocumentRead]  # the first is the reference
    fields: list[CompareFieldRead]
    line_items: list[CompareLineGroupRead]
    field_counts: StatusCounts
    line_counts: StatusCounts
    notes: list[str]


# -------------------------------------------------------------------- search


class SearchSnippetRead(BaseModel):
    """A matching passage. Highlighted terms are wrapped in U+0002 and
    U+0003 (keyword matches only)."""

    text: str
    page_number: int | None
    sheet_name: str | None


class SearchHitRead(BaseModel):
    document: DocumentBrief
    score: float
    matched: list[str]  # keyword | semantic | file_name
    snippets: list[SearchSnippetRead]


class SearchResultsRead(BaseModel):
    query: str
    mode: str
    hits: list[SearchHitRead]
