"""
Extracted invoices, their line items, and validation errors.
"""

import datetime as dt
import uuid
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import TimestampMixin

# Money: up to 14 integer digits and 4 decimals, exact.
Money = Numeric(18, 4)
JsonType = JSON().with_variant(JSONB(), "postgresql")


class Invoice(TimestampMixin, Base):
    """The invoice extracted from a document (at most one per document).

    Every extracted field is a column; anything the document does not state
    is NULL. Re-extraction replaces the row (and its line items).
    """

    __tablename__ = "invoices"
    __table_args__ = (
        # Duplicate lookup: same vendor and normalised invoice number.
        Index(None, "vendor_key", "invoice_number_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    is_invoice: Mapped[bool] = mapped_column(Boolean, nullable=False)

    invoice_number: Mapped[str | None] = mapped_column(String(100), index=True)
    invoice_date: Mapped[dt.date | None] = mapped_column(Date)
    due_date: Mapped[dt.date | None] = mapped_column(Date)
    currency: Mapped[str | None] = mapped_column(String(10))
    purchase_order_number: Mapped[str | None] = mapped_column(String(100))
    payment_terms: Mapped[str | None] = mapped_column(String(255))

    vendor_name: Mapped[str | None] = mapped_column(String(255))
    vendor_address: Mapped[str | None] = mapped_column(Text)
    vendor_tax_id: Mapped[str | None] = mapped_column(String(100))
    vendor_email: Mapped[str | None] = mapped_column(String(255))
    vendor_phone: Mapped[str | None] = mapped_column(String(100))

    customer_name: Mapped[str | None] = mapped_column(String(255))
    customer_address: Mapped[str | None] = mapped_column(Text)
    customer_tax_id: Mapped[str | None] = mapped_column(String(100))
    customer_email: Mapped[str | None] = mapped_column(String(255))
    customer_phone: Mapped[str | None] = mapped_column(String(100))

    subtotal: Mapped[Decimal | None] = mapped_column(Money)
    tax: Mapped[Decimal | None] = mapped_column(Money)
    total_amount: Mapped[Decimal | None] = mapped_column(Money)
    amount_due: Mapped[Decimal | None] = mapped_column(Money)

    # Field names the model reported as uncertain.
    uncertain_fields: Mapped[list[str]] = mapped_column(
        JsonType, nullable=False, server_default=text("'[]'")
    )

    # Normalised keys for duplicate detection (see app.extraction.normalize).
    invoice_number_key: Mapped[str | None] = mapped_column(String(100))
    vendor_key: Mapped[str | None] = mapped_column(String(255))

    # Set when a person last corrected the extracted values.
    corrected_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    # Extraction metadata.
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False)

    line_items: Mapped[list["InvoiceLineItem"]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceLineItem.line_number",
        lazy="selectin",
    )


class InvoiceLineItem(TimestampMixin, Base):
    """One billed line of an invoice, in document order."""

    __tablename__ = "invoice_line_items"
    __table_args__ = (UniqueConstraint("invoice_id", "line_number"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-based
    description: Mapped[str | None] = mapped_column(Text)
    product_code: Mapped[str | None] = mapped_column(String(100))
    quantity: Mapped[Decimal | None] = mapped_column(Money)
    unit: Mapped[str | None] = mapped_column(String(50))
    unit_price: Mapped[Decimal | None] = mapped_column(Money)
    tax_rate: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))  # percent
    amount: Mapped[Decimal | None] = mapped_column(Money)

    invoice: Mapped[Invoice] = relationship(back_populates="line_items")


class Severity(StrEnum):
    # Must be resolved before the invoice can be trusted.
    ERROR = "error"
    # Worth a look; the data may still be right.
    WARNING = "warning"


class DocumentValidationError(TimestampMixin, Base):
    """A problem found in a document's extraction, awaiting review.

    A document needs review while it has any unresolved row. ``code`` is a
    failed amount check (``total``, ``tax_amount``, ...) or a review reason
    (``duplicate``, ``missing_fields``, ``low_ocr_confidence``, ...).
    """

    __tablename__ = "validation_errors"
    __table_args__ = (Index(None, "document_id", "resolved"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Set by the application with microsecond precision (SQLite's clock is
    # per-second), so errors list in the order they were raised.
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
        server_default=func.now(),
        nullable=False,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(
        JsonType, nullable=False, server_default=text("'{}'")
    )
    resolved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class InvoiceCorrection(Base):
    """One manual correction of a document's invoice: what changed.

    Linked to the document (not the invoice row, which is replaced on every
    save), so the history survives corrections and re-extraction.
    ``changes`` maps each changed field to ``{"old": ..., "new": ...}``.
    """

    __tablename__ = "invoice_corrections"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    changes: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
        server_default=func.now(),
        nullable=False,
    )
