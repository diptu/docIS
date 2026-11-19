"""
Deterministic arithmetic checks of extracted invoice amounts.

All arithmetic uses Decimal (amounts are converted via their printed decimal
form), so 0.1 + 0.2 really is 0.3. A check is ``skipped`` when the values it
needs are missing; only a ``failed`` check is evidence of an inconsistency.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.extraction.schema import InvoiceData

DEFAULT_TOLERANCE = Decimal("0.01")
HUNDRED = Decimal(100)


class CheckStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class CheckCode(StrEnum):
    LINE_AMOUNTS = "line_amounts"  # quantity x unit price = line amount
    LINES_SUBTOTAL = "lines_subtotal"  # sum of line amounts = subtotal
    TAX_AMOUNT = "tax_amount"  # sum of line amount x rate = tax amount
    TOTAL = "total"  # subtotal + tax = total
    AMOUNT_DUE = "amount_due"  # amount due <= total


@dataclass(frozen=True)
class Check:
    code: CheckCode
    status: CheckStatus
    message: str
    expected: Decimal | None = None
    actual: Decimal | None = None


def _d(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _money(value: Decimal) -> str:
    return f"{value.normalize():f}"


def _within(a: Decimal, b: Decimal, tolerance: Decimal) -> bool:
    return abs(a - b) <= tolerance


def _compare(
    code: CheckCode,
    label: str,
    expected: Decimal,
    actual: Decimal,
    tolerance: Decimal,
) -> Check:
    if _within(expected, actual, tolerance):
        return Check(code, CheckStatus.PASSED, f"{label} matches.", expected, actual)
    return Check(
        code,
        CheckStatus.FAILED,
        f"{label}: expected {_money(expected)}, invoice states {_money(actual)} "
        f"(difference {_money(actual - expected)}).",
        expected,
        actual,
    )


def _check_line_amounts(data: InvoiceData, tolerance: Decimal) -> Check:
    code = CheckCode.LINE_AMOUNTS
    checked = 0
    wrong: list[str] = []
    for number, item in enumerate(data.line_items, start=1):
        quantity, price, amount = (
            _d(item.quantity),
            _d(item.unit_price),
            _d(item.amount),
        )
        if quantity is None or price is None or amount is None:
            continue
        checked += 1
        expected = quantity * price
        if not _within(expected, amount, tolerance):
            wrong.append(f"line {number} ({_money(expected)} vs {_money(amount)})")
    if not checked:
        return Check(
            code, CheckStatus.SKIPPED, "No line has quantity, price and amount."
        )
    if wrong:
        return Check(
            code,
            CheckStatus.FAILED,
            "Quantity x unit price differs from the line amount on "
            + ", ".join(wrong)
            + ".",
        )
    return Check(code, CheckStatus.PASSED, f"{checked} line amount(s) match.")


def _line_amounts(data: InvoiceData) -> list[Decimal] | None:
    """Every line amount, or None if there are no lines or any is missing."""
    amounts = [_d(item.amount) for item in data.line_items]
    if not amounts:
        return None
    present = [amount for amount in amounts if amount is not None]
    return present if len(present) == len(amounts) else None


def _check_lines_subtotal(data: InvoiceData, tolerance: Decimal) -> Check:
    code = CheckCode.LINES_SUBTOTAL
    amounts = _line_amounts(data)
    if amounts is None:
        return Check(code, CheckStatus.SKIPPED, "Not every line item has an amount.")
    lines_sum = sum(amounts, Decimal(0))
    subtotal, total = _d(data.subtotal), _d(data.total_amount)

    if subtotal is not None:
        check = _compare(
            code, "Sum of lines vs subtotal", lines_sum, subtotal, tolerance
        )
        if check.status == CheckStatus.PASSED:
            return check
    # Some invoices print gross (tax-inclusive) line amounts.
    if total is not None and _within(lines_sum, total, tolerance):
        message = "Line amounts include tax and add up to the total."
        return Check(code, CheckStatus.PASSED, message, lines_sum, total)
    if subtotal is not None:
        return check
    if total is not None:
        return _compare(code, "Sum of lines vs total", lines_sum, total, tolerance)
    return Check(code, CheckStatus.SKIPPED, "Neither subtotal nor total is stated.")


def _check_tax(data: InvoiceData, tolerance: Decimal) -> Check:
    code = CheckCode.TAX_AMOUNT
    tax = _d(data.tax_amount)
    if tax is None:
        return Check(code, CheckStatus.SKIPPED, "Tax amount is not stated.")
    expected = Decimal(0)
    for item in data.line_items:
        amount, rate = _d(item.amount), _d(item.tax_rate)
        if amount is None or rate is None:
            return Check(
                code,
                CheckStatus.SKIPPED,
                "Not every line item has an amount and a tax rate.",
            )
        expected += amount * rate / HUNDRED
    if not data.line_items:
        return Check(code, CheckStatus.SKIPPED, "There are no line items.")
    # Invoices may round tax per line: allow one rounding step per line.
    slack = tolerance * len(data.line_items)
    return _compare(code, "Tax from line rates vs tax amount", expected, tax, slack)


def _check_total(data: InvoiceData, tolerance: Decimal) -> Check:
    code = CheckCode.TOTAL
    subtotal, tax, total = _d(data.subtotal), _d(data.tax_amount), _d(data.total_amount)
    if subtotal is None or tax is None or total is None:
        message = "Subtotal, tax amount or total is not stated."
        return Check(code, CheckStatus.SKIPPED, message)
    return _compare(code, "Subtotal + tax vs total", subtotal + tax, total, tolerance)


def _check_amount_due(data: InvoiceData, tolerance: Decimal) -> Check:
    code = CheckCode.AMOUNT_DUE
    due, total = _d(data.amount_due), _d(data.total_amount)
    if due is None or total is None:
        message = "Amount due or total is not stated."
        return Check(code, CheckStatus.SKIPPED, message)
    if due <= total + tolerance:
        message = "Amount due does not exceed the total."
        return Check(code, CheckStatus.PASSED, message, total, due)
    return Check(
        code,
        CheckStatus.FAILED,
        f"Amount due {_money(due)} exceeds the total {_money(total)}.",
        total,
        due,
    )


def validate_amounts(
    data: InvoiceData, tolerance: Decimal = DEFAULT_TOLERANCE
) -> list[Check]:
    """Run every amount check. Non-invoices get no checks."""
    if not data.is_invoice:
        return []
    return [
        _check_line_amounts(data, tolerance),
        _check_lines_subtotal(data, tolerance),
        _check_tax(data, tolerance),
        _check_total(data, tolerance),
        _check_amount_due(data, tolerance),
    ]


def check_to_dict(check: Check) -> dict[str, str | None]:
    """A check as JSON-safe values (amounts as decimal strings)."""
    return {
        "code": check.code.value,
        "status": check.status.value,
        "message": check.message,
        "expected": str(check.expected) if check.expected is not None else None,
        "actual": str(check.actual) if check.actual is not None else None,
    }
