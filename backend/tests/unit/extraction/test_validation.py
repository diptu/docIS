from decimal import Decimal

import pytest

from app.extraction.schema import InvoiceLineItem
from app.extraction.validation import CheckCode, CheckStatus, validate_amounts
from tests.support.fakes import sample_invoice


def line(
    amount: float | None,
    quantity: float | None = None,
    unit_price: float | None = None,
    tax_rate: float | None = None,
) -> InvoiceLineItem:
    return InvoiceLineItem(
        description="item",
        product_code=None,
        quantity=quantity,
        unit=None,
        unit_price=unit_price,
        tax_rate=tax_rate,
        amount=amount,
    )


def statuses(**overrides: object) -> dict[str, str]:
    checks = validate_amounts(sample_invoice(**overrides))
    return {check.code.value: check.status.value for check in checks}


def test_consistent_invoice_passes_every_applicable_check() -> None:
    assert statuses() == {
        "line_amounts": "passed",
        "lines_subtotal": "passed",
        "tax_amount": "passed",
        "total": "passed",
        "amount_due": "skipped",  # not printed on the sample
    }


def test_exact_decimal_arithmetic() -> None:
    """0.1 + 0.2 must equal 0.3 exactly; binary floats would not."""
    result = statuses(
        line_items=[line(0.1, tax_rate=0), line(0.2, tax_rate=0)],
        subtotal=0.3,
        tax_amount=0.0,
        total_amount=0.3,
    )

    assert result["lines_subtotal"] == result["total"] == "passed"


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"line_items": [line(1300.0, 10, 125.0, 19)]}, "line_amounts"),
        ({"subtotal": 1200.0}, "lines_subtotal"),
        ({"tax_amount": 250.0}, "tax_amount"),
        ({"total_amount": 1500.0}, "total"),
        ({"amount_due": 2000.0}, "amount_due"),
    ],
)
def test_each_inconsistency_is_reported(
    overrides: dict[str, object], code: str
) -> None:
    checks = {c.code.value: c for c in validate_amounts(sample_invoice(**overrides))}

    assert checks[code].status == CheckStatus.FAILED


def test_failure_message_shows_expected_and_actual() -> None:
    [*_, total, _] = validate_amounts(sample_invoice(total_amount=1500.0))

    assert total.code == CheckCode.TOTAL
    assert total.message == (
        "Subtotal + tax vs total: expected 1487.5, invoice states 1500 "
        "(difference 12.5)."
    )
    assert (total.expected, total.actual) == (Decimal("1487.5"), Decimal("1500.0"))


def test_failing_line_numbers_are_named() -> None:
    lines = [line(10.0, 1, 10.0), line(99.0, 2, 10.0)]
    [line_check, *_] = validate_amounts(sample_invoice(line_items=lines))

    assert "line 2 (20 vs 99)" in line_check.message


def test_gross_line_amounts_matching_the_total_pass() -> None:
    lines = [line(1487.5, 10, 148.75)]
    result = statuses(line_items=lines, subtotal=1250.0)

    assert result["lines_subtotal"] == "passed"


def test_lines_compared_to_total_when_no_subtotal() -> None:
    assert (
        statuses(subtotal=None, line_items=[line(100.0)], total_amount=150.0)[
            "lines_subtotal"
        ]
        == "failed"
    )


def test_tax_rounding_per_line_is_tolerated() -> None:
    # 3 lines of 0.33 at 19% = 0.1881 tax; printed per line as 0.06 x 3 = 0.18.
    lines = [line(0.33, tax_rate=19) for _ in range(3)]
    result = statuses(
        line_items=lines, subtotal=0.99, tax_amount=0.18, total_amount=1.17
    )

    assert result["tax_amount"] == "passed"


@pytest.mark.parametrize(
    ("overrides", "skipped"),
    [
        ({"line_items": []}, {"line_amounts", "lines_subtotal", "tax_amount"}),
        (
            {"line_items": [line(None)]},
            {"line_amounts", "lines_subtotal", "tax_amount"},
        ),
        (
            {"line_items": [line(100.0)], "tax_amount": 19.0},
            {"line_amounts", "tax_amount"},
        ),
        ({"tax_amount": None}, {"tax_amount", "total"}),
        (
            {"line_items": [line(5.0)], "subtotal": None, "total_amount": None},
            {"lines_subtotal"},
        ),
    ],
)
def test_missing_values_skip_checks(
    overrides: dict[str, object], skipped: set[str]
) -> None:
    result = statuses(**overrides)

    assert {code for code, status in result.items() if status == "skipped"} >= skipped


def test_amount_due_below_total_passes() -> None:
    assert statuses(amount_due=500.0)["amount_due"] == "passed"


def test_non_invoices_are_not_checked() -> None:
    assert validate_amounts(sample_invoice(is_invoice=False)) == []
