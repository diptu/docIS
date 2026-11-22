"""
Post-processing of model output, and keys for duplicate matching.
"""

import re

from app.extraction.schema import InvoiceData, InvoiceLineItem, Party

_NON_ALNUM = re.compile(r"[^0-9a-z]+")
# Legal-form suffixes ignored when matching vendor names.
_LEGAL_SUFFIXES = re.compile(
    r"\b(gmbh|ag|kg|ug|ohg|e\.?k|ltd|limited|llc|inc|corp|corporation|co|"
    r"plc|sa|sas|sarl|srl|spa|bv|nv|oy|ab|as|aps|kft|sro|sp z o o)\b\.?"
)


def _clean(value: str | None) -> str | None:
    """Strip whitespace; blank strings become None (a missing value)."""
    if value is None:
        return None
    value = value.strip()
    return value or None


def _party(party: Party) -> Party:
    return Party(
        name=_clean(party.name),
        address=_clean(party.address),
        tax_id=_clean(party.tax_id),
        email=_clean(party.email),
        phone=_clean(party.phone),
    )


def _line(item: InvoiceLineItem) -> InvoiceLineItem:
    return item.model_copy(
        update={
            "description": _clean(item.description),
            "product_code": _clean(item.product_code),
            "unit": _clean(item.unit),
        }
    )


def _negate(value: float | None) -> float | None:
    return -value if value is not None else None


def _fit_unit_price(
    quantity: float | None, unit_price: float | None, amount: float | None
) -> tuple[float | None, float | None]:
    """Make quantity x unit price agree in sign with the line amount,
    preferring positive quantities and prices."""
    if unit_price is None or not amount or (quantity is not None and not quantity):
        return quantity, unit_price
    if quantity is not None and quantity < 0 and unit_price < 0:
        quantity, unit_price = -quantity, -unit_price
    product = (quantity if quantity is not None else 1.0) * unit_price
    if (product > 0) == (amount > 0):
        return quantity, unit_price
    if unit_price < 0 or quantity is None or quantity > 0:
        return quantity, -unit_price
    return -quantity, unit_price


def _reported_negative(data: InvoiceData) -> bool:
    """Whether the document's own amounts came back negative (the total, or
    failing that the subtotal, or failing that the sum of the lines)."""
    for value in (data.total_amount, data.subtotal):
        if value:
            return value < 0
    return sum(item.amount or 0.0 for item in data.line_items) < 0


def apply_sign_convention(data: InvoiceData) -> InvoiceData:
    """Amounts are positive magnitudes in the document's own direction
    (see app.extraction.schema). A credit note reported with negative
    amounts, as many print them, has every amount negated; a line that ran
    against it (a fee deducted from the credit) becomes negative."""
    if data.document_type != "credit_note" or not _reported_negative(data):
        return data
    lines = []
    for item in data.line_items:
        amount = _negate(item.amount)
        quantity, unit_price = _fit_unit_price(
            item.quantity, _negate(item.unit_price), amount
        )
        lines.append(
            item.model_copy(
                update={
                    "amount": amount,
                    "quantity": quantity,
                    "unit_price": unit_price,
                }
            )
        )
    return data.model_copy(
        update={
            "subtotal": _negate(data.subtotal),
            "tax_amount": _negate(data.tax_amount),
            "total_amount": _negate(data.total_amount),
            "amount_due": _negate(data.amount_due),
            "line_items": lines,
        }
    )


def normalize_invoice(data: InvoiceData) -> InvoiceData:
    """Enforce what the JSON schema cannot: blank strings are null, the
    currency code is upper-case, uncertain fields are listed once,
    ``is_invoice`` agrees with ``document_type``, and amounts follow the
    sign convention."""
    currency = _clean(data.currency)
    data = apply_sign_convention(data)
    return data.model_copy(
        update={
            "is_invoice": data.document_type != "other",
            "invoice_number": _clean(data.invoice_number),
            "currency": currency.upper() if currency else None,
            "purchase_order_number": _clean(data.purchase_order_number),
            "payment_terms": _clean(data.payment_terms),
            "vendor": _party(data.vendor),
            "customer": _party(data.customer),
            "line_items": [_line(item) for item in data.line_items],
            "uncertain_fields": list(dict.fromkeys(data.uncertain_fields)),
        }
    )


def invoice_number_key(number: str | None) -> str | None:
    """Invoice number for matching: case and separators ignored.

    "INV-2026/001", "inv 2026 001" and "INV2026001" all match.
    """
    if number is None:
        return None
    key = _NON_ALNUM.sub("", number.lower())
    return key[:100] or None


def vendor_key(vendor: Party) -> str | None:
    """Vendor identity for matching: tax ID if present, else the name
    without case, punctuation or legal-form suffix ("ACME GmbH" == "Acme")."""
    if vendor.tax_id:
        tax_id = _NON_ALNUM.sub("", vendor.tax_id.lower())
        if tax_id:
            return f"tax:{tax_id}"[:255]
    if name := name_key(vendor.name):
        return f"name:{name}"[:255]
    return None


def name_key(name: str | None) -> str | None:
    """A company name for matching: case, punctuation and legal-form suffix
    ignored ("ACME GmbH", "Acme" and "acme." all match)."""
    if name is None:
        return None
    key = _NON_ALNUM.sub("", _LEGAL_SUFFIXES.sub(" ", name.lower()))
    return key[:250] or None
