"""
Extraction evaluation: run labelled documents through the real pipeline
(content extraction -> model -> normalisation), score them against ground
truth and report the result.

Dataset layout::

    dataset/documents/<invoice number>.<pdf|xlsx|csv>
    dataset/ground_truth/<invoice number>.json
        {"format", "locale", "expected": InvoiceData}
"""

import datetime as dt
import json
import random
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anyio import to_thread

from app.extraction.document_text import render_document
from app.extraction.llm import InvoiceLLM, LLMExtraction
from app.extraction.normalize import normalize_invoice
from app.extraction.schema import InvoiceData
from app.extraction.validation import CheckStatus, validate_amounts
from app.models import DocumentPage, DocumentSheet
from app.processing.extract import extract_content
from app.processing.ocr import OcrConfig
from app.services.file_types import type_for_file_name
from evaluation.metrics import (
    HALLUCINATED,
    MISSED,
    WRONG,
    CaseScore,
    score_invoice,
    summarise,
)
from evaluation.reporting import EXTRACTION_RESULTS, write_json, write_summary


@dataclass(frozen=True)
class Case:
    name: str
    path: Path
    format: str
    locale: str
    expected: InvoiceData
    # Fields the label covers (None: all of them); see metrics.score_invoice.
    scored_fields: frozenset[str] | None = None
    # Whether the amount checks should flag the document (None: not labelled).
    should_flag: bool | None = None


def documents_dir(dataset: Path) -> Path:
    return dataset / "documents"


def ground_truth_dir(dataset: Path) -> Path:
    return dataset / "ground_truth"


def load_dataset(dataset: Path) -> list[Case]:
    """Cases are documents in ``dataset/documents/<name>.<ext>`` labelled by
    ``dataset/ground_truth/<name>.json``: ``{"format", "locale", "expected":
    InvoiceData}``, optionally with ``"scored_fields": [...]`` (a partial
    label: only these fields are scored) and ``"should_flag": bool``."""
    documents, labels = documents_dir(dataset), ground_truth_dir(dataset)
    cases = []
    for label in sorted(labels.glob("*.json")):
        matches = list(documents.glob(f"{label.stem}.*"))
        if len(matches) != 1:
            raise ValueError(
                f"{label.name}: expected exactly one document named "
                f"documents/{label.stem}.*"
            )
        data = json.loads(label.read_text())
        cases.append(
            Case(
                name=label.stem,
                path=matches[0],
                format=data.get("format", matches[0].suffix.lstrip(".")),
                locale=data.get("locale", "unknown"),
                expected=InvoiceData.model_validate(data["expected"]),
                scored_fields=frozenset(data["scored_fields"])
                if "scored_fields" in data
                else None,
                should_flag=data.get("should_flag"),
            )
        )
    return cases


class OracleLLM:
    """Returns the ground truth: checks the harness itself (expect 100%)."""

    def __init__(self) -> None:
        self.answer: InvoiceData | None = None

    async def extract(self, document_text: str) -> LLMExtraction:
        assert self.answer is not None
        return LLMExtraction(self.answer, "oracle", 0, 0, 0)


class NoisyLLM(OracleLLM):
    """The ground truth with fields corrupted at ``rate``: shows that the
    metrics detect errors, misses and hallucinations."""

    def __init__(self, rate: float, seed: int = 7) -> None:
        super().__init__()
        self.rate = rate
        self.rng = random.Random(seed)

    async def extract(self, document_text: str) -> LLMExtraction:
        assert self.answer is not None
        data = self.answer.model_dump()
        for key in (
            "invoice_number",
            "payment_terms",
            "purchase_order_number",
            "currency",
        ):
            if self.rng.random() < self.rate:
                data[key] = None if data[key] else "INVENTED"
        for key in ("total_amount", "tax_amount"):
            if self.rng.random() < self.rate and data[key] is not None:
                data[key] = round(data[key] * 1.1, 2)
        if self.rng.random() < self.rate and data["line_items"]:
            data["line_items"].pop()
        return LLMExtraction(InvoiceData.model_validate(data), "noisy", 0, 0, 0)


@dataclass
class CaseResult:
    case: Case
    score: CaseScore | None
    error: str | None
    content_seconds: float
    model_seconds: float
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    checks_pass: bool | None = None
    predicted: dict[str, Any] | None = None


def _as_rows(path: Path, media_type: str, config: OcrConfig, name: str) -> str:
    result = extract_content(path, media_type, config, name)
    pages = [DocumentPage(page_number=p.page_number, text=p.text) for p in result.pages]
    sheets = [
        DocumentSheet(sheet_number=s.sheet_number, name=s.name, rows=s.rows)
        for s in result.sheets
    ]
    return render_document(pages, sheets, max_chars=600_000)


async def run_case(case: Case, llm: InvoiceLLM, config: OcrConfig) -> CaseResult:
    file_type = type_for_file_name(case.path.name)
    if file_type is None:
        return CaseResult(case, None, "Unsupported file type", 0.0, 0.0)
    started = time.perf_counter()
    try:
        text = await to_thread.run_sync(
            _as_rows, case.path, file_type.media_type, config, case.path.name
        )
    except Exception as exc:
        return CaseResult(
            case,
            None,
            f"Content: {type(exc).__name__}: {exc}",
            time.perf_counter() - started,
            0.0,
        )
    content_seconds = time.perf_counter() - started

    if isinstance(llm, OracleLLM):
        llm.answer = case.expected
    started = time.perf_counter()
    try:
        extraction = await llm.extract(text)
    except Exception as exc:
        return CaseResult(
            case,
            None,
            f"Model: {type(exc).__name__}: {exc}",
            content_seconds,
            time.perf_counter() - started,
        )
    model_seconds = time.perf_counter() - started

    predicted = normalize_invoice(extraction.data)
    checks = validate_amounts(predicted)
    return CaseResult(
        case=case,
        score=score_invoice(case.expected, predicted, case.scored_fields),
        error=None,
        content_seconds=content_seconds,
        model_seconds=model_seconds,
        input_tokens=extraction.input_tokens,
        output_tokens=extraction.output_tokens,
        cache_read_tokens=extraction.cache_read_tokens,
        checks_pass=not any(c.status == CheckStatus.FAILED for c in checks),
        predicted=predicted.model_dump(mode="json"),
    )


async def run(
    cases: list[Case], llm: InvoiceLLM, config: OcrConfig
) -> list[CaseResult]:
    results = []
    for index, case in enumerate(cases, start=1):
        result = await run_case(case, llm, config)
        status = (
            "error"
            if result.error
            else ("exact" if result.score and result.score.exact else "scored")
        )
        print(f"  [{index}/{len(cases)}] {case.name}: {status}", flush=True)
        results.append(result)
    return results


# --- Report ------------------------------------------------------------


def _fmt(value: Any) -> str:
    return (
        "—"
        if value is None
        else f"{value}%"
        if isinstance(value, float)
        else str(value)
    )


def _secs(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f} s"


def _row(*cells: object) -> str:
    return "| " + " | ".join(str(cell) for cell in cells) + " |"


def render_markdown(summary: dict[str, Any], llm: str, dataset: str) -> str:
    f = summary["fields"]
    lines = summary["line_items"]
    timing = summary["timing_seconds"]
    tokens = summary["tokens"]
    reliability = summary["reliability"]
    headline = [
        ("Document success rate (processed end to end)", reliability["success_rate"]),
        ("Document failure rate", reliability["failure_rate"]),
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
        *(
            [
                (
                    f"Inconsistent documents flagged (of {flag['should_flag']})",
                    flag["recall"],
                ),
                ("Flags that were right (precision)", flag["precision"]),
            ]
            if (flag := summary["flagging"])
            else []
        ),
        ("Median processing time per document", _secs(timing["total"]["median"])),
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
        "## Reliability",
        "",
        _row("Documents", "Succeeded", "Failed", "Success rate", "Failure rate"),
        _row("---", "---", "---", "---", "---"),
        _row(
            reliability["documents"],
            reliability["succeeded"],
            reliability["failed"],
            _fmt(reliability["success_rate"]),
            _fmt(reliability["failure_rate"]),
        ),
        "",
        "Failures by stage: "
        + ", ".join(
            f"{stage.replace('_', ' ')} {count}"
            for stage, count in reliability["failures_by_stage"].items()
        )
        + ".",
        "",
        "## Processing time (per successful document)",
        "",
        _row("Stage", "Mean", "Median", "p95", "Max"),
        _row("---", "---", "---", "---", "---"),
        *[
            _row(
                label,
                *(_secs(timing[key][s]) for s in ("mean", "median", "p95", "max")),
            )
            for label, key in (
                ("Content extraction (parse / OCR)", "content_extraction"),
                ("AI extraction (model call)", "ai_extraction"),
                ("Total", "total"),
            )
        ],
        *[
            _row(
                f"Total, {fmt}",
                *(_secs(t[s]) for s in ("mean", "median", "p95", "max")),
            )
            for fmt, t in timing["total_by_format"].items()
        ],
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


def case_details(results: Sequence[CaseResult]) -> list[dict[str, Any]]:
    """Every case with its mistakes (expected vs extracted) and prediction."""
    return [
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


def write_results(
    results: Sequence[CaseResult], results_dir: Path, llm: str, dataset: str
) -> dict[str, Any]:
    """Write ``extraction_results.json`` and refresh the evaluation summary."""
    summary = summarise(results)
    write_json(
        results_dir / EXTRACTION_RESULTS,
        {
            "run_at": dt.datetime.now().isoformat(timespec="seconds"),
            "model": llm,
            "dataset": dataset,
            "summary": summary,
            "cases": case_details(results),
        },
    )
    write_summary(results_dir)
    return summary
