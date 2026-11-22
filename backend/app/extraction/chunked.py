"""
Invoice extraction for documents of any length.

A document that fits one request is extracted in one call. A longer one is
split into parts at page and sheet boundaries (``split_document``); the parts
are extracted concurrently, each told which part it is, and the results are
merged into one invoice:

* document type: from the first part that is a billing document (the title
  is normally on the first page);
* header fields and parties: the first value stated, in document order. If
  parts state different values, the field is listed as uncertain so a person
  checks it;
* line items: every part's lines, in document order;
* totals (subtotal, tax, total, amount due): the last value stated, since
  totals are printed at the end;
* uncertain fields: everything any part was unsure about.

Each part follows the amount sign convention on its own before merging (see
``apply_sign_convention``), so a credit note stays consistent even if some
parts reported its amounts as negative.

A part whose answer is cut off by the output-token limit (very dense line
items) is split in half and retried, a few times at most. Any other failure
cancels the remaining parts and propagates, so the job retries as a whole.
"""

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from app.extraction.document_text import (
    DocumentTooLargeError,
    Section,
    split_document,
)
from app.extraction.llm import InvoiceLLM, LLMExtraction, OutputLimitError, Part
from app.extraction.normalize import apply_sign_convention, invoice_number_key, name_key
from app.extraction.schema import InvoiceData, Party, UncertainField

HEADER_FIELDS: tuple[UncertainField, ...] = (
    "invoice_number",
    "invoice_date",
    "due_date",
    "currency",
    "purchase_order_number",
    "payment_terms",
)
TOTAL_FIELDS = ("subtotal", "tax_amount", "total_amount", "amount_due")
PARTY_FIELDS = ("name", "address", "tax_id", "email", "phone")
ROLES: tuple[Literal["vendor", "customer"], ...] = ("vendor", "customer")
# How often a part that overflows the output limit may be halved again.
MAX_SPLIT_DEPTH = 3

ProgressFn = Callable[[int, int], None]


@dataclass(frozen=True)
class ChunkingConfig:
    """``chunk_chars``: target size of one request's document text.
    ``max_parts``: more parts than this fails the job (a cost guard).
    ``concurrency``: parts extracted at the same time."""

    chunk_chars: int = 50_000
    max_parts: int = 120
    concurrency: int = 4


DEFAULT_CHUNKING = ChunkingConfig()


def _no_progress(_: int, __: int) -> None:
    """Default progress callback: report nothing."""


async def extract_document(
    llm: InvoiceLLM,
    sections: Sequence[Section],
    config: ChunkingConfig = DEFAULT_CHUNKING,
    progress: ProgressFn = _no_progress,
) -> LLMExtraction:
    """Extract one invoice from a document's sections, in parts if needed."""
    text = "\n\n".join(section.text() for section in sections)
    parts = [text] if len(text) <= config.chunk_chars else None
    if parts is None:
        parts = split_document(sections, config.chunk_chars)
        if len(parts) > config.max_parts:
            raise DocumentTooLargeError(
                f"The document needs {len(parts)} extraction requests of "
                f"{config.chunk_chars:,} characters; the limit is "
                f"{config.max_parts} (LLM_MAX_PARTS)."
            )

    total = len(parts)
    progress(0, total)
    if total == 1:
        results = await _extract_part(llm, parts[0], None)
        progress(1, 1)
        return combine(results)

    done = 0
    results_by_part: list[list[LLMExtraction]] = [[] for _ in parts]
    semaphore = asyncio.Semaphore(config.concurrency)

    async def run(index: int) -> None:
        nonlocal done
        async with semaphore:
            results_by_part[index] = await _extract_part(
                llm, parts[index], Part(index + 1, total)
            )
        done += 1
        progress(done, total)

    tasks = [asyncio.create_task(run(index)) for index in range(total)]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    return combine([result for part in results_by_part for result in part])


def _halves(text: str) -> tuple[str, str] | None:
    """Split text at the line boundary nearest its middle; None if it is a
    single line."""
    lines = text.split("\n")
    if len(lines) < 2:
        return None
    middle = len(text) // 2
    position, best, best_distance = 0, 1, len(text)
    for index, line in enumerate(lines[:-1], start=1):
        position += len(line) + 1
        if abs(position - middle) < best_distance:
            best, best_distance = index, abs(position - middle)
    return "\n".join(lines[:best]), "\n".join(lines[best:])


async def _extract_part(
    llm: InvoiceLLM, text: str, part: Part | None, depth: int = 0
) -> list[LLMExtraction]:
    """One part's extraction, or several if its output overflowed."""
    try:
        return [await llm.extract(text, part)]
    except OutputLimitError:
        halves = _halves(text)
        if halves is None or depth >= MAX_SPLIT_DEPTH:
            raise
    # Halves of a part keep its number; halves of a whole document become
    # its two parts.
    first_part, second_part = (part, part) if part else (Part(1, 2), Part(2, 2))
    first = await _extract_part(llm, halves[0], first_part, depth + 1)
    second = await _extract_part(llm, halves[1], second_part, depth + 1)
    return first + second


def combine(results: Sequence[LLMExtraction]) -> LLMExtraction:
    """One extraction from several: merged data, summed token usage."""
    if len(results) == 1:
        return results[0]
    return LLMExtraction(
        data=merge_parts([result.data for result in results]),
        model=results[0].model,
        input_tokens=sum(result.input_tokens for result in results),
        output_tokens=sum(result.output_tokens for result in results),
        cache_read_tokens=sum(result.cache_read_tokens for result in results),
    )


def _same(field: str, a: Any, b: Any) -> bool:
    if field == "invoice_number":
        return invoice_number_key(a) == invoice_number_key(b)
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().casefold() == b.strip().casefold()
    return bool(a == b)


def _first(values: Sequence[Any], field: str) -> tuple[Any, bool]:
    """The first stated value, and whether another part disagrees."""
    stated = [value for value in values if value is not None]
    if not stated:
        return None, False
    return stated[0], any(not _same(field, stated[0], other) for other in stated[1:])


def _merge_party(parties: Sequence[Party]) -> tuple[Party, bool]:
    merged: dict[str, Any] = {}
    for field in PARTY_FIELDS:
        merged[field], _ = _first([getattr(p, field) for p in parties], field)
    names = {name_key(p.name) for p in parties if p.name and name_key(p.name)}
    tax_ids = {p.tax_id.strip().casefold() for p in parties if p.tax_id}
    conflict = len(names) > 1 or len(tax_ids) > 1
    return Party(**merged), conflict


def merge_parts(parts: Sequence[InvoiceData]) -> InvoiceData:
    """Merge per-part extractions of one document (see the module docstring)."""
    billing = [part for part in parts if part.document_type != "other"]
    if not billing:
        return parts[0]
    document_type = billing[0].document_type
    signed = [
        apply_sign_convention(part.model_copy(update={"document_type": document_type}))
        for part in billing
    ]

    update: dict[str, Any] = {"document_type": document_type, "is_invoice": True}
    uncertain: list[UncertainField] = [
        field for part in signed for field in part.uncertain_fields
    ]
    for field in HEADER_FIELDS:
        update[field], conflict = _first([getattr(p, field) for p in signed], field)
        if conflict:
            uncertain.append(field)
    for role in ROLES:
        update[role], conflict = _merge_party([getattr(p, role) for p in signed])
        if conflict:
            uncertain.append(role)
    for total in TOTAL_FIELDS:
        stated = [getattr(p, total) for p in signed if getattr(p, total) is not None]
        update[total] = stated[-1] if stated else None
    update["line_items"] = [item for part in signed for item in part.line_items]
    update["uncertain_fields"] = list(dict.fromkeys(uncertain))
    return signed[0].model_copy(update=update)
