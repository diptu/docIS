"""In-memory ORM objects for tests that mock the repository.

These are unsaved model instances with every column a response needs, so
route code can serialise them exactly as it would rows from the database.
"""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from app.models import (
    Document,
    DocumentPage,
    DocumentSheet,
    DocumentStatus,
    DocumentValidationError,
    Invoice,
    JobKind,
    JobStatus,
    ProcessingJob,
)

NOW = dt.datetime(2026, 11, 20, 9, 30, tzinfo=dt.UTC)


def _stamped(obj: Any, **overrides: Any) -> Any:
    obj.created_at = overrides.pop("created_at", NOW)
    obj.updated_at = overrides.pop("updated_at", NOW)
    for key, value in overrides.items():
        setattr(obj, key, value)
    return obj


def build_document(**overrides: Any) -> Document:
    document_id = overrides.pop("id", uuid.uuid4())
    document = Document(
        id=document_id,
        file_name="invoice.pdf",
        file_type="application/pdf",
        storage_path=f"originals/{document_id.hex[:2]}/{document_id}.pdf",
        size_bytes=1234,
        sha256="ab" * 32,
        status=DocumentStatus.PROCESSED,
    )
    return _stamped(document, **overrides)  # type: ignore[no-any-return]


def build_job(document_id: uuid.UUID, **overrides: Any) -> ProcessingJob:
    job = ProcessingJob(
        id=overrides.pop("id", uuid.uuid4()),
        document_id=document_id,
        kind=JobKind.EXTRACT_CONTENT,
        status=JobStatus.SUCCEEDED,
        stage="saving",
        progress_current=1,
        progress_total=1,
        attempts=1,
        max_attempts=3,
        error=None,
        next_run_at=None,
        started_at=NOW,
        finished_at=NOW + dt.timedelta(seconds=2),
    )
    job.created_at = overrides.pop("created_at", NOW)
    for key, value in overrides.items():
        setattr(job, key, value)
    return job


def build_invoice(document_id: uuid.UUID, **overrides: Any) -> Invoice:
    invoice = Invoice(
        id=uuid.uuid4(),
        document_id=document_id,
        is_invoice=True,
        invoice_number="INV-2026-001",
        invoice_date=dt.date(2026, 11, 3),
        currency="EUR",
        vendor_name="ACME GmbH",
        total_amount=Decimal("1487.50"),
        corrected_at=None,
    )
    return _stamped(invoice, **overrides)  # type: ignore[no-any-return]


def build_validation_error(
    document_id: uuid.UUID, **overrides: Any
) -> DocumentValidationError:
    error = DocumentValidationError(
        id=uuid.uuid4(),
        document_id=document_id,
        code="total",
        severity="error",
        message="Total does not match subtotal plus tax.",
        details={"expected": "1487.5", "actual": "1500.0"},
        resolved=False,
        resolved_at=None,
    )
    return _stamped(error, **overrides)  # type: ignore[no-any-return]


def build_page(document_id: uuid.UUID, number: int = 1, text: str = "Hi") -> Any:
    page = DocumentPage(
        document_id=document_id,
        page_number=number,
        text=text,
        char_count=len(text),
        extraction_method="text_layer",
        needs_ocr=False,
        ocr_confidence=None,
    )
    return _stamped(page)


def build_sheet(document_id: uuid.UUID, rows: list[list[Any]]) -> DocumentSheet:
    sheet = DocumentSheet(
        document_id=document_id,
        sheet_number=1,
        name="Sheet1",
        row_count=len(rows),
        column_count=max((len(r) for r in rows), default=0),
        rows=rows,
        is_hidden=False,
        truncated=False,
    )
    return _stamped(sheet)  # type: ignore[no-any-return]
