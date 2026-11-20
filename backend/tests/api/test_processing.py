"""GET /processing/jobs and GET /documents/{id}/processing/history."""

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from app.repositories.document import JobEntry
from tests.support.builders import build_document, build_job


async def test_job_monitor_returns_jobs_with_documents_and_counts(
    client: AsyncClient, repository: Any
) -> None:
    document = build_document()
    running = build_job(
        document.id,
        status="running",
        stage="ocr",
        progress_current=1,
        progress_total=4,
        finished_at=None,
    )
    repository.list_all_jobs.return_value = (
        [JobEntry(job=running, file_name="scan.pdf", is_latest=True)],
        9,
        {"running": 1, "failed": 8},
    )

    response = await client.get(
        "/processing/jobs",
        params={
            "status": "running",
            "kind": "extract_content",
            "latest_only": "true",
            "offset": 3,
            "limit": 1,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["total"], body["offset"], body["limit"]) == (9, 3, 1)
    assert body["counts"] == {"running": 1, "failed": 8}
    (item,) = body["items"]
    assert item["id"] == str(running.id)
    assert item["document_id"] == str(document.id)
    assert (item["file_name"], item["is_latest"]) == ("scan.pdf", True)
    progress = (item["stage"], item["percent"], item["retry_scheduled"])
    assert progress == ("ocr", 25.0, False)
    repository.list_all_jobs.assert_awaited_once_with(
        3, 1, "running", "extract_content", True
    )


async def test_job_monitor_defaults(client: AsyncClient, repository: Any) -> None:
    repository.list_all_jobs.return_value = ([], 0, {})

    body = (await client.get("/processing/jobs")).json()

    assert body == {"items": [], "total": 0, "offset": 0, "limit": 50, "counts": {}}
    repository.list_all_jobs.assert_awaited_once_with(0, 50, None, None, False)


@pytest.mark.parametrize(
    "params",
    [
        {"status": "done"},
        {"kind": "ocr"},
        {"limit": 0},
        {"limit": 201},
        {"offset": -1},
        {"latest_only": "maybe"},
    ],
)
async def test_job_monitor_rejects_invalid_parameters(
    client: AsyncClient, repository: Any, params: dict[str, Any]
) -> None:
    response = await client.get("/processing/jobs", params=params)

    assert response.status_code == 422
    repository.list_all_jobs.assert_not_awaited()


async def test_history_lists_every_job_of_the_document(
    client: AsyncClient, repository: Any
) -> None:
    document = build_document()
    retried = build_job(document.id, status="queued", attempts=1, error="timeout")
    failed = build_job(document.id, status="failed", attempts=3, error="boom")
    repository.get.return_value = document
    repository.list_jobs.return_value = [retried, failed]

    response = await client.get(f"/documents/{document.id}/processing/history")

    assert response.status_code == 200
    body = response.json()
    assert [job["id"] for job in body] == [str(retried.id), str(failed.id)]
    assert (body[0]["retry_scheduled"], body[0]["error"]) == (True, "timeout")
    assert (body[1]["status"], body[1]["error"]) == ("failed", "boom")
    repository.list_jobs.assert_awaited_once_with(document.id)


async def test_history_of_an_unknown_document_is_404(
    client: AsyncClient, repository: Any
) -> None:
    repository.get.return_value = None

    response = await client.get(f"/documents/{uuid.uuid4()}/processing/history")

    assert response.status_code == 404
    repository.list_jobs.assert_not_awaited()


async def test_document_list_summary_carries_progress_and_last_error(
    client: AsyncClient, repository: Any
) -> None:
    document = build_document()
    waiting = build_job(
        document.id,
        status="queued",
        stage="ocr",
        progress_current=1,
        progress_total=2,
        attempts=2,
        error="TimeoutError: worker lost",
    )
    repository.list_documents.return_value = ([document], 1)
    repository.summaries.return_value = {document.id: (waiting, None, 0)}

    (item,) = (await client.get("/documents")).json()["items"]

    assert item["processing"] == {
        "kind": "extract_content",
        "status": "queued",
        "stage": "ocr",
        "percent": 50.0,
        "attempts": 2,
        "max_attempts": 3,
        "retry_scheduled": True,
        "error": "TimeoutError: worker lost",
    }
