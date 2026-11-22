"""Multi-document comparison rules (app.compare.multi)."""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import pytest

from app.compare.multi import (
    CompareSource,
    Kind,
    MultiComparison,
    Presence,
    RowStatus,
    compare_documents,
    normalize,
    plain,
)
from app.models.invoice import Invoice
from app.models.invoice import InvoiceLineItem as LineRow


def line(number: int, **values: Any) -> LineRow:
    defaults: dict[str, Any] = {
        "description": None,
        "product_code": None,
        "quantity": None,
        "unit": None,
        "unit_price": None,
        "tax_rate": None,
        "amount": None,
    }
    return LineRow(line_number=number, **(defaults | values))


def invoice(lines: list[LineRow] | None = None, **values: Any) -> Invoice:
    defaults: dict[str, Any] = {
        "is_invoice": True,
        "invoice_number": "INV-2026-001",
        "invoice_date": dt.date(2026, 11, 3),
        "currency": "EUR",
        "vendor_name": "ACME GmbH",
        "subtotal": Decimal("1250.0000"),
        "tax": Decimal("237.5000"),
        "total_amount": Decimal("1487.5000"),
    }
    return Invoice(line_items=lines or [], **(defaults | values))


def source(inv: Invoice | None, reason: str | None = None) -> CompareSource:
    return CompareSource(uuid.uuid4(), inv, reason)


def row(result: MultiComparison, name: str) -> Any:
    return next(f for f in result.fields if f.field == name)


# ------------------------------------------------------------ normalising


@pytest.mark.parametrize(
    ("kind", "a", "b"),
    [
        (Kind.IDENTIFIER, "INV-2026/001", "inv 2026 001"),
        (Kind.NAME, "ACME GmbH", "Acme"),
        (Kind.TEXT, "Net  30 days", "net 30 DAYS"),
        (Kind.CODE, "eur", "EUR"),
        (Kind.MONEY, Decimal("1487.5000"), 1487.5),
        (Kind.DATE, dt.date(2026, 11, 3), "2026-11-03"),
    ],
)
def test_equivalent_values_normalise_alike(kind: Kind, a: Any, b: Any) -> None:
    assert normalize(a, kind) == normalize(b, kind)


@pytest.mark.parametrize(
    ("value", "kind", "text"),
    [
        (Decimal("1487.5000"), Kind.MONEY, "1487.50"),
        (Decimal("12"), Kind.MONEY, "12.00"),
        (Decimal("0.1250"), Kind.MONEY, "0.125"),
        (Decimal("2.5000"), Kind.QUANTITY, "2.5"),
        (Decimal("19.0000"), Kind.RATE, "19"),
        (Decimal("-0.00"), Kind.MONEY, "0.00"),
    ],
)
def test_decimals_are_shown_exactly_without_float_noise(
    value: Decimal, kind: Kind, text: str
) -> None:
    assert plain(value, kind) == text


# ------------------------------------------------------------ field rows


def test_three_documents_that_agree_match_on_every_field() -> None:
    sources = [source(invoice()) for _ in range(3)]

    result = compare_documents(sources)

    assert {f.status for f in result.fields} == {RowStatus.MATCH}
    # Fields no document has (due date, PO number...) are left out.
    assert "due_date" not in {f.field for f in result.fields}
    assert result.reference_document_id == sources[0].document_id


def test_amount_mismatch_reports_exact_decimal_differences_from_the_reference() -> None:
    sources = [
        source(invoice()),
        source(invoice(total_amount=Decimal("1500.0000"))),
        source(invoice(total_amount=Decimal("1487.4950"))),
    ]

    total = row(compare_documents(sources), "total_amount")

    assert total.status == RowStatus.MISMATCH
    assert total.baseline_document_id == sources[0].document_id
    assert [c.value for c in total.cells] == ["1487.50", "1500.00", "1487.495"]
    assert [c.difference for c in total.cells] == ["0.00", "+12.50", "-0.005"]
    assert [c.matches_baseline for c in total.cells] == [True, False, True]


def test_amounts_within_a_cent_match() -> None:
    sources = [
        source(invoice()),
        source(invoice(total_amount=Decimal("1487.5100"))),
    ]

    assert row(compare_documents(sources), "total_amount").status == RowStatus.MATCH


def test_equivalent_identifiers_and_vendor_names_match() -> None:
    sources = [
        source(invoice()),
        source(invoice(invoice_number="inv 2026 001", vendor_name="Acme")),
    ]

    result = compare_documents(sources)

    assert row(result, "invoice_number").status == RowStatus.MATCH
    vendor = row(result, "vendor.name")
    assert vendor.status == RowStatus.MATCH
    assert [c.value for c in vendor.cells] == ["ACME GmbH", "Acme"]  # originals kept


def test_a_field_one_invoice_lacks_is_missing_there() -> None:
    sources = [source(invoice(due_date=dt.date(2026, 12, 3))), source(invoice())]

    due = row(compare_documents(sources), "due_date")

    assert due.status == RowStatus.MISSING
    assert [c.presence for c in due.cells] == [Presence.PRESENT, Presence.MISSING]


def test_a_document_without_an_invoice_is_unavailable_not_missing() -> None:
    sources = [
        source(invoice()),
        source(invoice()),
        source(None, "scan.png: no invoice has been extracted from it."),
    ]

    result = compare_documents(sources)

    total = row(result, "total_amount")
    assert total.status == RowStatus.MATCH  # the two invoices agree
    assert total.cells[2].presence == Presence.UNAVAILABLE
    assert result.notes == ["scan.png: no invoice has been extracted from it."]


def test_with_a_single_invoice_nothing_can_be_compared() -> None:
    sources = [
        source(invoice()),
        source(invoice(is_invoice=False), "letter.pdf: not recognised as an invoice."),
    ]

    result = compare_documents(sources)

    assert {f.status for f in result.fields} == {RowStatus.UNAVAILABLE}
    assert "Fewer than two documents" in result.notes[-1]


def test_baseline_falls_back_when_the_reference_lacks_the_value() -> None:
    sources = [
        source(invoice(amount_due=None)),
        source(invoice(amount_due=Decimal("100"))),
        source(invoice(amount_due=Decimal("90"))),
    ]

    due = row(compare_documents(sources), "amount_due")

    assert due.status == RowStatus.MISMATCH  # mismatch outranks missing
    assert due.baseline_document_id == sources[1].document_id
    assert [c.difference for c in due.cells] == [None, "0.00", "-10.00"]


def test_different_currencies_are_noted() -> None:
    sources = [source(invoice()), source(invoice(currency="USD"))]

    result = compare_documents(sources)

    assert row(result, "currency").status == RowStatus.MISMATCH
    assert any("different currencies (EUR, USD)" in n for n in result.notes)


def test_fewer_than_two_documents_is_an_error() -> None:
    with pytest.raises(ValueError, match="at least 2"):
        compare_documents([source(invoice())])


# ------------------------------------------------------------- line items


def test_line_items_align_by_product_code_then_description() -> None:
    reference = [
        line(1, product_code="A-1", description="Laptop", quantity=Decimal("2"),
             unit_price=Decimal("500"), amount=Decimal("1000")),
        line(2, description="Docking station", quantity=Decimal("1"),
             unit_price=Decimal("250"), amount=Decimal("250")),
    ]  # fmt: skip
    reordered = [
        line(1, description="Docking station", quantity=Decimal("1"),
             unit_price=Decimal("250"), amount=Decimal("250")),
        line(2, product_code="a1", description="Notebook computer",
             quantity=Decimal("3"), unit_price=Decimal("500"), amount=Decimal("1500")),
    ]  # fmt: skip
    sources = [source(invoice(reference)), source(invoice(reordered))]

    laptop, dock = compare_documents(sources).line_items

    assert [(r.line_number, r.matched_by) for r in laptop.lines] == [
        (1, "reference"),
        (2, "product_code"),
    ]
    assert laptop.status == RowStatus.MISMATCH
    by_field = {f.field: f for f in laptop.fields}
    assert by_field["quantity"].status == RowStatus.MISMATCH
    assert [c.difference for c in by_field["quantity"].cells] == ["0", "+1"]
    assert by_field["unit_price"].status == RowStatus.MATCH
    assert [c.difference for c in by_field["amount"].cells] == ["0.00", "+500.00"]

    assert [(r.line_number, r.matched_by) for r in dock.lines] == [
        (2, "reference"),
        (1, "description"),
    ]
    assert dock.status == RowStatus.MATCH


def test_lines_only_some_documents_have_are_missing_elsewhere() -> None:
    a = [line(1, description="Laptop", amount=Decimal("1000"))]
    b = [
        line(1, description="Laptop", amount=Decimal("1000")),
        line(2, description="Express shipping", amount=Decimal("25")),
    ]
    c = [line(1, description="Laptop", amount=Decimal("1000"))]
    sources = [source(invoice(a)), source(invoice(b)), source(invoice(c))]

    laptop, shipping = compare_documents(sources).line_items

    assert laptop.status == RowStatus.MATCH
    assert shipping.status == RowStatus.MISSING
    assert [r.line_number for r in shipping.lines] == [None, 2, None]
    assert [r.matched_by for r in shipping.lines] == [None, None, None]
    amount = next(f for f in shipping.fields if f.field == "amount")
    assert [c.presence for c in amount.cells] == [
        Presence.MISSING,
        Presence.PRESENT,
        Presence.MISSING,
    ]


def test_a_later_document_can_match_a_line_the_reference_lacks() -> None:
    a = [line(1, description="Laptop", amount=Decimal("1000"))]
    b = [line(1, description="Laptop", amount=Decimal("1000")),
         line(2, description="Express shipping", amount=Decimal("25"))]  # fmt: skip
    c = [line(1, description="Express shipping", amount=Decimal("30"))]
    sources = [source(invoice(a)), source(invoice(b)), source(invoice(c))]

    _, shipping = compare_documents(sources).line_items

    assert [r.line_number for r in shipping.lines] == [None, 2, 1]
    assert shipping.status == RowStatus.MISMATCH  # 25 vs 30
