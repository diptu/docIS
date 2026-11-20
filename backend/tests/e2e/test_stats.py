"""E2E: GET /documents/stats over real processing outcomes."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.api import set_job_status
from tests.support.helpers import (
    CSV_BYTES,
)
from tests.support.worker import Worker


async def test_stats_when_there_are_no_documents(client: AsyncClient) -> None:
    assert (await client.get("/documents/stats")).json() == {
        "total": 0,
        "completed": 0,
        "failed": 0,
        "in_progress": 0,
        "avg_processing_seconds": None,
    }


async def test_stats_count_latest_jobs_and_average_processing_time(
    client: AsyncClient,
    run_worker: Worker,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from app.models import ProcessingJob

    fast, slow, broken = (
        d["id"]
        for d in (
            await client.post(
                "/documents",
                files=[("files", (f"{n}.csv", CSV_BYTES)) for n in ("a", "b", "c")],
            )
        ).json()
    )
    await run_worker()
    queued = await client.post("/documents", files=[("files", ("d.csv", CSV_BYTES))])
    assert queued.status_code == 201

    start = datetime(2026, 1, 1, tzinfo=UTC)

    async def set_times() -> None:
        async with session_factory() as session:
            for document_id, seconds in ((fast, 2.0), (slow, 4.5), (broken, 60.0)):
                await session.execute(
                    update(ProcessingJob)
                    .where(ProcessingJob.document_id == uuid.UUID(document_id))
                    .values(
                        started_at=start,
                        finished_at=start + timedelta(seconds=seconds),
                    )
                )
            await session.commit()

    await set_times()
    # A failed latest job counts as failed, and its time is not averaged.
    await set_job_status(session_factory, broken, "failed")

    stats = (await client.get("/documents/stats")).json()

    assert {k: v for k, v in stats.items() if k != "avg_processing_seconds"} == {
        "total": 4,
        "completed": 2,
        "failed": 1,
        "in_progress": 1,
    }
    assert stats["avg_processing_seconds"] == pytest.approx(3.25, abs=0.01)


async def test_delete_succeeds_when_the_stored_file_cannot_be_removed(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The record is the source of truth: once it is gone, a leftover file
    is only logged (it is unreachable), not reported as a failed delete."""
    from app.storage.local import LocalStorage

    async def broken_delete(self: LocalStorage, key: str) -> None:
        raise PermissionError("read-only file system")

    document_id = (
        await client.post("/documents", files=[("files", ("a.csv", CSV_BYTES))])
    ).json()[0]["id"]
    monkeypatch.setattr(LocalStorage, "delete", broken_delete)

    response = await client.delete(f"/documents/{document_id}")

    assert response.status_code == 204
    assert (await client.get(f"/documents/{document_id}")).status_code == 404
    assert "Could not remove stored file" in caplog.text
