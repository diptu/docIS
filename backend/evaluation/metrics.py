"""
Metrics: scoring one extracted invoice against its ground truth, and
aggregating many scored cases into accuracy, reliability and timing figures.

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
import statistics
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from app.compare.service import match_line_items
from app.extraction.schema import InvoiceData, InvoiceLineItem

if TYPE_CHECKING:
    from evaluation.evaluate_extraction import CaseResult

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


def score_invoice(
    expected: InvoiceData,
    actual: InvoiceData,
    scored_fields: frozenset[str] | None = None,
) -> CaseScore:
    """Score header fields, parties, totals and line items.

    ``scored_fields`` limits scoring to the fields a dataset actually labels
    (names as in ``FieldScore.field``: ``invoice_number``, ``vendor.name``,
    ``line.amount``, ...). Unlabelled fields are neither right nor wrong.
    Line items are scored only if some ``line.*`` field is labelled.
    ``None`` scores everything (fully labelled ground truth).
    """

    def labelled(name: str) -> bool:
        return scored_fields is None or name in scored_fields

    fields = [
        _score(n, getattr(expected, n), getattr(actual, n))
        for n in HEADER_FIELDS
        if labelled(n)
    ]
    for party in ("vendor", "customer"):
        for name in PARTY_FIELDS:
            if labelled(f"{party}.{name}"):
                fields.append(
                    _score(
                        f"{party}.{name}",
                        getattr(getattr(expected, party), name),
                        getattr(getattr(actual, party), name),
                    )
                )
    fields += [
        _score(n, getattr(expected, n), getattr(actual, n))
        for n in TOTAL_FIELDS
        if labelled(n)
    ]

    line_names = [n for n in LINE_FIELDS if labelled(f"line.{n}")]
    if not line_names:
        return CaseScore(fields, 0, 0, 0)
    pairs = match_line_items(expected.line_items, actual.line_items)
    line_fields: list[FieldScore] = []
    for pair in pairs:
        if pair.left_index is None or pair.right_index is None:
            continue
        want: InvoiceLineItem = expected.line_items[pair.left_index - 1]
        got: InvoiceLineItem = actual.line_items[pair.right_index - 1]
        line_fields += [
            _score(f"line.{n}", getattr(want, n), getattr(got, n)) for n in line_names
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


# --- Aggregation -------------------------------------------------------

# Claude Opus 5.5 list prices, USD per million tokens (estimate only).
PRICE_INPUT, PRICE_OUTPUT, PRICE_CACHE_READ = 4.0, 20.0, 0.20


def percent(numerator: float, denominator: float) -> float | None:
    return round(100 * numerator / denominator, 1) if denominator else None


def _accuracy(counts: Counter[str]) -> dict[str, Any]:
    total = sum(counts.values())
    with_value = counts[CORRECT] + counts[WRONG] + counts[MISSED]
    expected_null = counts[CORRECT_NULL] + counts[HALLUCINATED]
    return {
        "accuracy": percent(counts[CORRECT] + counts[CORRECT_NULL], total),
        "value_accuracy": percent(counts[CORRECT], with_value),
        "null_accuracy": percent(counts[CORRECT_NULL], expected_null),
        "hallucination_rate": percent(counts[HALLUCINATED], expected_null),
        "miss_rate": percent(counts[MISSED], with_value),
        "counts": {k: counts[k] for k in OUTCOMES},
    }


def _percentile(values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile (an actual observed value)."""
    ordered = sorted(values)
    rank = max(1, -(-len(ordered) * pct // 100))  # ceil
    return ordered[int(rank) - 1]


def _timing(values: Sequence[float]) -> dict[str, float | None]:
    """Mean, median, 95th percentile and maximum, in seconds."""
    if not values:
        return {"mean": None, "median": None, "p95": None, "max": None}
    return {
        "mean": round(statistics.fmean(values), 3),
        "median": round(statistics.median(values), 3),
        "p95": round(_percentile(values, 95), 3),
        "max": round(max(values), 3),
    }


def _failure_stage(error: str) -> str:
    """Where a case failed: content extraction (parsing/OCR), the model
    call, or before either (e.g. unsupported file)."""
    if error.startswith("Content:"):
        return "content_extraction"
    if error.startswith("Model:"):
        return "ai_extraction"
    return "other"


def _reliability(results: Sequence["CaseResult"]) -> dict[str, Any]:
    failed = [r for r in results if r.error]
    stages = Counter(_failure_stage(r.error) for r in failed if r.error)
    return {
        "documents": len(results),
        "succeeded": len(results) - len(failed),
        "failed": len(failed),
        "success_rate": percent(len(results) - len(failed), len(results)),
        "failure_rate": percent(len(failed), len(results)),
        "failures_by_stage": {
            stage: stages[stage]
            for stage in ("content_extraction", "ai_extraction", "other")
        },
    }


def _flagging(scored: Sequence["CaseResult"]) -> dict[str, Any] | None:
    """For datasets labelling ``should_flag`` (amounts that don't add up):
    do the amount checks on the extraction flag exactly those documents?
    Flagged = some check failed."""
    labelled = [r for r in scored if r.case.should_flag is not None]
    if not labelled:
        return None
    flagged = {id(r): r.checks_pass is False for r in labelled}
    tp = sum(1 for r in labelled if r.case.should_flag and flagged[id(r)])
    fp = sum(1 for r in labelled if not r.case.should_flag and flagged[id(r)])
    positives = sum(1 for r in labelled if r.case.should_flag)
    return {
        "documents": len(labelled),
        "should_flag": positives,
        **binary_classification(
            tp=tp, fp=fp, fn=positives - tp, tn=len(labelled) - positives - fp
        ),
    }


def summarise(results: Sequence["CaseResult"]) -> dict[str, Any]:
    scored = [r for r in results if r.score is not None]
    succeeded = [r for r in results if r.error is None]
    fields: Counter[str] = Counter()
    per_field: dict[str, Counter[str]] = defaultdict(Counter)
    line_fields: Counter[str] = Counter()
    expected_lines = extracted_lines = matched_lines = 0
    by_format: dict[str, list[CaseResult]] = defaultdict(list)
    for result in scored:
        assert result.score is not None
        by_format[result.case.format].append(result)
        for f in result.score.fields:
            fields[f.outcome] += 1
            per_field[f.field][f.outcome] += 1
        for f in result.score.line_fields:
            line_fields[f.outcome] += 1
        expected_lines += result.score.lines_expected
        extracted_lines += result.score.lines_extracted
        matched_lines += result.score.lines_matched

    precision = percent(matched_lines, extracted_lines)
    recall = percent(matched_lines, expected_lines)
    f1 = (
        round(2 * precision * recall / (precision + recall), 1)
        if precision and recall
        else None
    )
    tokens_in = sum(r.input_tokens for r in results)
    tokens_out = sum(r.output_tokens for r in results)
    tokens_cache = sum(r.cache_read_tokens for r in results)
    cost = (
        tokens_in * PRICE_INPUT
        + tokens_out * PRICE_OUTPUT
        + tokens_cache * PRICE_CACHE_READ
    ) / 1e6

    def exact(group: Sequence["CaseResult"]) -> float | None:
        return percent(sum(1 for r in group if r.score and r.score.exact), len(group))

    return {
        "cases": len(results),
        "errors": [{"case": r.case.name, "error": r.error} for r in results if r.error],
        "documents_exact": exact(scored),
        "documents_fields_correct": percent(
            sum(1 for r in scored if r.score and r.score.fields_ok), len(scored)
        ),
        "amount_checks_pass": percent(
            sum(1 for r in scored if r.checks_pass), len(scored)
        ),
        "flagging": _flagging(scored),
        "fields": _accuracy(fields),
        "per_field": {
            name: _accuracy(counts) for name, counts in sorted(per_field.items())
        },
        "line_items": {
            "expected": expected_lines,
            "extracted": extracted_lines,
            "matched": matched_lines,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "field_accuracy": _accuracy(line_fields),
        },
        "by_format": {
            fmt: {
                "cases": len(group),
                "documents_exact": exact(group),
                "field_accuracy": _accuracy(
                    Counter(f.outcome for r in group if r.score for f in r.score.fields)
                )["accuracy"],
            }
            for fmt, group in sorted(by_format.items())
        },
        "reliability": _reliability(results),
        "timing_seconds": {
            "content_median": round(
                statistics.median(r.content_seconds for r in results), 3
            )
            if results
            else None,
            "model_median": round(
                statistics.median(r.model_seconds for r in results), 3
            )
            if results
            else None,
            # Per successfully processed document, end to end.
            "total": _timing([r.content_seconds + r.model_seconds for r in succeeded]),
            "content_extraction": _timing([r.content_seconds for r in succeeded]),
            "ai_extraction": _timing([r.model_seconds for r in succeeded]),
            "total_by_format": {
                fmt: _timing(
                    [
                        r.content_seconds + r.model_seconds
                        for r in succeeded
                        if r.case.format == fmt
                    ]
                )
                for fmt in sorted({r.case.format for r in succeeded})
            },
        },
        "tokens": {
            "input": tokens_in,
            "output": tokens_out,
            "cache_read": tokens_cache,
        },
        "estimated_cost_usd": round(cost, 4),
    }


def binary_classification(tp: int, fp: int, fn: int, tn: int) -> dict[str, Any]:
    """Precision, recall, F1 and specificity (percentages) for a detector."""
    precision = percent(tp, tp + fp)
    recall = percent(tp, tp + fn)
    f1 = (
        round(2 * precision * recall / (precision + recall), 1)
        if precision and recall
        else None
    )
    return {
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "true_negatives": tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "specificity": percent(tn, tn + fp),
    }
