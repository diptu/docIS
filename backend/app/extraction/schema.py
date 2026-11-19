"""
Invoice data schema.

This is both the structured-output schema Claude must follow and the API
response shape. Every field is present in every result; anything not found
in the document is ``null`` (or an empty ``line_items`` list), never guessed.
Amounts are plain numbers in the invoice currency, as printed.
"""

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Fields the model may report as uncertain (illegible, ambiguous, conflicting).
UncertainField = Literal[
    "invoice_number",
    "invoice_date",
    "due_date",
    "currency",
    "purchase_order_number",
    "payment_terms",
    "vendor",
    "customer",
    "line_items",
    "subtotal",
    "tax_amount",
    "total_amount",
    "amount_due",
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Party(_Strict):
    """A business or person named on the invoice."""

    name: str | None = Field(description="Legal or trading name.")
    address: str | None = Field(
        description="Full postal address as printed, lines joined with ', '."
    )
    tax_id: str | None = Field(
        description="VAT number, tax ID or company registration number, as printed."
    )
    email: str | None = Field(description="Email address.")
    phone: str | None = Field(description="Phone number, as printed.")


class InvoiceLineItem(_Strict):
    """One billed line of the invoice."""

    description: str | None = Field(description="What was sold or the service.")
    product_code: str | None = Field(description="SKU, article or item number.")
    quantity: float | None = Field(description="Quantity billed.")
    unit: str | None = Field(description="Unit of measure, e.g. 'h', 'pcs', 'kg'.")
    unit_price: float | None = Field(description="Price per unit.")
    tax_rate: float | None = Field(
        description="Tax/VAT rate for this line as a percentage, e.g. 19 for 19%."
    )
    amount: float | None = Field(
        description="Line total as printed on the invoice (do not recompute)."
    )


class InvoiceData(_Strict):
    """Structured content of one invoice."""

    is_invoice: bool = Field(
        description=(
            "True if the document is an invoice or bill requesting payment. "
            "False for anything else; then leave every other field null/empty."
        )
    )
    invoice_number: str | None = Field(description="Invoice number or ID.")
    invoice_date: dt.date | None = Field(description="Issue date (YYYY-MM-DD).")
    due_date: dt.date | None = Field(description="Payment due date (YYYY-MM-DD).")
    currency: str | None = Field(
        description="ISO 4217 code, e.g. 'EUR', inferred from symbols if needed."
    )
    purchase_order_number: str | None = Field(description="Buyer's PO number.")
    payment_terms: str | None = Field(description="e.g. 'Net 30', as printed.")
    vendor: Party = Field(description="The seller issuing the invoice.")
    customer: Party = Field(description="The buyer being billed (bill-to).")
    line_items: list[InvoiceLineItem] = Field(
        description="Every billed line, in document order; empty if none."
    )
    subtotal: float | None = Field(description="Total before tax, as printed.")
    tax_amount: float | None = Field(description="Total tax/VAT, as printed.")
    total_amount: float | None = Field(
        description="Grand total including tax, as printed."
    )
    amount_due: float | None = Field(
        description="Balance still due, if printed separately from the total."
    )
    uncertain_fields: list[UncertainField] = Field(
        description=(
            "Fields whose value you are not confident about: illegible or "
            "garbled text, several conflicting candidates, or an ambiguous "
            "date. Empty if every extracted value is clearly stated."
        )
    )
