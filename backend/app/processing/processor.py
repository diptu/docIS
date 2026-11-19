"""
Extraction of one document's content, as run by the background worker.
"""

import logging
import uuid
from functools import partial

from anyio import to_thread
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.document import Document, DocumentStatus
from app.models.document_chunk import DocumentChunk
from app.models.document_page import DocumentPage
from app.models.document_sheet import DocumentSheet
from app.processing.extract import PROCESSABLE_TYPES, extract_content
from app.processing.ocr import OcrConfig
from app.processing.progress import ProgressCallback, Stage, ignore_progress
from app.repositories.document import DocumentRepository
from app.search.chunking import Chunk, build_chunks
from app.search.embeddings import Embedder
from app.storage.local import LocalStorage

logger = logging.getLogger(__name__)


class DocumentNotProcessableError(Exception):
    """The document does not exist or has no extractor."""


class DocumentProcessor:
    """Extract content from a stored document and record the result.

    Uses its own database session, independent of any request.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        storage: LocalStorage,
        ocr_config: OcrConfig | None = None,
        embedder: Embedder | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.storage = storage
        self.ocr_config = ocr_config or OcrConfig()
        # None disables semantic search; chunks are still keyword-indexed.
        self.embedder = embedder

    @staticmethod
    def can_process(document: Document) -> bool:
        """True if the document's type has an extractor."""
        return document.file_type in PROCESSABLE_TYPES

    async def process(
        self, document_id: uuid.UUID, progress: ProgressCallback = ignore_progress
    ) -> None:
        """Extract a document's content and mark it processed.

        On failure nothing is saved and the exception propagates; the job
        runner decides whether to retry or to mark the document failed.
        """
        async with self.session_factory() as session:
            repository = DocumentRepository(session)
            document = await repository.get(document_id)
            if document is None:
                raise DocumentNotProcessableError(f"Document {document_id} not found.")
            if not self.can_process(document):
                raise DocumentNotProcessableError(
                    f"No extractor for file type {document.file_type!r}."
                )

            storage_path = document.storage_path
            media_type = document.file_type
            file_name = document.file_name
            await repository.set_status(document_id, DocumentStatus.PROCESSING)

            try:
                path = self.storage.path_for(storage_path)
                result = await to_thread.run_sync(
                    partial(
                        extract_content,
                        path,
                        media_type,
                        self.ocr_config,
                        file_name,
                        progress,
                    )
                )
                progress(Stage.SAVING, 0, 1)
                await repository.replace_pages(
                    document_id,
                    [
                        DocumentPage(
                            document_id=document_id,
                            page_number=page.page_number,
                            text=page.text,
                            char_count=page.char_count,
                            extraction_method=page.method,
                            needs_ocr=page.needs_ocr,
                            ocr_confidence=page.confidence,
                        )
                        for page in result.pages
                    ],
                )
                await repository.replace_sheets(
                    document_id,
                    [
                        DocumentSheet(
                            document_id=document_id,
                            sheet_number=sheet.sheet_number,
                            name=sheet.name,
                            row_count=sheet.row_count,
                            column_count=sheet.column_count,
                            rows=sheet.rows,
                            is_hidden=sheet.is_hidden,
                            truncated=sheet.truncated,
                        )
                        for sheet in result.sheets
                    ],
                )
                chunks = build_chunks(
                    [(page.page_number, page.text) for page in result.pages],
                    [(s.sheet_number, s.name, s.rows) for s in result.sheets],
                )
                await repository.replace_chunks(
                    document_id, await self._index(document_id, chunks, progress)
                )
                await repository.set_status(document_id, DocumentStatus.PROCESSED)
                progress(Stage.SAVING, 1, 1)
            except Exception:
                await session.rollback()
                raise

    async def _index(
        self,
        document_id: uuid.UUID,
        chunks: list[Chunk],
        progress: ProgressCallback,
    ) -> list[DocumentChunk]:
        """Search rows for the chunks, embedded if semantic search is on."""
        progress(Stage.INDEXING, 0, 1)
        embeddings: list[list[float] | None] = [None] * len(chunks)
        if self.embedder is not None and chunks:
            embeddings = list(
                await to_thread.run_sync(
                    self.embedder.embed_passages, [chunk.content for chunk in chunks]
                )
            )
        progress(Stage.INDEXING, 1, 1)
        return [
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
        ]
