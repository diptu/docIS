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


def normalize_invoice(data: InvoiceData) -> InvoiceData:
    """Enforce what the JSON schema cannot: blank strings are null, the
    currency code is upper-case, and uncertain fields are listed once."""
    currency = _clean(data.currency)
    return data.model_copy(
        update={
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
    if vendor.name:
        name = _LEGAL_SUFFIXES.sub(" ", vendor.name.lower())
        name = _NON_ALNUM.sub("", name)
        if name:
            return f"name:{name}"[:255]
    return None
