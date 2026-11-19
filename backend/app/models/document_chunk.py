"""
Searchable chunks of a document's extracted content.
"""

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.base import TimestampMixin

# bge-small-en-v1.5 produces 384-dimensional, unit-length vectors.
EMBEDDING_DIM = 384
# Text search configuration: English stemming ("invoices" matches "invoice").
TEXT_SEARCH_CONFIG = "english"


class DocumentChunk(TimestampMixin, Base):
    """A passage of page text or spreadsheet rows, indexed for search.

    Keyword search uses a full-text index over ``content``; semantic search
    uses ``embedding`` (cosine similarity, HNSW index). Both indexes exist on
    PostgreSQL only; other databases fall back to slower scans.
    """

    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index"),
        Index(
            "ix_document_chunks_content_fts",
            text(f"to_tsvector('{TEXT_SEARCH_CONFIG}', content)"),
            postgresql_using="gin",
        ).ddl_if(dialect="postgresql"),
        Index(
            "ix_document_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ).ddl_if(dialect="postgresql"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)
    sheet_number: Mapped[int | None] = mapped_column(Integer)
    sheet_name: Mapped[str | None] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # NULL when semantic search is disabled (keyword search still works).
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIM).with_variant(JSON(), "sqlite")
    )
