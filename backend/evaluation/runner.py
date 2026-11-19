"""
Running labelled cases through the real extraction pipeline.
"""

import json
import random
import time
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
from evaluation.scoring import CaseScore, score_invoice


@dataclass(frozen=True)
class Case:
    name: str
    path: Path
    format: str
    locale: str
    expected: InvoiceData


def load_dataset(directory: Path) -> list[Case]:
    """Cases are ``<name>.<ext>`` documents next to ``<name>.json`` labels
    (``{"format", "locale", "expected": InvoiceData}``). Real labelled
    invoices can be added the same way."""
    cases = []
    for label in sorted(directory.glob("*.json")):
        documents = [
            p for p in directory.glob(f"{label.stem}.*") if p.suffix != ".json"
        ]
        if len(documents) != 1:
            raise ValueError(
                f"{label.name}: expected exactly one document named {label.stem}.*"
            )
        data = json.loads(label.read_text())
        cases.append(
            Case(
                name=label.stem,
                path=documents[0],
                format=data.get("format", documents[0].suffix.lstrip(".")),
                locale=data.get("locale", "unknown"),
                expected=InvoiceData.model_validate(data["expected"]),
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
        score=score_invoice(case.expected, predicted),
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
