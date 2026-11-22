"""
Invoice data schema.

This is both the structured-output schema Claude must follow and the API
response shape. Every field is present in every result; anything not found
in the document is ``null`` (or an empty ``line_items`` list), never guessed.

Amount sign convention (the same for invoices and credit notes, as in UBL):
amounts are positive magnitudes in the document's own direction, and
``document_type`` says which direction that is. A credit note for 100.00
has ``total_amount`` 100.00, however the document prints it (-100.00,
(100.00), 100.00 CR). Only lines that run against the document are
negative: an itemised discount on an invoice, a restocking fee on a credit
note. Adjustments below the subtotal (credit applied, payments) are not line
items. To post to a ledger, negate a credit note's amounts.
"""

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DocumentType = Literal["invoice", "credit_note", "other"]

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
        description=(
            "Line total as printed (do not recompute). Positive for lines in "
            "the document's direction; negative only for an itemised line "
            "that reduces the subtotal (a discount line, a restocking fee "
            "on a credit note)."
        )
    )


class InvoiceData(_Strict):
    """Structured content of one invoice."""

    document_type: DocumentType = Field(
        description=(
            "'invoice' for an invoice, bill or debit note (charges the "
            "customer); 'credit_note' for a credit note or credit memo "
            "(refunds or reduces what the customer owes, including 'nota de "
            "crédito'); 'other' for anything else (statement, quote, receipt-"
            "less letter, price list), and then leave every other field "
            "null/empty."
        )
    )
    is_invoice: bool = Field(
        description=(
            "True for a billing document: an invoice or a credit note "
            "(document_type 'invoice' or 'credit_note'). False only for "
            "document_type 'other'."
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
    subtotal: float | None = Field(
        description="Total before tax, as a positive magnitude (see sign rules)."
    )
    tax_amount: float | None = Field(
        description="Total tax/VAT, as a positive magnitude (see sign rules)."
    )
    total_amount: float | None = Field(
        description=(
            "Grand total including tax, as a positive magnitude: a credit "
            "note's total is positive even if printed as -X, (X) or X CR."
        )
    )
    amount_due: float | None = Field(
        description=(
            "Balance still due (or still to be refunded, on a credit note), "
            "if printed separately from the total."
        )
    )
    uncertain_fields: list[UncertainField] = Field(
        description=(
            "Fields whose value you are not confident about: illegible or "
            "garbled text, several conflicting candidates, or an ambiguous "
            "date. Empty if every extracted value is clearly stated."
        )
    )
