"""
Rebuild the search index from stored content: ``python -m app.search.reindex``.

Needed for documents processed before search existed, or after changing the
chunking or embedding model. Runs from the stored pages and sheets; nothing
is re-extracted.
"""

import asyncio
import uuid

from anyio import to_thread
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.db.session import AsyncSessionLocal, close_db
from app.models import Document, DocumentChunk, DocumentStatus
from app.repositories.document import DocumentRepository
from app.search.chunking import build_chunks
from app.search.embeddings import Embedder, get_embedder


async def reindex_document(
    session: AsyncSession, document_id: uuid.UUID, embedder: Embedder | None
) -> int:
    """Rebuild one document's chunks; returns how many were written."""
    repository = DocumentRepository(session)
    pages = await repository.list_pages(document_id)
    sheets = await repository.list_sheets(document_id)
    chunks = build_chunks(
        [(p.page_number, p.text) for p in pages],
        [(s.sheet_number, s.name, s.rows) for s in sheets],
    )
    embeddings: list[list[float] | None] = [None] * len(chunks)
    if embedder is not None and chunks:
        embeddings = list(
            await to_thread.run_sync(
                embedder.embed_passages, [c.content for c in chunks]
            )
        )
    await repository.replace_chunks(
        document_id,
        [
            DocumentChunk(
                document_id=document_id,
                chunk_index=index,
                page_number=chunk.page_number,
                sheet_number=chunk.sheet_number,
                sheet_name=chunk.sheet_name,
                content=chunk.content,
                embedding=embedding,
            )
            for index, (chunk, embedding) in enumerate(
                zip(chunks, embeddings, strict=True)
            )
        ],
    )
    await session.commit()
    return len(chunks)


async def reindex_all(
    session_factory: async_sessionmaker[AsyncSession], embedder: Embedder | None
) -> tuple[int, int]:
    """Reindex every processed document; returns (documents, chunks)."""
    async with session_factory() as session:
        ids = list(
            await session.scalars(
                select(Document.id).where(Document.status == DocumentStatus.PROCESSED)
            )
        )
    total = 0
    for document_id in ids:
        async with session_factory() as session:
            total += await reindex_document(session, document_id, embedder)
    return len(ids), total


async def main() -> None:  # pragma: no cover
    settings = get_settings()
    embedder = (
        get_embedder(settings.embedding_cache_dir)
        if settings.semantic_search_enabled
        else None
    )
    try:
        documents, chunks = await reindex_all(AsyncSessionLocal, embedder)
        print(f"Reindexed {documents} documents ({chunks} chunks).")
    finally:
        await close_db()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
