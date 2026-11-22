"""add invoice document type

Credit notes are now extracted like invoices, with document_type
"credit_note" and positive amounts. Existing rows become "invoice", or
"other" where the model said the document was not an invoice.

Revision ID: 3c1f0e2a9b7d
Revises: 8edd056666e0
Create Date: 2026-11-22 23:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3c1f0e2a9b7d"
down_revision: str | Sequence[str] | None = "8edd056666e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "invoices",
        sa.Column(
            "document_type",
            sa.String(length=20),
            server_default="invoice",
            nullable=False,
        ),
    )
    op.execute("UPDATE invoices SET document_type = 'other' WHERE NOT is_invoice")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("invoices", "document_type")
