"""End-to-end: upload, background extraction, retrieval (SQLite database)."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.main import app
from app.processing.processor import DocumentProcessor
from app.storage.local import LocalStorage
from app.worker.queue import JobQueue
from app.worker.runner import Worker
from tests.helpers import (
    CSV_BYTES,
    MULTI_SHEET_XLS_BYTES,
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
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> Callable[[], int]:
    """Process every queued job, as the background worker would."""
    worker = Worker(
        JobQueue(session_factory),
        DocumentProcessor(session_factory, LocalStorage(tmp_path / "storage")),
        progress_interval=0.01,
    )
    return lambda: asyncio.run(worker.run_until_idle())


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
        return SimpleNamespace(status="failed")

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
