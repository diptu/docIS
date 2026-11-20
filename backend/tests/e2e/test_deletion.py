"""E2E: Deleting documents: single and bulk, files and extractions removed, running jobs
protected, duplicate flags resolved.
"""

import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.api import set_job_status, upload_and_extract
from tests.support.helpers import (
    CSV_BYTES,
    make_text_pdf,
)
from tests.support.worker import Worker


async def test_delete_document_removes_record_extractions_and_file(
    client: AsyncClient, run_worker: Worker, tmp_path: Path
) -> None:
    keep, gone = (
        await client.post(
            "/documents",
            files=[
                ("files", ("keep.csv", CSV_BYTES)),
                ("files", ("gone.csv", CSV_BYTES)),
            ],
        )
    ).json()
    await run_worker()
    stored = tmp_path / "storage" / gone["storage_path"]
    assert stored.exists()

    response = await client.delete(f"/documents/{gone['id']}")

    assert response.status_code == 204
    assert not stored.exists()
    assert (tmp_path / "storage" / keep["storage_path"]).exists()
    for suffix in ("", "/sheets", "/processing", "/file"):
        assert (await client.get(f"/documents/{gone['id']}{suffix}")).status_code == 404
    listed = (await client.get("/documents")).json()
    assert [d["id"] for d in listed["items"]] == [keep["id"]]


async def test_delete_unknown_document_returns_404(client: AsyncClient) -> None:
    assert (await client.delete(f"/documents/{uuid.uuid4()}")).status_code == 404


async def test_document_being_processed_cannot_be_deleted(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    document = await client.post("/documents", files=[("files", ("a.csv", CSV_BYTES))])
    document_id = document.json()[0]["id"]
    await set_job_status(session_factory, document_id, "running")

    response = await client.delete(f"/documents/{document_id}")

    assert response.status_code == 409
    assert "being processed" in response.json()["detail"]
    assert (await client.get(f"/documents/{document_id}")).status_code == 200


async def test_queued_document_can_be_deleted_and_its_job_is_skipped(
    client: AsyncClient, run_worker: Worker
) -> None:
    upload = await client.post("/documents", files=[("files", ("a.csv", CSV_BYTES))])
    document_id = upload.json()[0]["id"]

    assert (await client.delete(f"/documents/{document_id}")).status_code == 204
    assert await run_worker() == 0


async def test_bulk_delete_reports_each_document(
    client: AsyncClient,
    run_worker: Worker,
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    first, second, busy = (
        await client.post(
            "/documents",
            files=[("files", (f"{n}.csv", CSV_BYTES)) for n in ("a", "b", "c")],
        )
    ).json()
    await run_worker()
    await set_job_status(session_factory, busy["id"], "running")
    missing = str(uuid.uuid4())

    response = await client.post(
        "/documents/delete",
        json={"ids": [first["id"], missing, busy["id"], second["id"], first["id"]]},
    )

    assert response.status_code == 200
    assert response.json() == {
        "deleted": [first["id"], second["id"]],
        "in_progress": [busy["id"]],
        "not_found": [missing],
    }
    assert [d["id"] for d in (await client.get("/documents")).json()["items"]] == [
        busy["id"]
    ]
    assert not (tmp_path / "storage" / first["storage_path"]).exists()
    assert (tmp_path / "storage" / busy["storage_path"]).exists()


@pytest.mark.parametrize("ids", [[], ["not-a-uuid"], [str(uuid.uuid4())] * 201])
async def test_bulk_delete_validates_ids(client: AsyncClient, ids: list[str]) -> None:
    assert (
        await client.post("/documents/delete", json={"ids": ids})
    ).status_code == 422


async def test_deleting_a_duplicate_resolves_the_flag_on_the_original(
    client: AsyncClient, run_worker: Worker
) -> None:
    from tests.support.fakes import FakeInvoiceLLM

    first = await upload_and_extract(
        client, run_worker, make_text_pdf("Invoice one"), FakeInvoiceLLM()
    )
    second = await upload_and_extract(
        client, run_worker, make_text_pdf("Invoice again"), FakeInvoiceLLM()
    )
    assert (await client.get(f"/documents/{first}/invoice")).json()["review"][
        "needs_review"
    ]

    assert (await client.delete(f"/documents/{second}")).status_code == 204

    body = (await client.get(f"/documents/{first}/invoice")).json()
    assert body["duplicates"] == []
    assert body["review"]["needs_review"] is False
    [error] = body["review"]["errors"]
    assert (error["code"], error["resolved"]) == ("duplicate", True)
    assert error["details"]["document_ids"] == []
