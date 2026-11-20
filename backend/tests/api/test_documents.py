"""Document read routes: listing, detail, pages, sheets, processing state,
validation errors and the original file, against a mocked repository."""

import uuid
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.config import Settings
from app.models import DocumentStatus, JobStatus
from tests.support.builders import (
    build_document,
    build_invoice,
    build_job,
    build_page,
    build_sheet,
    build_validation_error,
)

MISSING = "00000000-0000-0000-0000-00000000dead"


# --------------------------------------------------------------- listing


async def test_list_combines_documents_with_their_summaries(
    client: AsyncClient, repository: Any
) -> None:
    """Each item carries its latest job, invoice summary and open errors."""
    done, fresh = build_document(file_name="a.pdf"), build_document(file_name="b.csv")
    invoice = build_invoice(done.id, corrected_at=done.created_at)
    repository.list_documents.return_value = ([done, fresh], 7)
    repository.summaries.return_value = {
        done.id: (build_job(done.id), invoice, 2),
        fresh.id: (None, None, 0),
    }

    response = await client.get("/documents", params={"offset": 5, "limit": 2})

    assert response.status_code == 200
    body = response.json()
    assert (body["total"], body["offset"], body["limit"]) == (7, 5, 2)
    first, second = body["items"]
    assert first["file_name"] == "a.pdf"
    assert first["processing"] == {
        "kind": "extract_content",
        "status": "succeeded",
        "stage": "saving",
        "percent": 100.0,
        "attempts": 1,
        "max_attempts": 3,
        "retry_scheduled": False,
        "error": None,
    }
    assert first["invoice"]["invoice_number"] == "INV-2026-001"
    assert first["invoice"]["total_amount"] == "1487.50"
    assert first["invoice"]["corrected"] is True
    assert first["unresolved_errors"] == 2
    assert (second["processing"], second["invoice"], second["unresolved_errors"]) == (
        None,
        None,
        0,
    )
    repository.summaries.assert_awaited_once_with([done.id, fresh.id])


async def test_list_passes_filters_to_the_repository(
    client: AsyncClient, repository: Any
) -> None:
    """Query filters reach the repository typed (enum status, bool review)."""
    repository.list_documents.return_value = ([], 0)
    repository.summaries.return_value = {}

    response = await client.get(
        "/documents",
        params={
            "status": "failed",
            "file_type": "text/csv",
            "needs_review": "false",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "offset": 0, "limit": 50}
    repository.list_documents.assert_awaited_once_with(
        0, 50, DocumentStatus.FAILED, "text/csv", False
    )


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 201},
        {"offset": -1},
        {"status": "deleted"},
        {"needs_review": "maybe"},
    ],
)
async def test_list_rejects_invalid_query_parameters(
    client: AsyncClient, repository: Any, params: dict[str, object]
) -> None:
    """Out-of-range paging and unknown filter values are 422, unqueried."""
    response = await client.get("/documents", params=params)

    assert response.status_code == 422
    repository.list_documents.assert_not_awaited()


# ---------------------------------------------------------------- detail


async def test_get_document(client: AsyncClient, repository: Any) -> None:
    document = build_document()
    repository.get.return_value = document

    response = await client.get(f"/documents/{document.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(document.id)
    assert (body["status"], body["size_bytes"], body["sha256"]) == (
        "processed",
        1234,
        "ab" * 32,
    )


@pytest.mark.parametrize(
    "suffix",
    ["", "/pages", "/sheets", "/sheets/1", "/processing", "/validation-errors"],
)
async def test_unknown_document_is_404_on_every_read(
    client: AsyncClient, repository: Any, suffix: str
) -> None:
    """Every per-document read checks the document exists first."""
    repository.get.return_value = None

    response = await client.get(f"/documents/{MISSING}{suffix}")

    assert response.status_code == 404
    assert response.json() == {"detail": f"Document {MISSING} not found."}


@pytest.mark.parametrize("bad_id", ["123", "not-a-uuid", "0" * 40])
async def test_malformed_document_ids_are_422(
    client: AsyncClient, repository: Any, bad_id: str
) -> None:
    """IDs are validated as UUIDs before anything is looked up."""
    response = await client.get(f"/documents/{bad_id}")

    assert response.status_code == 422
    repository.get.assert_not_awaited()


# --------------------------------------------------------- pages & sheets


async def test_pages_in_order(client: AsyncClient, repository: Any) -> None:
    document = build_document()
    repository.get.return_value = document
    repository.list_pages.return_value = [
        build_page(document.id, 1, "First"),
        build_page(document.id, 2, "Second"),
    ]

    response = await client.get(f"/documents/{document.id}/pages")

    assert [p["text"] for p in response.json()] == ["First", "Second"]
    assert response.json()[0] == {
        "page_number": 1,
        "text": "First",
        "char_count": 5,
        "extraction_method": "text_layer",
        "needs_ocr": False,
        "ocr_confidence": None,
    }


async def test_sheet_rows_are_windowed_by_offset_and_limit(
    client: AsyncClient, repository: Any
) -> None:
    """offset/limit select a slice of rows; row_count stays the total."""
    document = build_document(file_type="text/csv")
    repository.get.return_value = document
    repository.get_sheet.return_value = build_sheet(
        document.id, [[f"r{i}", i] for i in range(10)]
    )

    response = await client.get(
        f"/documents/{document.id}/sheets/1", params={"offset": 8, "limit": 5}
    )

    body = response.json()
    assert (body["row_count"], body["offset"]) == (10, 8)
    assert body["rows"] == [["r8", 8], ["r9", 9]]
    repository.get_sheet.assert_awaited_once_with(document.id, 1)


async def test_sheet_list_hides_cell_data(client: AsyncClient, repository: Any) -> None:
    document = build_document(file_type="text/csv")
    repository.get.return_value = document
    repository.list_sheets.return_value = [build_sheet(document.id, [["a"]])]

    [sheet] = (await client.get(f"/documents/{document.id}/sheets")).json()

    assert "rows" not in sheet
    assert sheet["name"] == "Sheet1"


async def test_missing_sheet_is_404(client: AsyncClient, repository: Any) -> None:
    document = build_document()
    repository.get.return_value = document
    repository.get_sheet.return_value = None

    response = await client.get(f"/documents/{document.id}/sheets/9")

    assert response.status_code == 404
    assert "Sheet 9" in response.json()["detail"]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 10_001}, {"offset": -1}])
async def test_sheet_paging_is_validated(
    client: AsyncClient, repository: Any, params: dict[str, int]
) -> None:
    response = await client.get(f"/documents/{MISSING}/sheets/1", params=params)

    assert response.status_code == 422


# ------------------------------------------------------------- processing


async def test_processing_reports_progress_and_retry_state(
    client: AsyncClient, repository: Any
) -> None:
    """percent and retry_scheduled are derived from the job's fields."""
    document = build_document(status=DocumentStatus.UPLOADED)
    repository.get.return_value = document
    repository.latest_job.return_value = build_job(
        document.id,
        status=JobStatus.QUEUED,
        stage="ocr",
        progress_current=1,
        progress_total=4,
        attempts=1,
        error="TimeoutError: OCR timed out",
    )

    body = (await client.get(f"/documents/{document.id}/processing")).json()

    assert (body["status"], body["stage"], body["percent"]) == ("queued", "ocr", 25.0)
    assert body["retry_scheduled"] is True
    assert body["error"] == "TimeoutError: OCR timed out"


async def test_processing_without_a_job_is_404(
    client: AsyncClient, repository: Any
) -> None:
    document = build_document()
    repository.get.return_value = document
    repository.latest_job.return_value = None

    response = await client.get(f"/documents/{document.id}/processing")

    assert response.status_code == 404
    assert "no processing job" in response.json()["detail"]


async def test_retry_returns_the_new_job(client: AsyncClient, service: Any) -> None:
    document_id = uuid.uuid4()
    service.retry_processing.return_value = build_job(
        document_id, status=JobStatus.QUEUED, attempts=0
    )

    response = await client.post(f"/documents/{document_id}/processing/retry")

    assert response.status_code == 202
    assert (response.json()["status"], response.json()["retry_scheduled"]) == (
        "queued",
        False,
    )
    service.retry_processing.assert_awaited_once_with(document_id)


# ------------------------------------------------------ validation errors


async def test_resolve_a_validation_error(client: AsyncClient, repository: Any) -> None:
    """PATCH stores the requested state and returns the updated error."""
    document = build_document()
    error = build_validation_error(document.id)
    resolved = build_validation_error(document.id, id=error.id, resolved=True)
    repository.get.return_value = document
    repository.get_validation_error.return_value = error
    repository.set_resolved.return_value = resolved

    response = await client.patch(
        f"/documents/{document.id}/validation-errors/{error.id}",
        json={"resolved": True},
    )

    assert response.status_code == 200
    assert response.json()["resolved"] is True
    repository.set_resolved.assert_awaited_once_with(error, True)


async def test_resolving_an_unknown_error_is_404(
    client: AsyncClient, repository: Any
) -> None:
    repository.get.return_value = build_document()
    repository.get_validation_error.return_value = None

    response = await client.patch(
        f"/documents/{MISSING}/validation-errors/{uuid.uuid4()}",
        json={"resolved": True},
    )

    assert response.status_code == 404
    repository.set_resolved.assert_not_awaited()


@pytest.mark.parametrize("body", [{}, {"resolved": None}, {"resolved": [1]}])
async def test_resolve_requires_a_boolean(
    client: AsyncClient, repository: Any, body: dict[str, object]
) -> None:
    response = await client.patch(
        f"/documents/{MISSING}/validation-errors/{uuid.uuid4()}", json=body
    )

    assert response.status_code == 422
    repository.get.assert_not_awaited()


async def test_correction_body_is_validated_before_any_lookup(
    client: AsyncClient, repository: Any
) -> None:
    """PUT /invoice rejects an incomplete invoice without reading data."""
    response = await client.put(
        f"/documents/{MISSING}/invoice", json={"invoice_number": "X"}
    )

    assert response.status_code == 422
    repository.get.assert_not_awaited()


# ----------------------------------------------------------- original file


@pytest.fixture
def stored(settings: Settings) -> Any:
    """Write a document's original into the configured storage."""

    def write(file_type: str, file_name: str, content: bytes) -> Any:
        document = build_document(file_type=file_type, file_name=file_name)
        path: Path = settings.storage_dir / document.storage_path
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
        return document

    return write


async def test_file_is_served_inline_and_sandboxed(
    client: AsyncClient, repository: Any, stored: Any
) -> None:
    """Non-PDF originals are sandboxed and never MIME-sniffed."""
    repository.get.return_value = stored("text/csv", "data.csv", b"a,b\n1,2\n")

    response = await client.get(f"/documents/{repository.get.return_value.id}/file")

    assert response.status_code == 200
    assert response.content == b"a,b\n1,2\n"
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"].startswith("inline")
    assert response.headers["content-security-policy"] == "sandbox"
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_pdf_download_is_an_attachment_without_sandbox(
    client: AsyncClient, repository: Any, stored: Any
) -> None:
    """PDF viewers refuse sandboxed documents, so PDFs are not sandboxed."""
    repository.get.return_value = stored("application/pdf", "inv.pdf", b"%PDF-1.7")

    response = await client.get(
        f"/documents/{repository.get.return_value.id}/file",
        params={"download": "true"},
    )

    assert response.headers["content-disposition"].startswith("attachment")
    assert 'filename="inv.pdf"' in response.headers["content-disposition"]
    assert "content-security-policy" not in response.headers


async def test_missing_stored_file_is_404(client: AsyncClient, repository: Any) -> None:
    repository.get.return_value = build_document()

    response = await client.get(f"/documents/{repository.get.return_value.id}/file")

    assert response.status_code == 404
    assert "stored file" in response.json()["detail"]
