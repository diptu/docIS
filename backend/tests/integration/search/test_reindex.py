import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
    Document,
    DocumentChunk,
    DocumentPage,
    DocumentSheet,
    DocumentStatus,
)
from app.search.reindex import reindex_all
from tests.support.fakes import FakeEmbedder

Factory = async_sessionmaker[AsyncSession]


async def test_reindex_builds_chunks_for_processed_documents(
    session_factory: Factory,
) -> None:
    processed, pending = uuid.uuid4(), uuid.uuid4()
    async with session_factory() as session:
        for document_id, status in (
            (processed, DocumentStatus.PROCESSED),
            (pending, DocumentStatus.UPLOADED),
        ):
            session.add(
                Document(
                    id=document_id,
                    file_name="a.pdf",
                    file_type="application/pdf",
                    storage_path="x",
                    status=status,
                )
            )
        await session.flush()
        session.add(
            DocumentPage(
                document_id=processed,
                page_number=1,
                text="Invoice ACME",
                char_count=12,
                extraction_method="text_layer",
                needs_ocr=False,
            )
        )
        session.add(
            DocumentSheet(
                document_id=processed,
                sheet_number=1,
                name="Lines",
                row_count=2,
                column_count=1,
                rows=[["item"], ["bolts"]],
                is_hidden=False,
                truncated=False,
            )
        )
        await session.commit()

    assert await reindex_all(session_factory, FakeEmbedder()) == (1, 2)
    assert await reindex_all(session_factory, None) == (1, 2)  # idempotent

    async with session_factory() as session:
        chunks = list(
            await session.scalars(
                select(DocumentChunk).order_by(DocumentChunk.chunk_index)
            )
        )
    assert [(c.page_number, c.sheet_name, c.embedding is None) for c in chunks] == [
        (1, None, True),
        (None, "Lines", True),
    ]
