"""
Comparing two or more documents field by field, whatever their file type.

Every processed document, whether PDF, image, workbook or CSV, goes through
the same invoice extraction. Its stored invoice is therefore the common form
compared here, read from the database rows so amounts stay exact Decimals.

For each field, every document's cell is:

* ``present``: the document has a value;
* ``missing``: the document has an invoice, but not this value;
* ``unavailable``: the document has no extracted invoice to compare.

A field row is then:

* ``mismatch``: two present values differ after normalisation;
* ``missing``: the present values agree, but some invoice lacks the field;
* ``unavailable``: only one document has it, the rest have no invoice;
* ``match``: at least two documents have it and they all agree.

Rows where no document has a value are left out.

The comparison is deterministic. Values are normalised by kind:

* text ignores case and spacing;
* identifiers ignore case and separators;
* company names also ignore the legal form;
* dates compare as dates.

Money agrees when the largest and smallest values are at most
``AMOUNT_TOLERANCE`` apart. Quantities and tax rates must be exactly equal.
Each difference is the value minus the row's baseline: the first document,
in the order given, that has a value. That is the reference document
whenever it has one.

Line items are aligned to the reference document's lines (the first
document with lines) by product code, then by description
(``match_line_items``). A line that no earlier document has starts a group
of its own.
"""

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from app.compare.service import AMOUNT_TOLERANCE, _norm_text, match_line_items
from app.extraction.normalize import invoice_number_key, name_key
from app.models.invoice import Invoice
from app.models.invoice import InvoiceLineItem as LineRow

MIN_DOCUMENTS = 2
MAX_DOCUMENTS = 10


class Kind(StrEnum):
    """How a field's values are normalised and compared."""

    TEXT = "text"
    IDENTIFIER = "identifier"
    NAME = "name"
    CODE = "code"
    DATE = "date"
    MONEY = "money"
    QUANTITY = "quantity"
    RATE = "rate"


NUMERIC = frozenset({Kind.MONEY, Kind.QUANTITY, Kind.RATE})


class Presence(StrEnum):
    PRESENT = "present"
    MISSING = "missing"
    UNAVAILABLE = "unavailable"


class RowStatus(StrEnum):
    MATCH = "match"
    MISMATCH = "mismatch"
    MISSING = "missing"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class FieldSpec:
    name: str  # as in InvoiceData: "vendor.name", "tax_amount"
    label: str
    group: str  # header | vendor | customer | totals | line
    kind: Kind
    column: str  # attribute of the stored row

    def get(self, row: Any) -> Any:
        return getattr(row, self.column)


def _spec(name: str, label: str, group: str, kind: Kind, column: str = "") -> FieldSpec:
    return FieldSpec(name, label, group, kind, column or name.replace(".", "_"))


INVOICE_FIELDS = (
    _spec("invoice_number", "Invoice number", "header", Kind.IDENTIFIER),
    _spec("invoice_date", "Invoice date", "header", Kind.DATE),
    _spec("due_date", "Due date", "header", Kind.DATE),
    _spec("currency", "Currency", "header", Kind.CODE),
    _spec("purchase_order_number", "PO number", "header", Kind.IDENTIFIER),
    _spec("payment_terms", "Payment terms", "header", Kind.TEXT),
    _spec("vendor.name", "Vendor name", "vendor", Kind.NAME),
    _spec("vendor.tax_id", "Vendor tax ID", "vendor", Kind.IDENTIFIER),
    _spec("vendor.address", "Vendor address", "vendor", Kind.TEXT),
    _spec("vendor.email", "Vendor email", "vendor", Kind.TEXT),
    _spec("customer.name", "Customer name", "customer", Kind.NAME),
    _spec("customer.tax_id", "Customer tax ID", "customer", Kind.IDENTIFIER),
    _spec("customer.address", "Customer address", "customer", Kind.TEXT),
    _spec("subtotal", "Subtotal", "totals", Kind.MONEY),
    _spec("tax_amount", "Tax", "totals", Kind.MONEY, column="tax"),
    _spec("total_amount", "Total", "totals", Kind.MONEY),
    _spec("amount_due", "Amount due", "totals", Kind.MONEY),
)

LINE_FIELDS = (
    _spec("description", "Description", "line", Kind.TEXT),
    _spec("product_code", "Product code", "line", Kind.IDENTIFIER),
    _spec("quantity", "Quantity", "line", Kind.QUANTITY),
    _spec("unit", "Unit", "line", Kind.TEXT),
    _spec("unit_price", "Unit price", "line", Kind.MONEY),
    _spec("tax_rate", "Tax rate (%)", "line", Kind.RATE),
    _spec("amount", "Amount", "line", Kind.MONEY),
)


# ------------------------------------------------------------ normalising


def _decimal(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def plain(value: Decimal, kind: Kind) -> str:
    """A Decimal as fixed-point text: money with at least two decimals
    ("1487.50"), quantities and rates without trailing zeros ("2.5", "19")."""
    normal = value.normalize() if value else Decimal(0)  # no "-0"
    if kind == Kind.MONEY and int(normal.as_tuple().exponent) > -2:
        normal = normal.quantize(Decimal("0.01"))
    return f"{normal:f}"


def normalize(value: Any, kind: Kind) -> str | Decimal | None:
    """The comparable form of a value, or None when there is nothing to
    compare (blank text)."""
    if value is None:
        return None
    if kind in NUMERIC:
        return _decimal(value)
    if kind == Kind.DATE:
        return value.isoformat() if isinstance(value, dt.date) else str(value)
    text = str(value)
    if kind == Kind.IDENTIFIER:
        return invoice_number_key(text)
    if kind == Kind.NAME:
        return name_key(text) or _norm_text(text) or None
    if kind == Kind.CODE:
        return text.strip().upper() or None
    return _norm_text(text) or None


def _display(value: Any, kind: Kind) -> Any:
    """The original value, JSON-ready (exact decimals as text)."""
    if value is None:
        return None
    if kind in NUMERIC:
        return plain(_decimal(value), kind)
    if isinstance(value, dt.date):
        return value.isoformat()
    return value


# ---------------------------------------------------------------- results


@dataclass(frozen=True)
class Cell:
    document_id: uuid.UUID
    presence: Presence
    value: Any = None  # as extracted, JSON-ready
    normalized: str | None = None
    matches_baseline: bool | None = None  # None: nothing to compare
    difference: str | None = None  # numeric fields: value - baseline


@dataclass(frozen=True)
class FieldRow:
    field: str
    label: str
    group: str
    kind: Kind
    status: RowStatus
    baseline_document_id: uuid.UUID | None
    cells: list[Cell]


@dataclass(frozen=True)
class LineRef:
    document_id: uuid.UUID
    line_number: int | None  # 1-based; None when the document lacks the line
    matched_by: str | None  # reference | product_code | description


@dataclass(frozen=True)
class LineGroup:
    position: int  # 1-based, in display order
    status: RowStatus
    lines: list[LineRef]
    fields: list[FieldRow]


@dataclass(frozen=True)
class CompareSource:
    """One document to compare: its invoice, or why it has none."""

    document_id: uuid.UUID
    invoice: Invoice | None
    unavailable_reason: str | None = None

    @property
    def available(self) -> bool:
        return self.invoice is not None and self.invoice.is_invoice


@dataclass
class MultiComparison:
    reference_document_id: uuid.UUID
    fields: list[FieldRow]
    line_items: list[LineGroup]
    notes: list[str] = field(default_factory=list)

    def counts(self, rows: Sequence[FieldRow] | Sequence[LineGroup]) -> dict[str, int]:
        return {s.value: sum(1 for r in rows if r.status == s) for s in RowStatus}


def _equal(a: str | Decimal, b: str | Decimal, kind: Kind) -> bool:
    if kind == Kind.MONEY:
        return abs(_decimal(a) - _decimal(b)) <= AMOUNT_TOLERANCE
    return a == b


def compare_field(
    spec: FieldSpec,
    rows: Sequence[tuple[uuid.UUID, Any | None, bool]],
) -> FieldRow | None:
    """One field across documents. ``rows`` holds (document id, the row the
    value is read from or None, whether the document has an invoice). Returns
    None when no document has a value."""
    values: list[tuple[uuid.UUID, Any, str | Decimal | None, bool]] = []
    for document_id, row, available in rows:
        raw = spec.get(row) if row is not None else None
        values.append((document_id, raw, normalize(raw, spec.kind), available))

    present = [v for v in values if v[2] is not None]
    if not present:
        return None
    baseline_id, _, baseline, _ = present[0]
    assert baseline is not None

    if spec.kind == Kind.MONEY:
        amounts = [_decimal(v[2]) for v in present]
        mismatch = max(amounts) - min(amounts) > AMOUNT_TOLERANCE
    else:
        mismatch = len({v[2] for v in present}) > 1

    cells: list[Cell] = []
    for document_id, raw, comparable, available in values:
        if comparable is None:
            presence = Presence.MISSING if available else Presence.UNAVAILABLE
            cells.append(Cell(document_id, presence))
            continue
        difference = None
        if spec.kind in NUMERIC:
            delta = _decimal(comparable) - _decimal(baseline)
            difference = ("+" if delta > 0 else "") + plain(delta, spec.kind)
        cells.append(
            Cell(
                document_id,
                Presence.PRESENT,
                value=_display(raw, spec.kind),
                normalized=(
                    plain(comparable, spec.kind)
                    if isinstance(comparable, Decimal)
                    else comparable
                ),
                matches_baseline=_equal(comparable, baseline, spec.kind),
                difference=difference,
            )
        )

    if mismatch:
        status = RowStatus.MISMATCH
    elif any(c.presence == Presence.MISSING for c in cells):
        status = RowStatus.MISSING
    elif len(present) < 2:
        status = RowStatus.UNAVAILABLE
    else:
        status = RowStatus.MATCH
    return FieldRow(
        spec.name, spec.label, spec.group, spec.kind, status, baseline_id, cells
    )


def _align_lines(
    sources: Sequence[CompareSource],
) -> list[dict[int, tuple[LineRow, str]]]:
    """Groups of corresponding lines: {source index: (line, matched by)}."""
    groups: list[dict[int, tuple[LineRow, str]]] = []
    representatives: list[LineRow] = []  # the line each group is matched on
    for index, source in enumerate(sources):
        if not source.available:
            continue
        assert source.invoice is not None
        lines = list(source.invoice.line_items)
        if not lines:
            continue
        if not groups:
            for line in lines:
                groups.append({index: (line, "reference")})
                representatives.append(line)
            continue
        for pair in match_line_items(representatives, lines):
            if pair.right_index is None:
                continue
            line = lines[pair.right_index - 1]
            if pair.left_index is None:
                groups.append({index: (line, "unmatched")})
                representatives.append(line)
            else:
                groups[pair.left_index - 1][index] = (line, str(pair.matched_by))
    return groups


def compare_line_items(sources: Sequence[CompareSource]) -> list[LineGroup]:
    result: list[LineGroup] = []
    for position, group in enumerate(_align_lines(sources), start=1):
        refs: list[LineRef] = []
        rows: list[tuple[uuid.UUID, Any | None, bool]] = []
        for index, source in enumerate(sources):
            line, how = group.get(index, (None, None))
            refs.append(
                LineRef(
                    source.document_id,
                    line.line_number if line is not None else None,
                    None if how == "unmatched" else how,
                )
            )
            rows.append((source.document_id, line, source.available))
        fields = [
            row
            for spec in LINE_FIELDS
            if (row := compare_field(spec, rows)) is not None
        ]
        lacking = sum(
            1
            for s, ref in zip(sources, refs, strict=True)
            if s.available and ref.line_number is None
        )
        if any(f.status == RowStatus.MISMATCH for f in fields):
            status = RowStatus.MISMATCH
        elif lacking:
            status = RowStatus.MISSING
        elif len(group) < 2:
            status = RowStatus.UNAVAILABLE
        else:
            status = RowStatus.MATCH
        result.append(LineGroup(position, status, refs, fields))
    return result


def compare_documents(sources: Sequence[CompareSource]) -> MultiComparison:
    """Compare the invoices of ``sources``; the first is the reference."""
    if len(sources) < MIN_DOCUMENTS:
        raise ValueError(f"Compare at least {MIN_DOCUMENTS} documents.")
    rows = [
        (s.document_id, s.invoice if s.available else None, s.available)
        for s in sources
    ]
    fields = [
        row for spec in INVOICE_FIELDS if (row := compare_field(spec, rows)) is not None
    ]
    notes = [
        s.unavailable_reason
        for s in sources
        if not s.available and s.unavailable_reason
    ]
    available = [s for s in sources if s.available]
    if len(available) < 2:
        notes.append(
            "Fewer than two documents have an extracted invoice, so values "
            "can't be compared with each other."
        )
    currencies = sorted(
        {
            c
            for s in available
            if s.invoice and (c := (s.invoice.currency or "").upper())
        }
    )
    if len(currencies) > 1:
        notes.append(
            f"The invoices use different currencies ({', '.join(currencies)}); "
            "amount differences are not converted."
        )
    return MultiComparison(
        reference_document_id=sources[0].document_id,
        fields=fields,
        line_items=compare_line_items(sources),
        notes=notes,
    )
