"""
Scoring an extracted invoice against its ground truth.

Every field ends in one of five outcomes:

* ``correct``: expected a value and got it (after normalisation);
* ``correct_null``: expected nothing and got nothing;
* ``wrong``: expected a value, got a different one;
* ``missed``: expected a value, got null;
* ``hallucinated``: expected null, got a value (an invented field).

Normalisation is deliberately forgiving about presentation and strict about
content: text compares letters and digits only, case-insensitively
("Hauptstr. 1,\\n10115 Berlin" == "hauptstr 1 10115 berlin"); dates must be
equal; amounts may differ by at most 0.01.
"""

import datetime as dt
import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.compare.service import match_line_items
from app.extraction.schema import InvoiceData, InvoiceLineItem

TOLERANCE = Decimal("0.01")

HEADER_FIELDS = (
    "is_invoice",
    "invoice_number",
    "invoice_date",
    "due_date",
    "currency",
    "purchase_order_number",
    "payment_terms",
)
PARTY_FIELDS = ("name", "address", "tax_id", "email", "phone")
TOTAL_FIELDS = ("subtotal", "tax_amount", "total_amount", "amount_due")
LINE_FIELDS = (
    "description",
    "product_code",
    "quantity",
    "unit",
    "unit_price",
    "tax_rate",
    "amount",
)

CORRECT, CORRECT_NULL, WRONG, MISSED, HALLUCINATED = (
    "correct",
    "correct_null",
    "wrong",
    "missed",
    "hallucinated",
)
OUTCOMES = (CORRECT, CORRECT_NULL, WRONG, MISSED, HALLUCINATED)


def _text(value: str) -> str:
    return re.sub(r"[^0-9a-z]", "", value.casefold())


def values_match(expected: Any, actual: Any) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return bool(expected == actual)
    if isinstance(expected, int | float) and isinstance(actual, int | float):
        return abs(Decimal(str(expected)) - Decimal(str(actual))) <= TOLERANCE
    if isinstance(expected, dt.date) or isinstance(actual, dt.date):
        return bool(expected == actual)
    return _text(str(expected)) == _text(str(actual))


def outcome(expected: Any, actual: Any) -> str:
    if expected is None:
        return CORRECT_NULL if actual is None else HALLUCINATED
    if actual is None:
        return MISSED
    return CORRECT if values_match(expected, actual) else WRONG


@dataclass(frozen=True)
class FieldScore:
    field: str
    expected: Any
    actual: Any
    outcome: str

    @property
    def ok(self) -> bool:
        return self.outcome in (CORRECT, CORRECT_NULL)


@dataclass
class CaseScore:
    fields: list[FieldScore]
    lines_expected: int
    lines_extracted: int
    lines_matched: int
    line_fields: list[FieldScore] = field(default_factory=list)

    @property
    def fields_ok(self) -> bool:
        return all(f.ok for f in self.fields)

    @property
    def exact(self) -> bool:
        """Every field, every line and every line field right."""
        return (
            self.fields_ok
            and self.lines_matched == self.lines_expected == self.lines_extracted
            and all(f.ok for f in self.line_fields)
        )

    def outcomes(self) -> Counter[str]:
        return Counter(f.outcome for f in self.fields)


def _json(value: Any) -> Any:
    return value.isoformat() if isinstance(value, dt.date) else value


def _score(name: str, expected: Any, actual: Any) -> FieldScore:
    return FieldScore(name, _json(expected), _json(actual), outcome(expected, actual))


def score_invoice(expected: InvoiceData, actual: InvoiceData) -> CaseScore:
    """Score header fields, parties, totals and line items."""
    fields = [
        _score(n, getattr(expected, n), getattr(actual, n)) for n in HEADER_FIELDS
    ]
    for party in ("vendor", "customer"):
        for name in PARTY_FIELDS:
            fields.append(
                _score(
                    f"{party}.{name}",
                    getattr(getattr(expected, party), name),
                    getattr(getattr(actual, party), name),
                )
            )
    fields += [
        _score(n, getattr(expected, n), getattr(actual, n)) for n in TOTAL_FIELDS
    ]

    pairs = match_line_items(expected.line_items, actual.line_items)
    line_fields: list[FieldScore] = []
    for pair in pairs:
        if pair.left_index is None or pair.right_index is None:
            continue
        want: InvoiceLineItem = expected.line_items[pair.left_index - 1]
        got: InvoiceLineItem = actual.line_items[pair.right_index - 1]
        line_fields += [
            _score(f"line.{n}", getattr(want, n), getattr(got, n)) for n in LINE_FIELDS
        ]
    return CaseScore(
        fields=fields,
        lines_expected=len(expected.line_items),
        lines_extracted=len(actual.line_items),
        lines_matched=sum(
            p.left_index is not None and p.right_index is not None for p in pairs
        ),
        line_fields=line_fields,
    )
