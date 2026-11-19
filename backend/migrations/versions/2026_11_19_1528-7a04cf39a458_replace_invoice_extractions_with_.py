"""replace invoice extractions with invoices line items and validation errors

Revision ID: 7a04cf39a458
Revises: 2f661b0eb825
Create Date: 2026-11-19 15:28:55.964766+00:00

"""

import datetime as dt
import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "7a04cf39a458"
down_revision: str | Sequence[str] | None = "2f661b0eb825"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


WARNING_CODES = {
    "possible_duplicate",
    "low_ocr_confidence",
    "model_uncertain",
    "not_invoice",
}


def _copy_extractions() -> None:
    """Move rows from invoice_extractions (JSON) into the new tables."""
    bind = op.get_bind()
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    old = sa.table(
        "invoice_extractions",
        sa.column("document_id", sa.Uuid()),
        sa.column("is_invoice", sa.Boolean()),
        sa.column("invoice_number_key", sa.String()),
        sa.column("vendor_key", sa.String()),
        sa.column("review_reasons", json_type),
        sa.column("data", json_type),
        sa.column("model", sa.String()),
        sa.column("input_tokens", sa.Integer()),
        sa.column("output_tokens", sa.Integer()),
        sa.column("cache_read_tokens", sa.Integer()),
    )

    def column_type(name: str) -> sa.types.TypeEngine[Any]:
        if name == "uncertain_fields":
            return json_type
        return sa.Numeric() if name in MONEY_COLUMNS else sa.String()

    invoices = sa.table(
        "invoices",
        *(sa.column(name, column_type(name)) for name in INVOICE_COLUMNS),
        sa.column("id", sa.Uuid()),
        sa.column("document_id", sa.Uuid()),
        sa.column("is_invoice", sa.Boolean()),
        sa.column("invoice_date", sa.Date()),
        sa.column("due_date", sa.Date()),
        sa.column("input_tokens", sa.Integer()),
        sa.column("output_tokens", sa.Integer()),
        sa.column("cache_read_tokens", sa.Integer()),
    )
    lines = sa.table(
        "invoice_line_items",
        sa.column("id", sa.Uuid()),
        sa.column("invoice_id", sa.Uuid()),
        sa.column("line_number", sa.Integer()),
        *(
            sa.column(name, sa.Numeric() if name in LINE_NUMBERS else sa.String())
            for name in LINE_COLUMNS
        ),
    )
    errors = sa.table(
        "validation_errors",
        sa.column("id", sa.Uuid()),
        sa.column("document_id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("severity", sa.String()),
        sa.column("message", sa.Text()),
        sa.column("details", json_type),
    )

    def day(value: str | None) -> dt.date | None:
        return dt.date.fromisoformat(value) if value else None

    def dec(value: object) -> Decimal | None:
        # Via str, so 0.1 stays exactly 0.1.
        return None if value is None else Decimal(str(value))

    for row in bind.execute(sa.select(old)).mappings():
        data = row["data"]
        vendor, customer = data.get("vendor") or {}, data.get("customer") or {}
        invoice_id = uuid.uuid4()
        bind.execute(
            invoices.insert().values(
                id=invoice_id,
                document_id=row["document_id"],
                is_invoice=row["is_invoice"],
                invoice_number=data.get("invoice_number"),
                invoice_date=day(data.get("invoice_date")),
                due_date=day(data.get("due_date")),
                currency=data.get("currency"),
                purchase_order_number=data.get("purchase_order_number"),
                payment_terms=data.get("payment_terms"),
                **{f"vendor_{k}": vendor.get(k) for k in PARTY_FIELDS},
                **{f"customer_{k}": customer.get(k) for k in PARTY_FIELDS},
                subtotal=dec(data.get("subtotal")),
                tax=dec(data.get("tax_amount")),
                total_amount=dec(data.get("total_amount")),
                amount_due=dec(data.get("amount_due")),
                uncertain_fields=data.get("uncertain_fields") or [],
                invoice_number_key=row["invoice_number_key"],
                vendor_key=row["vendor_key"],
                model=row["model"],
                input_tokens=row["input_tokens"],
                output_tokens=row["output_tokens"],
                cache_read_tokens=row["cache_read_tokens"],
            )
        )
        for number, item in enumerate(data.get("line_items") or [], start=1):
            bind.execute(
                lines.insert().values(
                    id=uuid.uuid4(),
                    invoice_id=invoice_id,
                    line_number=number,
                    description=item.get("description"),
                    product_code=item.get("product_code"),
                    quantity=dec(item.get("quantity")),
                    unit=item.get("unit"),
                    unit_price=dec(item.get("unit_price")),
                    tax_rate=dec(item.get("tax_rate")),
                    amount=dec(item.get("amount")),
                )
            )
        for reason in row["review_reasons"] or []:
            bind.execute(
                errors.insert().values(
                    id=uuid.uuid4(),
                    document_id=row["document_id"],
                    code=reason["code"],
                    severity="warning" if reason["code"] in WARNING_CODES else "error",
                    message=reason["message"],
                    details=reason.get("details") or {},
                )
            )


PARTY_FIELDS = ("name", "address", "tax_id", "email", "phone")
INVOICE_COLUMNS = (
    "invoice_number",
    "currency",
    "purchase_order_number",
    "payment_terms",
    *(f"vendor_{k}" for k in PARTY_FIELDS),
    *(f"customer_{k}" for k in PARTY_FIELDS),
    "subtotal",
    "tax",
    "total_amount",
    "amount_due",
    "uncertain_fields",
    "invoice_number_key",
    "vendor_key",
    "model",
)
MONEY_COLUMNS = {"subtotal", "tax", "total_amount", "amount_due"}
LINE_NUMBERS = {"quantity", "unit_price", "tax_rate", "amount"}
LINE_COLUMNS = (
    "description",
    "product_code",
    "quantity",
    "unit",
    "unit_price",
    "tax_rate",
    "amount",
)


def upgrade() -> None:
    """Upgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "invoices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("is_invoice", sa.Boolean(), nullable=False),
        sa.Column("invoice_number", sa.String(length=100), nullable=True),
        sa.Column("invoice_date", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(length=10), nullable=True),
        sa.Column("purchase_order_number", sa.String(length=100), nullable=True),
        sa.Column("payment_terms", sa.String(length=255), nullable=True),
        sa.Column("vendor_name", sa.String(length=255), nullable=True),
        sa.Column("vendor_address", sa.Text(), nullable=True),
        sa.Column("vendor_tax_id", sa.String(length=100), nullable=True),
        sa.Column("vendor_email", sa.String(length=255), nullable=True),
        sa.Column("vendor_phone", sa.String(length=100), nullable=True),
        sa.Column("customer_name", sa.String(length=255), nullable=True),
        sa.Column("customer_address", sa.Text(), nullable=True),
        sa.Column("customer_tax_id", sa.String(length=100), nullable=True),
        sa.Column("customer_email", sa.String(length=255), nullable=True),
        sa.Column("customer_phone", sa.String(length=100), nullable=True),
        sa.Column("subtotal", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("tax", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("total_amount", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("amount_due", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column(
            "uncertain_fields",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column("invoice_number_key", sa.String(length=100), nullable=True),
        sa.Column("vendor_key", sa.String(length=255), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_invoices_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invoices")),
        sa.UniqueConstraint("document_id", name=op.f("uq_invoices_document_id")),
    )
    op.create_index(
        op.f("ix_invoices_invoice_number"), "invoices", ["invoice_number"], unique=False
    )
    op.create_index(
        op.f("ix_invoices_vendor_key_invoice_number_key"),
        "invoices",
        ["vendor_key", "invoice_number_key"],
        unique=False,
    )
    op.create_table(
        "validation_errors",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "details",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "resolved", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_validation_errors_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_validation_errors")),
    )
    op.create_index(
        op.f("ix_validation_errors_document_id_resolved"),
        "validation_errors",
        ["document_id", "resolved"],
        unique=False,
    )
    op.create_table(
        "invoice_line_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("product_code", sa.String(length=100), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("unit", sa.String(length=50), nullable=True),
        sa.Column("unit_price", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("tax_rate", sa.Numeric(precision=7, scale=4), nullable=True),
        sa.Column("amount", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            ["invoices.id"],
            name=op.f("fk_invoice_line_items_invoice_id_invoices"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invoice_line_items")),
        sa.UniqueConstraint(
            "invoice_id",
            "line_number",
            name=op.f("uq_invoice_line_items_invoice_id_line_number"),
        ),
    )
    _copy_extractions()
    op.drop_index(
        op.f("ix_invoice_extractions_invoice_number"), table_name="invoice_extractions"
    )
    op.drop_index(
        op.f("ix_invoice_extractions_needs_review"), table_name="invoice_extractions"
    )
    op.drop_index(
        op.f("ix_invoice_extractions_vendor_key_invoice_number_key"),
        table_name="invoice_extractions",
    )
    op.drop_table("invoice_extractions")
    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema.

    Recreates an empty invoice_extractions table: extracted invoices are not
    copied back (re-run extraction after downgrading to restore them).
    """
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "invoice_extractions",
        sa.Column("id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("document_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("is_invoice", sa.BOOLEAN(), autoincrement=False, nullable=False),
        sa.Column(
            "invoice_number", sa.VARCHAR(length=100), autoincrement=False, nullable=True
        ),
        sa.Column(
            "vendor_name", sa.VARCHAR(length=255), autoincrement=False, nullable=True
        ),
        sa.Column("invoice_date", sa.DATE(), autoincrement=False, nullable=True),
        sa.Column(
            "currency", sa.VARCHAR(length=10), autoincrement=False, nullable=True
        ),
        sa.Column(
            "total_amount",
            sa.NUMERIC(precision=18, scale=4),
            autoincrement=False,
            nullable=True,
        ),
        sa.Column(
            "data",
            postgresql.JSONB(astext_type=sa.Text()),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("model", sa.VARCHAR(length=100), autoincrement=False, nullable=False),
        sa.Column("input_tokens", sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column("output_tokens", sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column(
            "cache_read_tokens", sa.INTEGER(), autoincrement=False, nullable=False
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "invoice_number_key",
            sa.VARCHAR(length=100),
            autoincrement=False,
            nullable=True,
        ),
        sa.Column(
            "vendor_key", sa.VARCHAR(length=255), autoincrement=False, nullable=True
        ),
        sa.Column(
            "checks",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "needs_review",
            sa.BOOLEAN(),
            server_default=sa.text("false"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "review_reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            autoincrement=False,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_invoice_extractions_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invoice_extractions")),
        sa.UniqueConstraint(
            "document_id",
            name=op.f("uq_invoice_extractions_document_id"),
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
    )
    op.create_index(
        op.f("ix_invoice_extractions_vendor_key_invoice_number_key"),
        "invoice_extractions",
        ["vendor_key", "invoice_number_key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_invoice_extractions_needs_review"),
        "invoice_extractions",
        ["needs_review"],
        unique=False,
    )
    op.create_index(
        op.f("ix_invoice_extractions_invoice_number"),
        "invoice_extractions",
        ["invoice_number"],
        unique=False,
    )
    op.drop_table("invoice_line_items")
    op.drop_index(
        op.f("ix_validation_errors_document_id_resolved"),
        table_name="validation_errors",
    )
    op.drop_table("validation_errors")
    op.drop_index(
        op.f("ix_invoices_vendor_key_invoice_number_key"), table_name="invoices"
    )
    op.drop_index(op.f("ix_invoices_invoice_number"), table_name="invoices")
    op.drop_table("invoices")
    # ### end Alembic commands ###
