"""
Comparing two documents: extracted invoices, page text, and spreadsheet rows
against PDF text.

Which sections a comparison has depends on what both sides offer:

* ``invoice``: both have an extracted invoice: header fields and totals
  side by side, line items matched by product code, then by description.
* ``text``: both have page text (PDFs, images): a line diff.
* ``rows``: one side has spreadsheet rows, the other page text: for each
  row, whether its values appear in the text (PDF vs Excel reconciliation).
"""

import datetime as dt
import difflib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Protocol

from app.extraction.schema import InvoiceData

AMOUNT_TOLERANCE = Decimal("0.01")
DESCRIPTION_MATCH = 0.6  # minimum similarity to pair line items by description
MAX_DIFF_LINES = 400
MAX_ROWS = 1000


class Status(StrEnum):
    EQUAL = "equal"
    DIFFERENT = "different"
    LEFT_MISSING = "left_missing"
    RIGHT_MISSING = "right_missing"
    BOTH_MISSING = "both_missing"


@dataclass(frozen=True)
class FieldComparison:
    field: str
    left: Any
    right: Any
    status: Status


@dataclass
class LinePair:
    left_index: int | None  # 1-based line number, None if unmatched
    right_index: int | None
    matched_by: str | None  # product_code | description
    fields: list[FieldComparison] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.left_index is None:
            return "right_only"
        if self.right_index is None:
            return "left_only"
        # A field neither line states (say, no product code on either
        # side) is not a difference.
        return (
            "equal"
            if all(f.status in (Status.EQUAL, Status.BOTH_MISSING) for f in self.fields)
            else "different"
        )


# ------------------------------------------------------------------ fields


def _norm_text(value: str) -> str:
    return " ".join(value.lower().split())


def _norm_code(value: str | None) -> str:
    return re.sub(r"[^0-9a-z]", "", (value or "").lower())


def compare_values(name: str, left: Any, right: Any) -> FieldComparison:
    """Compare two field values; text ignores case and spacing, numbers
    allow a cent of rounding."""
    if left is None and right is None:
        status = Status.BOTH_MISSING
    elif left is None:
        status = Status.LEFT_MISSING
    elif right is None:
        status = Status.RIGHT_MISSING
    elif isinstance(left, int | float) and isinstance(right, int | float):
        equal = abs(Decimal(str(left)) - Decimal(str(right))) <= AMOUNT_TOLERANCE
        status = Status.EQUAL if equal else Status.DIFFERENT
    else:
        equal = _norm_text(str(left)) == _norm_text(str(right))
        status = Status.EQUAL if equal else Status.DIFFERENT
    return FieldComparison(name, _json(left), _json(right), status)


def _json(value: Any) -> Any:
    return value.isoformat() if isinstance(value, dt.date) else value


HEADER_FIELDS = (
    "invoice_number",
    "invoice_date",
    "due_date",
    "currency",
    "purchase_order_number",
    "payment_terms",
)
TOTAL_FIELDS = ("subtotal", "tax_amount", "total_amount", "amount_due")
PARTY_FIELDS = ("name", "tax_id", "address")
LINE_FIELDS = (
    "description",
    "product_code",
    "quantity",
    "unit_price",
    "tax_rate",
    "amount",
)


def compare_invoices(
    left: InvoiceData, right: InvoiceData
) -> tuple[list[FieldComparison], list[LinePair]]:
    """Header, party and total fields, then line items."""
    fields = [
        compare_values(n, getattr(left, n), getattr(right, n)) for n in HEADER_FIELDS
    ]
    for party in ("vendor", "customer"):
        for name in PARTY_FIELDS:
            fields.append(
                compare_values(
                    f"{party}.{name}",
                    getattr(getattr(left, party), name),
                    getattr(getattr(right, party), name),
                )
            )
    fields += [
        compare_values(n, getattr(left, n), getattr(right, n)) for n in TOTAL_FIELDS
    ]
    return fields, match_line_items(left.line_items, right.line_items)


class MatchableLine(Protocol):
    """What line matching needs: an extracted line or a stored line row."""

    @property
    def product_code(self) -> str | None: ...
    @property
    def description(self) -> str | None: ...


def _line_fields(left: MatchableLine, right: MatchableLine) -> list[FieldComparison]:
    return [compare_values(n, getattr(left, n), getattr(right, n)) for n in LINE_FIELDS]


def match_line_items(
    left: Sequence[MatchableLine], right: Sequence[MatchableLine]
) -> list[LinePair]:
    """Pair line items: same product code first, then the most similar
    descriptions (at least DESCRIPTION_MATCH alike); the rest are unmatched.
    Pairs are listed in left-document order, then right-only lines."""
    unmatched_right = set(range(len(right)))
    pairs: dict[int, tuple[int, str]] = {}

    for i, item in enumerate(left):
        code = _norm_code(item.product_code)
        if not code:
            continue
        j = next(
            (
                j
                for j in sorted(unmatched_right)
                if _norm_code(right[j].product_code) == code
            ),
            None,
        )
        if j is not None:
            pairs[i] = (j, "product_code")
            unmatched_right.discard(j)

    candidates = sorted(
        (
            (
                difflib.SequenceMatcher(
                    None,
                    _norm_text(left[i].description or ""),
                    _norm_text(right[j].description or ""),
                ).ratio(),
                i,
                j,
            )
            for i in range(len(left))
            if i not in pairs and left[i].description
            for j in unmatched_right
            if right[j].description
        ),
        reverse=True,
    )
    for ratio, i, j in candidates:
        if ratio < DESCRIPTION_MATCH:
            break
        if i in pairs or j not in unmatched_right:
            continue
        pairs[i] = (j, "description")
        unmatched_right.discard(j)

    result = []
    for i, item in enumerate(left):
        if i in pairs:
            j, how = pairs[i]
            result.append(LinePair(i + 1, j + 1, how, _line_fields(item, right[j])))
        else:
            result.append(LinePair(i + 1, None, None))
    result.extend(LinePair(None, j + 1, None) for j in sorted(unmatched_right))
    return result


# -------------------------------------------------------------------- text


@dataclass(frozen=True)
class TextComparison:
    similarity: float  # 0-1
    added: int  # lines only on the right
    removed: int  # lines only on the left
    diff: list[str]  # unified diff, capped at MAX_DIFF_LINES
    truncated: bool


def _lines(text: str) -> list[str]:
    return [" ".join(line.split()) for line in text.splitlines() if line.strip()]


def compare_text(left: str, right: str) -> TextComparison:
    """Line diff, ignoring blank lines and whitespace differences."""
    a, b = _lines(left), _lines(right)
    diff = list(difflib.unified_diff(a, b, "left", "right", lineterm="", n=2))
    added = sum(
        1 for line in diff if line.startswith("+") and not line.startswith("+++")
    )
    removed = sum(
        1 for line in diff if line.startswith("-") and not line.startswith("---")
    )
    similarity = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
    return TextComparison(
        similarity=round(similarity, 4),
        added=added,
        removed=removed,
        diff=diff[:MAX_DIFF_LINES],
        truncated=len(diff) > MAX_DIFF_LINES,
    )


# --------------------------------------------------- rows vs text (PDF/XLSX)

# A number as printed: "1 250,00" (space-grouped thousands; a space is
# only part of the number when a 3-digit group follows, so neighbouring
# numbers like "0,50 60,00" stay separate), "1.250,50", "1,250.50", "42".
_NUMBER = re.compile(
    r"-?\d{1,3}(?:[  ]\d{3})+(?:[.,]\d+)?(?!\d)"
    r"|-?\d[\d.,']*\d"
    r"|-?\d"
)


def number_readings(token: str) -> set[Decimal]:
    """Every plausible value of a printed number.

    "1.250,50" -> {1250.50}; "1,250.50" -> {1250.50}; "1.250" -> {1250,
    1.25} (thousands separator or decimal point: both are kept).
    """
    raw = token.replace(" ", "").replace("\u00a0", "").replace("'", "")
    separators = [ch for ch in raw if ch in ".,"]
    candidates: list[str] = []
    if not separators:
        candidates = [raw]
    elif len(set(separators)) == 2:
        # Both kinds: the last one is the decimal separator.
        decimal_sep = separators[-1]
        group_sep = "," if decimal_sep == "." else "."
        candidates = [raw.replace(group_sep, "").replace(decimal_sep, ".")]
    elif len(separators) > 1:
        candidates = [raw.replace(separators[0], "")]  # repeated: grouping
    else:
        sep = separators[0]
        decimals = raw.split(sep)[1]
        candidates = [raw.replace(sep, ".")]
        if len(decimals) == 3:  # "1.250" may also be one thousand two fifty
            candidates.append(raw.replace(sep, ""))
    readings: set[Decimal] = set()
    for candidate in candidates:
        try:
            readings.add(Decimal(candidate).normalize())
        except InvalidOperation:
            continue
    return readings


def numbers_in(text: str) -> set[Decimal]:
    values: set[Decimal] = set()
    for token in _NUMBER.findall(text):
        values |= number_readings(token.strip(" ,."))
    return values


def _date_spellings(value: dt.date) -> list[str]:
    d, m, y = value.day, value.month, value.year
    return [
        value.isoformat(),
        f"{d:02d}.{m:02d}.{y}",
        f"{d}.{m}.{y}",
        f"{d:02d}/{m:02d}/{y}",
        f"{m:02d}/{d:02d}/{y}",
        f"{d}/{m}/{y}",
        f"{m}/{d}/{y}",
        value.strftime("%d %B %Y").lower(),
        value.strftime("%B %d, %Y").lower(),
        f"{d} {value.strftime('%b').lower()} {y}",
    ]


def cell_found(cell: object, text: str, numbers: set[Decimal]) -> bool:
    """Whether a spreadsheet cell's value appears in the (lower-cased,
    whitespace-normalised) text."""
    if isinstance(cell, bool):
        return True  # booleans are never printed as such; don't penalise
    if isinstance(cell, int | float):
        return Decimal(str(cell)).normalize() in numbers
    value = str(cell).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}(T00:00:00)?", value):
        date = dt.date.fromisoformat(value[:10])
        return any(spelling in text for spelling in _date_spellings(date))
    if _NUMBER.fullmatch(value) and (readings := number_readings(value)):
        return bool(readings & numbers)
    return _norm_text(value) in text


@dataclass(frozen=True)
class RowCheck:
    sheet_name: str
    row_number: int  # 1-based, as in the sheet
    status: str  # found | partial | missing
    cells: list[Any]
    missing: list[Any]


def reconcile_rows(
    sheets: Iterable[tuple[str, Sequence[Sequence[object]]]], text: str
) -> list[RowCheck]:
    """Check each non-empty spreadsheet row's values against the text."""
    lowered = _norm_text(text)
    numbers = numbers_in(text)
    checks: list[RowCheck] = []
    for name, rows in sheets:
        for number, row in enumerate(rows, start=1):
            cells = [cell for cell in row if cell is not None and str(cell).strip()]
            if not cells:
                continue
            missing = [cell for cell in cells if not cell_found(cell, lowered, numbers)]
            status = (
                "found"
                if not missing
                else "missing"
                if len(missing) == len(cells)
                else "partial"
            )
            checks.append(RowCheck(name, number, status, list(row), missing))
            if len(checks) >= MAX_ROWS:
                return checks
    return checks
