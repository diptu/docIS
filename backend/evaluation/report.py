"""
Aggregating case results into accuracy metrics and a report.
"""

import json
import statistics
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from evaluation.runner import CaseResult
from evaluation.scoring import (
    CORRECT,
    CORRECT_NULL,
    HALLUCINATED,
    MISSED,
    OUTCOMES,
    WRONG,
)

# Claude Opus 5.5 list prices, USD per million tokens (estimate only).
PRICE_INPUT, PRICE_OUTPUT, PRICE_CACHE_READ = 4.0, 20.0, 0.20


def _pct(numerator: float, denominator: float) -> float | None:
    return round(100 * numerator / denominator, 1) if denominator else None


def _accuracy(counts: Counter[str]) -> dict[str, Any]:
    total = sum(counts.values())
    with_value = counts[CORRECT] + counts[WRONG] + counts[MISSED]
    expected_null = counts[CORRECT_NULL] + counts[HALLUCINATED]
    return {
        "accuracy": _pct(counts[CORRECT] + counts[CORRECT_NULL], total),
        "value_accuracy": _pct(counts[CORRECT], with_value),
        "null_accuracy": _pct(counts[CORRECT_NULL], expected_null),
        "hallucination_rate": _pct(counts[HALLUCINATED], expected_null),
        "miss_rate": _pct(counts[MISSED], with_value),
        "counts": {k: counts[k] for k in OUTCOMES},
    }


def summarise(results: Sequence[CaseResult]) -> dict[str, Any]:
    scored = [r for r in results if r.score is not None]
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

    precision = _pct(matched_lines, extracted_lines)
    recall = _pct(matched_lines, expected_lines)
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

    def exact(group: Sequence[CaseResult]) -> float | None:
        return _pct(sum(1 for r in group if r.score and r.score.exact), len(group))

    return {
        "cases": len(results),
        "errors": [{"case": r.case.name, "error": r.error} for r in results if r.error],
        "documents_exact": exact(scored),
        "documents_fields_correct": _pct(
            sum(1 for r in scored if r.score and r.score.fields_ok), len(scored)
        ),
        "amount_checks_pass": _pct(
            sum(1 for r in scored if r.checks_pass), len(scored)
        ),
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
        },
        "tokens": {
            "input": tokens_in,
            "output": tokens_out,
            "cache_read": tokens_cache,
        },
        "estimated_cost_usd": round(cost, 4),
    }


def _fmt(value: Any) -> str:
    return (
        "—"
        if value is None
        else f"{value}%"
        if isinstance(value, float)
        else str(value)
    )


def _row(*cells: object) -> str:
    return "| " + " | ".join(str(cell) for cell in cells) + " |"


def render_markdown(summary: dict[str, Any], llm: str, dataset: str) -> str:
    f = summary["fields"]
    lines = summary["line_items"]
    timing = summary["timing_seconds"]
    tokens = summary["tokens"]
    headline = [
        ("Documents fully correct (fields + line items)", summary["documents_exact"]),
        (
            "Documents with all header/party/total fields correct",
            summary["documents_fields_correct"],
        ),
        ("Field accuracy (incl. correct nulls)", f["accuracy"]),
        ("Value accuracy (fields that have a value)", f["value_accuracy"]),
        ("Null accuracy (fields absent from the document)", f["null_accuracy"]),
        (
            "Hallucination rate (value invented for an absent field)",
            f["hallucination_rate"],
        ),
        ("Miss rate (value present but returned as null)", f["miss_rate"]),
        (
            "Line items: precision / recall / F1",
            " / ".join(_fmt(lines[k]) for k in ("precision", "recall", "f1")),
        ),
        ("Line-item field accuracy", lines["field_accuracy"]["accuracy"]),
        ("Extractions whose amount checks pass", summary["amount_checks_pass"]),
        (
            "Median content extraction / model time",
            f"{timing['content_median']} s / {timing['model_median']} s",
        ),
        (
            "Tokens (input / output / cache read)",
            f"{tokens['input']} / {tokens['output']} / {tokens['cache_read']}",
        ),
        ("Estimated cost", f"${summary['estimated_cost_usd']}"),
    ]
    out = [
        "# Extraction accuracy",
        "",
        f"Model: `{llm}` · dataset: `{dataset}` · {summary['cases']} cases"
        f" · {len(summary['errors'])} errors",
        "",
        "## Headline",
        "",
        _row("Metric", "Value"),
        _row("---", "---"),
        *[_row(label, _fmt(value)) for label, value in headline],
        "",
        "## By format",
        "",
        _row("Format", "Cases", "Documents fully correct", "Field accuracy"),
        _row("---", "---", "---", "---"),
        *[
            _row(fmt, g["cases"], _fmt(g["documents_exact"]), _fmt(g["field_accuracy"]))
            for fmt, g in summary["by_format"].items()
        ],
        "",
        "## Per field",
        "",
        _row("Field", "Accuracy", "Wrong", "Missed", "Hallucinated"),
        _row("---", "---", "---", "---", "---"),
        *[
            _row(
                name,
                _fmt(m["accuracy"]),
                m["counts"][WRONG],
                m["counts"][MISSED],
                m["counts"][HALLUCINATED],
            )
            for name, m in summary["per_field"].items()
        ],
    ]
    if summary["errors"]:
        out += [
            "",
            "## Errors",
            "",
            *[f"- `{e['case']}`: {e['error']}" for e in summary["errors"]],
        ]
    return "\n".join(out) + "\n"


def write_report(
    results: Sequence[CaseResult], out: Path, llm: str, dataset: str
) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    summary = summarise(results)
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    details = [
        {
            "case": r.case.name,
            "format": r.case.format,
            "locale": r.case.locale,
            "error": r.error,
            "exact": r.score.exact if r.score else None,
            "mistakes": [
                {
                    "field": f.field,
                    "expected": f.expected,
                    "actual": f.actual,
                    "outcome": f.outcome,
                }
                for f in ((r.score.fields + r.score.line_fields) if r.score else [])
                if not f.ok
            ],
            "lines": {
                "expected": r.score.lines_expected,
                "extracted": r.score.lines_extracted,
                "matched": r.score.lines_matched,
            }
            if r.score
            else None,
            "predicted": r.predicted,
        }
        for r in results
    ]
    (out / "cases.json").write_text(json.dumps(details, indent=2, ensure_ascii=False))
    (out / "report.md").write_text(render_markdown(summary, llm, dataset))
    return summary
