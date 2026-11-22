"""E2E: following processing jobs and their errors through the job monitor
and a document's job history."""

from pathlib import Path

from httpx import AsyncClient

from tests.support.helpers import make_text_pdf
from tests.support.worker import Worker


async def test_a_failure_and_its_retry_are_tracked_end_to_end(
    client: AsyncClient, run_worker: Worker, tmp_path: Path
) -> None:
    pdf = make_text_pdf("Tracked document")
    document = (
        await client.post("/documents", files=[("files", ("tracked.pdf", pdf))])
    ).json()[0]
    document_id = document["id"]

    queued = (await client.get("/processing/jobs")).json()
    assert queued["counts"] == {"queued": 1}
    assert queued["items"][0]["file_name"] == "tracked.pdf"

    # Processing fails: the stored original is gone.
    stored = tmp_path / "storage" / document["storage_path"]
    stored.chmod(0o600)
    stored.unlink()
    await run_worker()

    failed = (await client.get("/processing/jobs", params={"status": "failed"})).json()
    (job,) = failed["items"]
    assert (job["document_id"], job["is_latest"]) == (document_id, True)
    assert job["error"].startswith("The original file is missing from storage")
    listed = (await client.get("/documents")).json()["items"][0]["processing"]
    assert (listed["status"], listed["error"]) == ("failed", job["error"])

    stored.write_bytes(pdf)
    assert (
        await client.post(f"/documents/{document_id}/processing/retry")
    ).status_code == 202
    await run_worker()

    history = (await client.get(f"/documents/{document_id}/processing/history")).json()
    assert [j["status"] for j in history] == ["succeeded", "failed"]
    assert history[1]["id"] == job["id"]

    everything = (await client.get("/processing/jobs")).json()
    assert everything["counts"] == {"succeeded": 1, "failed": 1}
    assert [j["is_latest"] for j in everything["items"]] == [True, False]
    current = (
        await client.get("/processing/jobs", params={"latest_only": "true"})
    ).json()
    assert current["counts"] == {"succeeded": 1}
    assert [j["status"] for j in current["items"]] == ["succeeded"]


async def test_history_shows_the_queued_job_before_any_worker_runs(
    client: AsyncClient,
) -> None:
    files = [("files", ("a.pdf", make_text_pdf("x")))]
    document_id = (await client.post("/documents", files=files)).json()[0]["id"]

    history = (await client.get(f"/documents/{document_id}/processing/history")).json()

    assert [j["status"] for j in history] == ["queued"]
