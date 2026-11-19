"""
Per-page extracted content of a document.
"""

import uuid
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.base import TimestampMixin


class ExtractionMethod(StrEnum):
    """How a page's text was obtained."""

    TEXT_LAYER = "text_layer"
    OCR = "ocr"


class DocumentPage(TimestampMixin, Base):
    """Text extracted from one page of a document."""

    __tablename__ = "document_pages"
    __table_args__ = (UniqueConstraint("document_id", "page_number"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    extraction_method: Mapped[str] = mapped_column(String(20), nullable=False)
    # True when the page has no usable text layer and still needs OCR.
    needs_ocr: Mapped[bool] = mapped_column(Boolean, nullable=False, index=True)
    # Mean Tesseract word confidence (0-100) for OCR pages; NULL otherwise.
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
