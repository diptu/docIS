"""E2E: Upload → background content extraction (text layer, OCR, sheets) → retrieval;
progress, failures and retries.
"""

import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import get_job_dispatcher
from app.main import app
from tests.support.helpers import (
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
from tests.support.worker import Worker


@requires_tesseract
async def test_uploaded_pdf_text_is_extracted(
    client: AsyncClient, run_worker: Worker
) -> None:
    pdf = concat_pdfs(
        make_text_pdf("Invoice 2026-001\nTotal due: 1,250.00 EUR"),
        make_scanned_text_pdf(["Signed delivery note"]),
    )

    upload = await client.post("/documents", files=[("files", ("invoice.pdf", pdf))])
    await run_worker()
    assert upload.status_code == 201
    document_id = upload.json()[0]["id"]
    assert upload.json()[0]["status"] == "uploaded"

    document = await client.get(f"/documents/{document_id}")
    assert document.status_code == 200
    assert document.json()["status"] == "processed"

    pages = await client.get(f"/documents/{document_id}/pages")
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
async def test_uploaded_image_is_ocred(client: AsyncClient, run_worker: Worker) -> None:
    png = encode(make_text_image(["Receipt total 18.90"]), "PNG")

    upload = await client.post("/documents", files=[("files", ("receipt.png", png))])
    await run_worker()
    document_id = upload.json()[0]["id"]

    assert (await client.get(f"/documents/{document_id}")).json()[
        "status"
    ] == "processed"
    [page] = (await client.get(f"/documents/{document_id}/pages")).json()
    assert page["text"] == "Receipt total 18.90"
    assert page["extraction_method"] == "ocr"


async def test_uploaded_csv_is_parsed_into_one_sheet(
    client: AsyncClient, run_worker: Worker
) -> None:
    csv_bytes = b"\xef\xbb\xbfKunde;Betrag\r\nACME GmbH;1.250,00\r\nGlobex;980,40\r\n"

    upload = await client.post(
        "/documents", files=[("files", ("umsatz.csv", csv_bytes))]
    )
    await run_worker()
    document_id = upload.json()[0]["id"]

    assert (await client.get(f"/documents/{document_id}")).json()[
        "status"
    ] == "processed"
    assert (await client.get(f"/documents/{document_id}/pages")).json() == []
    [summary] = (await client.get(f"/documents/{document_id}/sheets")).json()
    assert (summary["name"], summary["row_count"], summary["column_count"]) == (
        "umsatz",
        3,
        2,
    )
    sheet = (await client.get(f"/documents/{document_id}/sheets/1")).json()
    assert sheet["rows"] == [
        ["Kunde", "Betrag"],
        ["ACME GmbH", "1.250,00"],
        ["Globex", "980,40"],
    ]


async def test_uploaded_workbook_sheets_are_parsed(
    client: AsyncClient, run_worker: Worker
) -> None:
    workbook = make_workbook(
        {
            "Orders": [["id", "amount"], *[[i, i * 10] for i in range(1, 6)]],
            "Hidden": [["internal"]],
        },
        hidden=("Hidden",),
    )

    upload = await client.post(
        "/documents", files=[("files", ("orders.xlsx", workbook))]
    )
    await run_worker()
    document_id = upload.json()[0]["id"]

    assert (await client.get(f"/documents/{document_id}")).json()[
        "status"
    ] == "processed"
    assert (await client.get(f"/documents/{document_id}/sheets")).json() == [
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

    window = (
        await client.get(
            f"/documents/{document_id}/sheets/1", params={"offset": 2, "limit": 2}
        )
    ).json()
    assert window["name"] == "Orders"
    assert window["offset"] == 2
    assert window["row_count"] == 6
    assert window["rows"] == [[2, 20], [3, 30]]


async def test_uploaded_xls_sheets_are_parsed(
    client: AsyncClient, run_worker: Worker
) -> None:
    upload = await client.post(
        "/documents", files=[("files", ("legacy.xls", MULTI_SHEET_XLS_BYTES))]
    )
    await run_worker()
    document_id = upload.json()[0]["id"]

    names = [
        s["name"] for s in (await client.get(f"/documents/{document_id}/sheets")).json()
    ]
    assert names == ["Revenue", "Meta", "Internal"]
    meta = (await client.get(f"/documents/{document_id}/sheets/2")).json()
    assert meta["rows"][0] == ["Generated", "2026-11-19T00:00:00"]


async def test_missing_sheet_returns_404(
    client: AsyncClient, run_worker: Worker
) -> None:
    workbook = make_workbook({"Only": [["x"]]})
    upload = await client.post("/documents", files=[("files", ("one.xlsx", workbook))])
    await run_worker()
    document_id = upload.json()[0]["id"]

    response = await client.get(f"/documents/{document_id}/sheets/2")

    assert response.status_code == 404
    assert "Sheet 2" in response.json()["detail"]


@pytest.mark.parametrize("limit", [0, 10_001])
async def test_sheet_row_limit_is_validated(client: AsyncClient, limit: int) -> None:
    response = await client.get(
        f"/documents/{uuid.uuid4()}/sheets/1", params={"limit": limit}
    )

    assert response.status_code == 422


@pytest.mark.parametrize("suffix", ["", "/pages", "/sheets", "/sheets/1"])
async def test_unknown_document_returns_404(client: AsyncClient, suffix: str) -> None:
    missing = uuid.uuid4()

    response = await client.get(f"/documents/{missing}{suffix}")

    assert response.status_code == 404
    assert str(missing) in response.json()["detail"]


async def test_invalid_document_id_returns_422(client: AsyncClient) -> None:
    assert (await client.get("/documents/not-a-uuid")).status_code == 422


async def test_processing_progress_is_reported(
    client: AsyncClient, run_worker: Worker
) -> None:
    workbook = make_workbook({"A": [["x"]], "B": [["y"]], "C": [["z"]]})
    upload = await client.post(
        "/documents", files=[("files", ("three.xlsx", workbook))]
    )
    document_id = upload.json()[0]["id"]

    queued = (await client.get(f"/documents/{document_id}/processing")).json()
    assert queued["status"] == "queued"
    assert (queued["attempts"], queued["percent"], queued["started_at"]) == (
        0,
        None,
        None,
    )

    await run_worker()

    done = (await client.get(f"/documents/{document_id}/processing")).json()
    assert done["status"] == "succeeded"
    assert done["stage"] == "saving"
    assert (done["progress_current"], done["progress_total"]) == (1, 1)
    assert done["percent"] == 100.0
    assert done["attempts"] == 1
    assert done["error"] is None
    assert done["started_at"] is not None and done["finished_at"] is not None


async def test_failed_processing_reports_error(
    client: AsyncClient, run_worker: Worker, tmp_path: Path
) -> None:
    upload = await client.post(
        "/documents", files=[("files", ("doc.pdf", make_text_pdf("Hello there")))]
    )
    document = upload.json()[0]
    # Simulate the stored original going missing before processing.
    stored = tmp_path / "storage" / document["storage_path"]
    stored.chmod(0o600)
    stored.unlink()

    await run_worker()

    job = (await client.get(f"/documents/{document['id']}/processing")).json()
    assert job["status"] == "failed"
    assert job["error"].startswith("The original file is missing from storage")
    assert (await client.get(f"/documents/{document['id']}")).json()[
        "status"
    ] == "failed"


async def test_processing_endpoint_404s(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    missing = uuid.uuid4()
    assert (await client.get(f"/documents/{missing}/processing")).status_code == 404

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

    legacy = await add_document_without_job()
    response = await client.get(f"/documents/{legacy}/processing")
    assert response.status_code == 404
    assert "no processing job" in response.json()["detail"]


async def _missing_original_upload(
    client: AsyncClient, run_worker: Worker, tmp_path: Path
) -> tuple[str, Path, bytes]:
    """Upload a PDF, remove its stored original, and let processing fail."""
    pdf = make_text_pdf("Recovered after a retry")
    document = (
        await client.post("/documents", files=[("files", ("doc.pdf", pdf))])
    ).json()[0]
    stored = tmp_path / "storage" / document["storage_path"]
    stored.chmod(0o600)
    stored.unlink()
    await run_worker()
    return document["id"], stored, pdf


async def test_failed_processing_can_be_retried(
    client: AsyncClient, run_worker: Worker, tmp_path: Path
) -> None:
    document_id, stored, pdf = await _missing_original_upload(
        client, run_worker, tmp_path
    )
    failed = (await client.get(f"/documents/{document_id}/processing")).json()
    assert (failed["status"], failed["retry_scheduled"]) == ("failed", False)

    stored.write_bytes(pdf)  # the cause is fixed (e.g. storage restored)
    retry = await client.post(f"/documents/{document_id}/processing/retry")

    assert retry.status_code == 202
    job = retry.json()
    assert job["id"] != failed["id"]
    assert (job["status"], job["attempts"], job["max_attempts"]) == ("queued", 0, 3)
    assert (await client.get(f"/documents/{document_id}")).json()[
        "status"
    ] == "uploaded"

    await run_worker()

    assert (await client.get(f"/documents/{document_id}/processing")).json()[
        "status"
    ] == ("succeeded")
    assert (await client.get(f"/documents/{document_id}")).json()[
        "status"
    ] == "processed"
    [page] = (await client.get(f"/documents/{document_id}/pages")).json()
    assert page["text"] == "Recovered after a retry"


async def test_retry_is_rejected_unless_the_job_failed(
    client: AsyncClient, run_worker: Worker
) -> None:
    upload = await client.post("/documents", files=[("files", ("a.csv", CSV_BYTES))])
    document_id = upload.json()[0]["id"]

    queued = await client.post(f"/documents/{document_id}/processing/retry")
    assert queued.status_code == 409
    assert "queued" in queued.json()["detail"]

    await run_worker()
    done = await client.post(f"/documents/{document_id}/processing/retry")
    assert done.status_code == 409
    assert "succeeded" in done.json()["detail"]


async def test_retry_unknown_document_returns_404(client: AsyncClient) -> None:
    response = await client.post(f"/documents/{uuid.uuid4()}/processing/retry")

    assert response.status_code == 404


async def test_concurrent_retry_is_rejected_by_the_database(
    client: AsyncClient,
    run_worker: Worker,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If two retries race past the status check, the unique index stops one."""
    from app.repositories.document import DocumentRepository

    document_id, _, _ = await _missing_original_upload(client, run_worker, tmp_path)
    assert (
        await client.post(f"/documents/{document_id}/processing/retry")
    ).status_code == 202

    # Simulate the race: the second request still sees the old failed job.
    # (A detached stand-in, so nothing is written back to the database.)
    from types import SimpleNamespace

    async def stale_view(self: DocumentRepository, doc_id: uuid.UUID) -> object:
        return SimpleNamespace(status="failed", kind="extract_content")

    monkeypatch.setattr(DocumentRepository, "latest_job", stale_view)
    second = await client.post(f"/documents/{document_id}/processing/retry")

    assert second.status_code == 409
    assert "already has a queued or running job" in second.json()["detail"]


async def test_retry_unprocessable_type_is_rejected(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
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

    response = await client.post(f"/documents/{await add_zip()}/processing/retry")

    assert response.status_code == 409
    assert "not processed" in response.json()["detail"]


async def test_retry_dispatches_the_new_job(
    client: AsyncClient, run_worker: Worker, tmp_path: Path
) -> None:
    sent: list[uuid.UUID] = []

    async def record(job_ids: object) -> None:
        sent.extend(job_ids)  # type: ignore[call-overload]

    document_id, _, _ = await _missing_original_upload(client, run_worker, tmp_path)
    app.dependency_overrides[get_job_dispatcher] = lambda: record

    job = (await client.post(f"/documents/{document_id}/processing/retry")).json()

    assert sent == [uuid.UUID(job["id"])]
