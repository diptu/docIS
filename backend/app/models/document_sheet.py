"""
Per-sheet structured content of a spreadsheet document.
"""

import uuid
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.base import TimestampMixin


class DocumentSheet(TimestampMixin, Base):
    """One worksheet of a spreadsheet, stored as a grid of cell values."""

    __tablename__ = "document_sheets"
    __table_args__ = (UniqueConstraint("document_id", "sheet_number"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    sheet_number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    column_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # Row-major grid: a list of rows, each a list of JSON scalars.
    rows: Mapped[list[list[Any]]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False
    )
    is_hidden: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # True when the sheet exceeded the stored-cell limit and was cut off.
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False)
