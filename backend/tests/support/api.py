"""Client-side steps shared by E2E workflows."""

import uuid

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import ProcessingJob
from tests.support.worker import Worker


async def upload(client: AsyncClient, name: str, content: bytes) -> str:
    """Upload one file; returns the new document's ID."""
    response = await client.post("/documents", files=[("files", (name, content))])
    assert response.status_code == 201, response.text
    document_id: str = response.json()[0]["id"]
    return document_id


async def upload_and_extract(
    client: AsyncClient, run_worker: Worker, pdf: bytes, llm: object
) -> str:
    """Upload a PDF named inv.pdf and run content + invoice extraction."""
    document_id = await upload(client, "inv.pdf", pdf)
    await run_worker(llm)
    return document_id


async def set_job_status(
    session_factory: async_sessionmaker[AsyncSession], document_id: str, status: str
) -> None:
    """Force a document's jobs into ``status`` (e.g. simulate a running job)."""
    async with session_factory() as session:
        await session.execute(
            update(ProcessingJob)
            .where(ProcessingJob.document_id == uuid.UUID(document_id))
            .values(status=status)
        )
        await session.commit()
