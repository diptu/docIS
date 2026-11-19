"""End-to-end: upload, background extraction, retrieval (SQLite database)."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import get_job_dispatcher
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.main import app
from app.worker.dispatch import no_dispatch
from app.worker.runtime import Runtime
from tests.conftest import run_due_jobs
from tests.helpers import (
    CSV_BYTES,
    MULTI_SHEET_XLS_BYTES,
    PNG_BYTES,
    concat_pdfs,
    encode,
    make_scanned_text_pdf,
    make_text_image,
    make_text_pdf,
    make_workbook,
    requires_tesseract,
)


@pytest.fixture
def run_worker(
    session_factory: async_sessionmaker[AsyncSession],
    make_runtime: Callable[..., Runtime],
) -> Callable[[], int]:
    """Run every due job through the same code path as the Celery task."""
    runtime = make_runtime()
    return lambda: asyncio.run(run_due_jobs(runtime, session_factory))


@pytest.fixture
def client(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> Iterator[TestClient]:
    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    settings = Settings(
        database_url="sqlite+aiosqlite://", storage_dir=tmp_path / "storage"
    )
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: settings
    # No broker in tests: jobs stay queued and run_worker executes them.
    app.dependency_overrides[get_job_dispatcher] = lambda: no_dispatch
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@requires_tesseract
def test_uploaded_pdf_text_is_extracted(
    client: TestClient, run_worker: Callable[[], int]
) -> None:
    pdf = concat_pdfs(
        make_text_pdf("Invoice 2026-001\nTotal due: 1,250.00 EUR"),
        make_scanned_text_pdf(["Signed delivery note"]),
    )

    upload = client.post("/documents", files=[("files", ("invoice.pdf", pdf))])
    run_worker()
    assert upload.status_code == 201
    document_id = upload.json()[0]["id"]
    assert upload.json()[0]["status"] == "uploaded"

    document = client.get(f"/documents/{document_id}")
    assert document.status_code == 200
    assert document.json()["status"] == "processed"

    pages = client.get(f"/documents/{document_id}/pages")
    assert pages.status_code == 200
    first, second = pages.json()
    assert first == {
        "page_number": 1,
        "text": "Invoice 2026-001\nTotal due: 1,250.00 EUR",
        "char_count": 40,
        "extraction_method": "text_layer",
        "needs_ocr": False,
        "ocr_confidence": None,
    }
    assert second["text"] == "Signed delivery note"
    assert second["extraction_method"] == "ocr"
    assert second["needs_ocr"] is False
    assert second["ocr_confidence"] > 80


@requires_tesseract
def test_uploaded_image_is_ocred(
    client: TestClient, run_worker: Callable[[], int]
) -> None:
    png = encode(make_text_image(["Receipt total 18.90"]), "PNG")

    upload = client.post("/documents", files=[("files", ("receipt.png", png))])
    run_worker()
    document_id = upload.json()[0]["id"]

    assert client.get(f"/documents/{document_id}").json()["status"] == "processed"
    [page] = client.get(f"/documents/{document_id}/pages").json()
    assert page["text"] == "Receipt total 18.90"
    assert page["extraction_method"] == "ocr"


def test_uploaded_csv_is_parsed_into_one_sheet(
    client: TestClient, run_worker: Callable[[], int]
) -> None:
    csv_bytes = b"\xef\xbb\xbfKunde;Betrag\r\nACME GmbH;1.250,00\r\nGlobex;980,40\r\n"

    upload = client.post("/documents", files=[("files", ("umsatz.csv", csv_bytes))])
    run_worker()
    document_id = upload.json()[0]["id"]

    assert client.get(f"/documents/{document_id}").json()["status"] == "processed"
    assert client.get(f"/documents/{document_id}/pages").json() == []
    [summary] = client.get(f"/documents/{document_id}/sheets").json()
    assert (summary["name"], summary["row_count"], summary["column_count"]) == (
        "umsatz",
        3,
        2,
    )
    sheet = client.get(f"/documents/{document_id}/sheets/1").json()
    assert sheet["rows"] == [
        ["Kunde", "Betrag"],
        ["ACME GmbH", "1.250,00"],
        ["Globex", "980,40"],
    ]


def test_uploaded_workbook_sheets_are_parsed(
    client: TestClient, run_worker: Callable[[], int]
) -> None:
    workbook = make_workbook(
        {
            "Orders": [["id", "amount"], *[[i, i * 10] for i in range(1, 6)]],
            "Hidden": [["internal"]],
        },
        hidden=("Hidden",),
    )

    upload = client.post("/documents", files=[("files", ("orders.xlsx", workbook))])
    run_worker()
    document_id = upload.json()[0]["id"]

    assert client.get(f"/documents/{document_id}").json()["status"] == "processed"
    assert client.get(f"/documents/{document_id}/sheets").json() == [
        {
            "sheet_number": 1,
            "name": "Orders",
            "row_count": 6,
            "column_count": 2,
            "is_hidden": False,
            "truncated": False,
        },
        {
            "sheet_number": 2,
            "name": "Hidden",
            "row_count": 1,
            "column_count": 1,
            "is_hidden": True,
            "truncated": False,
        },
    ]

    window = client.get(
        f"/documents/{document_id}/sheets/1", params={"offset": 2, "limit": 2}
    ).json()
    assert window["name"] == "Orders"
    assert window["offset"] == 2
    assert window["row_count"] == 6
    assert window["rows"] == [[2, 20], [3, 30]]


def test_uploaded_xls_sheets_are_parsed(
    client: TestClient, run_worker: Callable[[], int]
) -> None:
    upload = client.post(
        "/documents", files=[("files", ("legacy.xls", MULTI_SHEET_XLS_BYTES))]
    )
    run_worker()
    document_id = upload.json()[0]["id"]

    names = [s["name"] for s in client.get(f"/documents/{document_id}/sheets").json()]
    assert names == ["Revenue", "Meta", "Internal"]
    meta = client.get(f"/documents/{document_id}/sheets/2").json()
    assert meta["rows"][0] == ["Generated", "2026-11-19T00:00:00"]


def test_missing_sheet_returns_404(
    client: TestClient, run_worker: Callable[[], int]
) -> None:
    workbook = make_workbook({"Only": [["x"]]})
    upload = client.post("/documents", files=[("files", ("one.xlsx", workbook))])
    run_worker()
    document_id = upload.json()[0]["id"]

    response = client.get(f"/documents/{document_id}/sheets/2")

    assert response.status_code == 404
    assert "Sheet 2" in response.json()["detail"]


@pytest.mark.parametrize("limit", [0, 10_001])
def test_sheet_row_limit_is_validated(client: TestClient, limit: int) -> None:
    response = client.get(
        f"/documents/{uuid.uuid4()}/sheets/1", params={"limit": limit}
    )

    assert response.status_code == 422


@pytest.mark.parametrize("suffix", ["", "/pages", "/sheets", "/sheets/1"])
def test_unknown_document_returns_404(client: TestClient, suffix: str) -> None:
    missing = uuid.uuid4()

    response = client.get(f"/documents/{missing}{suffix}")

    assert response.status_code == 404
    assert str(missing) in response.json()["detail"]


def test_invalid_document_id_returns_422(client: TestClient) -> None:
    assert client.get("/documents/not-a-uuid").status_code == 422


def test_processing_progress_is_reported(
    client: TestClient, run_worker: Callable[[], int]
) -> None:
    workbook = make_workbook({"A": [["x"]], "B": [["y"]], "C": [["z"]]})
    upload = client.post("/documents", files=[("files", ("three.xlsx", workbook))])
    document_id = upload.json()[0]["id"]

    queued = client.get(f"/documents/{document_id}/processing").json()
    assert queued["status"] == "queued"
    assert (queued["attempts"], queued["percent"], queued["started_at"]) == (
        0,
        None,
        None,
    )

    run_worker()

    done = client.get(f"/documents/{document_id}/processing").json()
    assert done["status"] == "succeeded"
    assert done["stage"] == "saving"
    assert (done["progress_current"], done["progress_total"]) == (1, 1)
    assert done["percent"] == 100.0
    assert done["attempts"] == 1
    assert done["error"] is None
    assert done["started_at"] is not None and done["finished_at"] is not None


def test_failed_processing_reports_error(
    client: TestClient, run_worker: Callable[[], int], tmp_path: Path
) -> None:
    upload = client.post(
        "/documents", files=[("files", ("doc.pdf", make_text_pdf("Hello there")))]
    )
    document = upload.json()[0]
    # Simulate the stored original going missing before processing.
    stored = tmp_path / "storage" / document["storage_path"]
    stored.chmod(0o600)
    stored.unlink()

    run_worker()

    job = client.get(f"/documents/{document['id']}/processing").json()
    assert job["status"] == "failed"
    assert job["error"].startswith("FileNotFoundError")
    assert client.get(f"/documents/{document['id']}").json()["status"] == "failed"


def test_processing_endpoint_404s(
    client: TestClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    missing = uuid.uuid4()
    assert client.get(f"/documents/{missing}/processing").status_code == 404

    async def add_document_without_job() -> uuid.UUID:
        from app.models import Document

        async with session_factory() as session:
            document = Document(
                file_name="legacy.pdf",
                file_type="application/pdf",
                storage_path="x.pdf",
                status="processed",
            )
            session.add(document)
            await session.commit()
            return document.id

    legacy = asyncio.run(add_document_without_job())
    response = client.get(f"/documents/{legacy}/processing")
    assert response.status_code == 404
    assert "no processing job" in response.json()["detail"]


def _missing_original_upload(
    client: TestClient, run_worker: Callable[[], int], tmp_path: Path
) -> tuple[str, Path, bytes]:
    """Upload a PDF, remove its stored original, and let processing fail."""
    pdf = make_text_pdf("Recovered after a retry")
    document = client.post("/documents", files=[("files", ("doc.pdf", pdf))]).json()[0]
    stored = tmp_path / "storage" / document["storage_path"]
    stored.chmod(0o600)
    stored.unlink()
    run_worker()
    return document["id"], stored, pdf


def test_failed_processing_can_be_retried(
    client: TestClient, run_worker: Callable[[], int], tmp_path: Path
) -> None:
    document_id, stored, pdf = _missing_original_upload(client, run_worker, tmp_path)
    failed = client.get(f"/documents/{document_id}/processing").json()
    assert (failed["status"], failed["retry_scheduled"]) == ("failed", False)

    stored.write_bytes(pdf)  # the cause is fixed (e.g. storage restored)
    retry = client.post(f"/documents/{document_id}/processing/retry")

    assert retry.status_code == 202
    job = retry.json()
    assert job["id"] != failed["id"]
    assert (job["status"], job["attempts"], job["max_attempts"]) == ("queued", 0, 3)
    assert client.get(f"/documents/{document_id}").json()["status"] == "uploaded"

    run_worker()

    assert client.get(f"/documents/{document_id}/processing").json()["status"] == (
        "succeeded"
    )
    assert client.get(f"/documents/{document_id}").json()["status"] == "processed"
    [page] = client.get(f"/documents/{document_id}/pages").json()
    assert page["text"] == "Recovered after a retry"


def test_retry_is_rejected_unless_the_job_failed(
    client: TestClient, run_worker: Callable[[], int]
) -> None:
    upload = client.post("/documents", files=[("files", ("a.csv", CSV_BYTES))])
    document_id = upload.json()[0]["id"]

    queued = client.post(f"/documents/{document_id}/processing/retry")
    assert queued.status_code == 409
    assert "queued" in queued.json()["detail"]

    run_worker()
    done = client.post(f"/documents/{document_id}/processing/retry")
    assert done.status_code == 409
    assert "succeeded" in done.json()["detail"]


def test_retry_unknown_document_returns_404(client: TestClient) -> None:
    response = client.post(f"/documents/{uuid.uuid4()}/processing/retry")

    assert response.status_code == 404


def test_concurrent_retry_is_rejected_by_the_database(
    client: TestClient,
    run_worker: Callable[[], int],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If two retries race past the status check, the unique index stops one."""
    from app.repositories.document import DocumentRepository

    document_id, _, _ = _missing_original_upload(client, run_worker, tmp_path)
    assert client.post(f"/documents/{document_id}/processing/retry").status_code == 202

    # Simulate the race: the second request still sees the old failed job.
    # (A detached stand-in, so nothing is written back to the database.)
    from types import SimpleNamespace

    async def stale_view(self: DocumentRepository, doc_id: uuid.UUID) -> object:
        return SimpleNamespace(status="failed", kind="extract_content")

    monkeypatch.setattr(DocumentRepository, "latest_job", stale_view)
    second = client.post(f"/documents/{document_id}/processing/retry")

    assert second.status_code == 409
    assert "already has a queued or running job" in second.json()["detail"]


def test_retry_unprocessable_type_is_rejected(
    client: TestClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async def add_zip() -> uuid.UUID:
        from app.models import Document

        async with session_factory() as session:
            document = Document(
                file_name="a.zip",
                file_type="application/zip",
                storage_path="a.zip",
                status="uploaded",
            )
            session.add(document)
            await session.commit()
            return document.id

    response = client.post(f"/documents/{asyncio.run(add_zip())}/processing/retry")

    assert response.status_code == 409
    assert "not processed" in response.json()["detail"]


def test_retry_dispatches_the_new_job(
    client: TestClient, run_worker: Callable[[], int], tmp_path: Path
) -> None:
    sent: list[uuid.UUID] = []

    async def record(job_ids: object) -> None:
        sent.extend(job_ids)  # type: ignore[call-overload]

    document_id, _, _ = _missing_original_upload(client, run_worker, tmp_path)
    app.dependency_overrides[get_job_dispatcher] = lambda: record

    job = client.post(f"/documents/{document_id}/processing/retry").json()

    assert sent == [uuid.UUID(job["id"])]


# ------------------------------------------------------- invoice extraction


@pytest.fixture
def run_worker_with_llm(
    session_factory: async_sessionmaker[AsyncSession],
    make_runtime: Callable[..., Runtime],
) -> Callable[[object], int]:
    """Run due jobs with invoice extraction backed by the given fake LLM."""

    def run(llm: object) -> int:
        runtime = make_runtime(invoice_llm=llm)
        return asyncio.run(run_due_jobs(runtime, session_factory))

    return run


def test_invoice_is_extracted_after_content(
    client: TestClient, run_worker_with_llm: Callable[[object], int]
) -> None:
    from tests.fakes import FakeInvoiceLLM, sample_invoice

    pdf = make_text_pdf("ACME GmbH Invoice INV-2026-001\nTotal due: 1,487.50 EUR")
    document_id = client.post("/documents", files=[("files", ("inv.pdf", pdf))]).json()[
        0
    ]["id"]
    llm = FakeInvoiceLLM()

    assert client.get(f"/documents/{document_id}/invoice").status_code == 404
    assert run_worker_with_llm(llm) == 2  # content job, then invoice job

    # The model saw the extracted text, wrapped with page markers.
    assert llm.texts == [
        "--- Page 1 ---\nACME GmbH Invoice INV-2026-001\nTotal due: 1,487.50 EUR"
    ]
    job = client.get(f"/documents/{document_id}/processing").json()
    assert (job["kind"], job["status"], job["stage"]) == (
        "extract_invoice",
        "succeeded",
        "saving",
    )
    body = client.get(f"/documents/{document_id}/invoice").json()
    assert body["invoice"] == sample_invoice().model_dump(mode="json")
    assert body["invoice"]["line_items"][0] == {
        "description": "Consulting",
        "product_code": "SVC-1",
        "quantity": 10.0,
        "unit": "h",
        "unit_price": 125.0,
        "tax_rate": 19.0,
        "amount": 1250.0,
    }
    assert body["invoice"]["amount_due"] is None  # missing -> null
    assert body["extraction"] == {
        "model": "claude-opus-5-5",
        "input_tokens": 1200,
        "output_tokens": 300,
        "cache_read_tokens": 800,
    }
    assert client.get(f"/documents/{document_id}").json()["status"] == "processed"


def test_failed_extraction_keeps_content_and_retries_only_extraction(
    client: TestClient, run_worker_with_llm: Callable[[object], int]
) -> None:
    from app.extraction.llm import ExtractionRefusedError
    from tests.fakes import FakeInvoiceLLM

    pdf = make_text_pdf("Invoice INV-9 total 10.00 EUR")
    document_id = client.post("/documents", files=[("files", ("inv.pdf", pdf))]).json()[
        0
    ]["id"]

    run_worker_with_llm(FakeInvoiceLLM(error=ExtractionRefusedError("declined")))

    job = client.get(f"/documents/{document_id}/processing").json()
    assert (job["kind"], job["status"]) == ("extract_invoice", "failed")
    assert "declined" in job["error"]
    # Content extraction is unaffected.
    assert client.get(f"/documents/{document_id}").json()["status"] == "processed"
    assert client.get(f"/documents/{document_id}/pages").json()[0]["text"]

    retry = client.post(f"/documents/{document_id}/processing/retry").json()
    assert retry["kind"] == "extract_invoice"
    llm = FakeInvoiceLLM()
    assert run_worker_with_llm(llm) == 1  # only extraction re-ran, no OCR/parsing

    assert client.get(f"/documents/{document_id}/invoice").status_code == 200
    assert client.get(f"/documents/{document_id}").json()["status"] == "processed"


def test_invoice_endpoint_404s(client: TestClient) -> None:
    assert client.get(f"/documents/{uuid.uuid4()}/invoice").status_code == 404


def _upload_and_extract(
    client: TestClient, run: Callable[[object], int], pdf: bytes, llm: object
) -> str:
    document_id: str = client.post(
        "/documents", files=[("files", ("inv.pdf", pdf))]
    ).json()[0]["id"]
    run(llm)
    return document_id


def test_invoice_response_has_validation_duplicates_and_review(
    client: TestClient, run_worker_with_llm: Callable[[object], int]
) -> None:
    from tests.fakes import FakeInvoiceLLM, sample_invoice

    first = _upload_and_extract(
        client, run_worker_with_llm, make_text_pdf("Invoice one"), FakeInvoiceLLM()
    )
    body = client.get(f"/documents/{first}/invoice").json()
    assert body["validation"]["status"] == "passed"
    assert [c["code"] for c in body["validation"]["checks"]] == [
        "line_amounts",
        "lines_subtotal",
        "tax_amount",
        "total",
        "amount_due",
    ]
    assert body["duplicates"] == []
    assert body["review"] == {"needs_review": False, "errors": []}

    # The same invoice arrives again, with a wrong total typed in.
    wrong = sample_invoice(total_amount=1500.0)
    second = _upload_and_extract(
        client,
        run_worker_with_llm,
        make_text_pdf("Invoice one again"),
        FakeInvoiceLLM(wrong),
    )
    body = client.get(f"/documents/{second}/invoice").json()
    assert body["validation"]["status"] == "failed"
    total = next(c for c in body["validation"]["checks"] if c["code"] == "total")
    assert (total["status"], total["expected"], total["actual"]) == (
        "failed",
        "1487.5",
        "1500.0",
    )
    assert body["duplicates"] == [
        {"document_id": first, "file_name": "inv.pdf", "match": "same_invoice_number"}
    ]
    assert body["review"]["needs_review"] is True
    assert [
        (e["code"], e["severity"], e["resolved"]) for e in body["review"]["errors"]
    ] == [("total", "error", False), ("duplicate", "error", False)]

    # The first upload now lists the second as a duplicate, too.
    earlier = client.get(f"/documents/{first}/invoice").json()
    assert [d["document_id"] for d in earlier["duplicates"]] == [second]
    assert earlier["review"]["needs_review"] is True

    queue = client.get("/invoices/review").json()
    assert {item["document_id"] for item in queue} == {first, second}
    item = next(i for i in queue if i["document_id"] == second)
    assert (item["invoice_number"], item["vendor_name"], item["currency"]) == (
        "INV-2026-001",
        "ACME GmbH",
        "EUR",
    )
    assert float(item["total_amount"]) == 1500.0


def test_review_queue_paging_and_validation(
    client: TestClient, run_worker_with_llm: Callable[[object], int]
) -> None:
    from tests.fakes import FakeInvoiceLLM, sample_invoice

    for n in range(3):
        _upload_and_extract(
            client,
            run_worker_with_llm,
            make_text_pdf(f"Document {n}"),
            FakeInvoiceLLM(sample_invoice(invoice_number=f"N-{n}", is_invoice=False)),
        )

    assert len(client.get("/invoices/review").json()) == 3
    assert len(client.get("/invoices/review", params={"limit": 2}).json()) == 2
    assert len(client.get("/invoices/review", params={"offset": 2}).json()) == 1
    assert client.get("/invoices/review", params={"limit": 0}).status_code == 422


def test_validation_status_values() -> None:
    from app.api.routes.documents import validation_status
    from app.schema.document import ValidationCheckRead

    def check(status: str) -> ValidationCheckRead:
        return ValidationCheckRead(
            code="x", status=status, message="", expected=None, actual=None
        )

    assert validation_status([check("passed"), check("failed")]) == "failed"
    assert validation_status([check("passed"), check("skipped")]) == "passed"
    assert validation_status([check("skipped")]) == "not_checked"
    assert validation_status([]) == "not_checked"


def test_validation_errors_can_be_listed_resolved_and_reopened(
    client: TestClient, run_worker_with_llm: Callable[[object], int]
) -> None:
    from tests.fakes import FakeInvoiceLLM, sample_invoice

    bad = sample_invoice(total_amount=1500.0, uncertain_fields=["invoice_date"])
    document_id = _upload_and_extract(
        client, run_worker_with_llm, make_text_pdf("Invoice"), FakeInvoiceLLM(bad)
    )

    errors = client.get(f"/documents/{document_id}/validation-errors").json()
    assert [(e["code"], e["severity"]) for e in errors] == [
        ("total", "error"),  # errors before warnings
        ("model_uncertain", "warning"),
    ]
    assert errors[0]["details"] == {"expected": "1487.5", "actual": "1500.0"}
    assert {item["document_id"] for item in client.get("/invoices/review").json()} == {
        document_id
    }

    # Resolve both: the document leaves the review queue.
    for error in errors:
        response = client.patch(
            f"/documents/{document_id}/validation-errors/{error['id']}",
            json={"resolved": True},
        )
        assert response.status_code == 200
        assert response.json()["resolved"] is True
        assert response.json()["resolved_at"] is not None
    review = client.get(f"/documents/{document_id}/invoice").json()["review"]
    assert review["needs_review"] is False
    assert all(e["resolved"] for e in review["errors"])
    assert client.get("/invoices/review").json() == []

    # Reopen one: back in the queue, with only the unresolved error listed.
    client.patch(
        f"/documents/{document_id}/validation-errors/{errors[0]['id']}",
        json={"resolved": False},
    )
    [item] = client.get("/invoices/review").json()
    assert [e["code"] for e in item["errors"]] == ["total"]


def test_validation_error_endpoints_404(
    client: TestClient, run_worker_with_llm: Callable[[object], int]
) -> None:
    from tests.fakes import FakeInvoiceLLM

    missing = uuid.uuid4()
    assert client.get(f"/documents/{missing}/validation-errors").status_code == 404
    document_id = _upload_and_extract(
        client, run_worker_with_llm, make_text_pdf("Invoice"), FakeInvoiceLLM()
    )
    assert client.get(f"/documents/{document_id}/validation-errors").json() == []
    response = client.patch(
        f"/documents/{document_id}/validation-errors/{missing}", json={"resolved": True}
    )
    assert response.status_code == 404
    assert (
        client.patch(
            f"/documents/{document_id}/validation-errors/{missing}", json={}
        ).status_code
        == 422
    )


# ---------------------------------------------------- T3.1-3.3 review UI API


def test_list_documents_with_summaries_and_filters(
    client: TestClient, run_worker_with_llm: Callable[[object], int]
) -> None:
    from tests.fakes import FakeInvoiceLLM, sample_invoice

    good = _upload_and_extract(
        client, run_worker_with_llm, make_text_pdf("Invoice A"), FakeInvoiceLLM()
    )
    bad = _upload_and_extract(
        client,
        run_worker_with_llm,
        make_text_pdf("Invoice B"),
        FakeInvoiceLLM(sample_invoice(invoice_number="B-1", total_amount=9.0)),
    )
    csv_doc = client.post(
        "/documents", files=[("files", ("data.csv", CSV_BYTES))]
    ).json()[0]["id"]  # not processed yet

    page = client.get("/documents").json()
    assert page["total"] == 3
    assert [item["id"] for item in page["items"]] == [
        csv_doc,
        bad,
        good,
    ]  # newest first
    by_id = {item["id"]: item for item in page["items"]}
    assert by_id[good]["processing"] == {
        "kind": "extract_invoice",
        "status": "succeeded",
        "stage": "saving",
    }
    assert by_id[good]["invoice"]["invoice_number"] == "INV-2026-001"
    assert by_id[good]["invoice"]["corrected"] is False
    assert (by_id[good]["unresolved_errors"], by_id[bad]["unresolved_errors"]) == (0, 1)
    assert by_id[csv_doc]["invoice"] is None
    assert by_id[csv_doc]["processing"]["status"] == "queued"

    def ids(**params: object) -> list[str]:
        return [
            i["id"] for i in client.get("/documents", params=params).json()["items"]
        ]

    assert ids(needs_review="true") == [bad]
    assert ids(needs_review="false") == [csv_doc, good]
    assert ids(file_type="text/csv") == [csv_doc]
    assert ids(status="uploaded") == [csv_doc]
    assert ids(limit=1, offset=1) == [bad]
    assert client.get("/documents", params={"status": "bogus"}).status_code == 422


def test_original_file_is_served_inline_and_sandboxed(client: TestClient) -> None:
    pdf = make_text_pdf("Original bytes")
    document_id = client.post(
        "/documents", files=[("files", ("Q3 invoice.pdf", pdf))]
    ).json()[0]["id"]

    response = client.get(f"/documents/{document_id}/file")

    assert response.status_code == 200
    assert response.content == pdf
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline;")
    assert "Q3%20invoice.pdf" in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"
    # Browsers' PDF viewers refuse sandboxed documents.
    assert "content-security-policy" not in response.headers

    image_id = client.post(
        "/documents", files=[("files", ("scan.png", PNG_BYTES))]
    ).json()[0]["id"]
    image = client.get(f"/documents/{image_id}/file")
    assert image.headers["content-type"] == "image/png"
    assert image.headers["content-security-policy"] == "sandbox"

    download = client.get(f"/documents/{document_id}/file", params={"download": "true"})
    assert download.headers["content-disposition"].startswith("attachment;")


def test_file_endpoint_404s(client: TestClient, tmp_path: Path) -> None:
    assert client.get(f"/documents/{uuid.uuid4()}/file").status_code == 404

    document = client.post(
        "/documents", files=[("files", ("a.pdf", make_text_pdf("x")))]
    ).json()[0]
    stored = tmp_path / "storage" / document["storage_path"]
    stored.chmod(0o600)
    stored.unlink()

    response = client.get(f"/documents/{document['id']}/file")
    assert response.status_code == 404
    assert "missing" in response.json()["detail"]


def test_correct_invoice_via_api(
    client: TestClient, run_worker_with_llm: Callable[[object], int]
) -> None:
    from tests.fakes import FakeInvoiceLLM, sample_invoice

    document_id = _upload_and_extract(
        client,
        run_worker_with_llm,
        make_text_pdf("Invoice"),
        FakeInvoiceLLM(sample_invoice(total_amount=1500.0)),
    )
    before = client.get(f"/documents/{document_id}/invoice").json()
    assert before["review"]["needs_review"] is True
    assert before["corrected_at"] is None

    edited = before["invoice"] | {"total_amount": 1487.5}
    response = client.put(f"/documents/{document_id}/invoice", json=edited)

    assert response.status_code == 200
    after = response.json()
    assert after["invoice"]["total_amount"] == 1487.5
    assert after["corrected_at"] is not None
    assert after["validation"]["status"] == "passed"
    assert after["review"] == {"needs_review": False, "errors": []}
    [history] = client.get(f"/documents/{document_id}/invoice/corrections").json()
    assert history["changes"] == {"total_amount": {"old": 1500.0, "new": 1487.5}}
    listed = client.get("/documents").json()["items"][0]
    assert listed["invoice"]["corrected"] is True


def test_correction_rejects_invalid_invoices(
    client: TestClient, run_worker_with_llm: Callable[[object], int]
) -> None:
    from tests.fakes import FakeInvoiceLLM

    document_id = _upload_and_extract(
        client, run_worker_with_llm, make_text_pdf("Invoice"), FakeInvoiceLLM()
    )
    invoice = client.get(f"/documents/{document_id}/invoice").json()["invoice"]

    for bad in (
        invoice | {"invoice_date": "03/11/2026"},
        invoice | {"total_amount": "lots"},
        {k: v for k, v in invoice.items() if k != "currency"},
        invoice | {"surprise": True},
    ):
        assert (
            client.put(f"/documents/{document_id}/invoice", json=bad).status_code == 422
        )


def test_correction_and_history_404s(client: TestClient) -> None:
    from tests.fakes import sample_invoice

    payload = sample_invoice().model_dump(mode="json")
    missing = uuid.uuid4()
    assert client.put(f"/documents/{missing}/invoice", json=payload).status_code == 404
    assert client.get(f"/documents/{missing}/invoice/corrections").status_code == 404

    document_id = client.post(
        "/documents", files=[("files", ("a.pdf", make_text_pdf("x")))]
    ).json()[0]["id"]
    response = client.put(f"/documents/{document_id}/invoice", json=payload)
    assert response.status_code == 404
    assert "no extracted invoice" in response.json()["detail"]


def test_list_documents_when_there_are_none(client: TestClient) -> None:
    assert client.get("/documents").json() == {
        "items": [],
        "total": 0,
        "offset": 0,
        "limit": 50,
    }
