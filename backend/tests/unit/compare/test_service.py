import datetime as dt
from decimal import Decimal

import pytest

from app.compare.service import (
    Status,
    cell_found,
    compare_invoices,
    compare_text,
    compare_values,
    match_line_items,
    number_readings,
    numbers_in,
    reconcile_rows,
)
from app.extraction.schema import InvoiceLineItem
from tests.support.fakes import sample_invoice


def line(
    description: str | None, code: str | None = None, **values: float
) -> InvoiceLineItem:
    return InvoiceLineItem(
        description=description,
        product_code=code,
        quantity=values.get("quantity"),
        unit=None,
        unit_price=values.get("unit_price"),
        tax_rate=None,
        amount=values.get("amount"),
    )


@pytest.mark.parametrize(
    ("left", "right", "status"),
    [
        ("ACME  GmbH", "acme gmbh", Status.EQUAL),
        ("ACME", "Globex", Status.DIFFERENT),
        (1487.5, 1487.504, Status.EQUAL),  # within a cent
        (1487.5, 1487.52, Status.DIFFERENT),
        (None, "x", Status.LEFT_MISSING),
        ("x", None, Status.RIGHT_MISSING),
        (None, None, Status.BOTH_MISSING),
        (dt.date(2026, 11, 3), dt.date(2026, 11, 3), Status.EQUAL),
    ],
)
def test_compare_values(left: object, right: object, status: Status) -> None:
    assert compare_values("f", left, right).status == status


def test_identical_invoices_compare_equal() -> None:
    fields, lines = compare_invoices(sample_invoice(), sample_invoice())

    assert {f.status for f in fields} <= {Status.EQUAL, Status.BOTH_MISSING}
    assert [pair.status for pair in lines] == ["equal"]


def test_lines_without_a_product_code_on_either_side_are_equal() -> None:
    """Found by the reconciliation evaluation: a field neither line states
    made every such line "different", even against an identical copy."""
    invoice = sample_invoice()
    no_codes = invoice.model_copy(
        update={
            "line_items": [
                line.model_copy(update={"product_code": None})
                for line in invoice.line_items
            ]
        }
    )

    _, lines = compare_invoices(no_codes, no_codes)

    assert [pair.status for pair in lines] == ["equal"] * len(no_codes.line_items)


def test_changed_total_and_vendor_are_reported() -> None:
    vendor = sample_invoice().vendor.model_copy(update={"tax_id": "DE999"})
    fields, _ = compare_invoices(
        sample_invoice(), sample_invoice(total_amount=1500.0, vendor=vendor)
    )

    different = {
        f.field: (f.left, f.right) for f in fields if f.status == Status.DIFFERENT
    }
    assert different == {
        "vendor.tax_id": ("DE123456789", "DE999"),
        "total_amount": (1487.5, 1500.0),
    }


def test_line_items_pair_by_code_then_description() -> None:
    left = [
        line("Consulting hours", "SVC-1", amount=1250),
        line("Travel expenses Berlin", amount=80),
        line("Printer paper", amount=12),
    ]
    right = [
        line("Travel expense - Berlin", amount=95),  # described slightly differently
        line("Advisory (renamed)", "svc 1", amount=1250),  # same code, other format
        line("Server rack", amount=999),
    ]

    pairs = match_line_items(left, right)

    assert [(p.left_index, p.right_index, p.matched_by, p.status) for p in pairs] == [
        (1, 2, "product_code", "different"),  # description differs
        (2, 1, "description", "different"),  # amount 80 vs 95
        (3, None, None, "left_only"),
        (None, 3, None, "right_only"),
    ]
    travel = next(f for f in pairs[1].fields if f.field == "amount")
    assert (travel.left, travel.right) == (80, 95)


def test_text_diff_ignores_whitespace_and_counts_changes() -> None:
    left = "Invoice INV-1\n\nTotal:   100 EUR\nThanks"
    right = "Invoice INV-1\nTotal: 120 EUR\nThanks"

    result = compare_text(left, right)

    assert (result.added, result.removed) == (1, 1)
    assert "-Total: 100 EUR" in result.diff and "+Total: 120 EUR" in result.diff
    assert 0.5 < result.similarity < 1
    assert compare_text("same\ntext", "same  \n\n text").similarity == 1.0


@pytest.mark.parametrize(
    ("token", "readings"),
    [
        ("1.250,50", {Decimal("1250.5")}),
        ("1,250.50", {Decimal("1250.5")}),
        ("1.250", {Decimal("1250"), Decimal("1.25")}),
        ("42", {Decimal("42")}),
        ("1 250,00", {Decimal("1250")}),
    ],
)
def test_number_readings(token: str, readings: set[Decimal]) -> None:
    assert number_readings(token) == readings


def test_numbers_in_text() -> None:
    values = numbers_in("Total due: 1.487,50 EUR for 10 h at 125,00")

    assert {Decimal("1487.5"), Decimal("10"), Decimal("125")} <= values


@pytest.mark.parametrize(
    ("cell", "found"),
    [
        (1487.5, True),
        (125, True),
        (999, False),
        ("ACME GmbH", True),
        ("Globex", False),
        ("2026-07-03T00:00:00", True),  # printed as 03.07.2026
        ("1.487,50", True),
        (True, True),
    ],
)
def test_cell_found(cell: object, found: bool) -> None:
    text = "acme gmbh invoice of 03.07.2026: 10 h at 125,00 = 1.487,50 eur"
    assert cell_found(cell, text, numbers_in(text)) is found


def test_reconcile_rows() -> None:
    pdf_text = (
        "Invoice ACME GmbH\n"
        "Bolts 120 pcs 0,50 60,00\n"
        "Nuts 80 pcs 0,25 20,00\n"
        "Total 80,00"
    )
    sheets = [
        (
            "Order",
            [
                ["item", "qty", "price", "amount"],
                ["Bolts", 120, 0.5, 60],
                ["Nuts", 80, 0.25, 25],  # amount differs from the PDF
                [None, None, None, None],  # empty rows are skipped
                ["Washers", 500, 0.02, 10],  # not on the invoice at all
            ],
        )
    ]

    checks = reconcile_rows(sheets, pdf_text)

    assert [(c.row_number, c.status, c.missing) for c in checks] == [
        (1, "missing", ["item", "qty", "price", "amount"]),  # header isn't printed
        (2, "found", []),
        (3, "partial", [25]),
        (5, "missing", ["Washers", 500, 0.02, 10]),
    ]


def test_neighbouring_numbers_are_not_merged() -> None:
    values = numbers_in("Bolts 120 pcs 0,50 60,00 and 1 250,00 total")

    assert {Decimal("120"), Decimal("0.5"), Decimal("60"), Decimal("1250")} <= values
