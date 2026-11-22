"""Extraction in parts and merging (app/extraction/chunked.py)."""

import asyncio
from typing import Any

import pytest

from app.extraction.chunked import ChunkingConfig, extract_document, merge_parts
from app.extraction.document_text import DocumentTooLargeError, document_sections
from app.extraction.llm import LLMExtraction, OutputLimitError, Part, user_message
from app.extraction.schema import InvoiceData, InvoiceLineItem, Party
from app.models import DocumentPage
from tests.support.fakes import sample_invoice

SMALL = ChunkingConfig(chunk_chars=200, max_parts=50, concurrency=2)


def line(description: str, amount: float) -> InvoiceLineItem:
    return InvoiceLineItem(
        description=description,
        product_code=None,
        quantity=None,
        unit=None,
        unit_price=None,
        tax_rate=None,
        amount=amount,
    )


def empty_party() -> Party:
    return Party(name=None, address=None, tax_id=None, email=None, phone=None)


def part_data(**values: Any) -> InvoiceData:
    """What one part of a long invoice typically yields: only what it shows."""
    fields: dict[str, Any] = {
        "document_type": "invoice",
        "is_invoice": True,
        "invoice_number": None,
        "invoice_date": None,
        "due_date": None,
        "currency": None,
        "purchase_order_number": None,
        "payment_terms": None,
        "vendor": empty_party(),
        "customer": empty_party(),
        "line_items": [],
        "subtotal": None,
        "tax_amount": None,
        "total_amount": None,
        "amount_due": None,
        "uncertain_fields": [],
    }
    return InvoiceData(**{**fields, **values})


def pages(count: int) -> list[DocumentPage]:
    return [
        DocumentPage(page_number=n, text=f"Item {n} " + "." * 120)
        for n in range(1, count + 1)
    ]


class ScriptedLLM:
    """Answers each part from ``answers`` (by part number) and records calls."""

    def __init__(self, answers: dict[int, InvoiceData] | None = None) -> None:
        self.answers = answers or {}
        self.calls: list[tuple[str, Part | None]] = []
        self.active = 0
        self.max_active = 0

    async def extract(
        self, document_text: str, part: Part | None = None
    ) -> LLMExtraction:
        self.calls.append((document_text, part))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        number = part.number if part else 1
        data = self.answers.get(
            number, part_data(line_items=[line(f"part {number}", 1)])
        )
        return LLMExtraction(data, "claude-opus-5-5", 100, 10, 50)


# ------------------------------------------------------------- one request


async def test_a_short_document_is_one_request_without_a_part_number() -> None:
    llm = ScriptedLLM({1: sample_invoice()})
    progress: list[tuple[int, int]] = []

    result = await extract_document(
        llm,
        document_sections(pages(1), []),
        SMALL,
        lambda d, t: progress.append((d, t)),
    )

    assert [part for _, part in llm.calls] == [None]
    assert result.data == sample_invoice()
    assert progress == [(0, 1), (1, 1)]


def test_the_part_number_is_given_outside_the_document_tags() -> None:
    assert user_message("TEXT", None) == "<document>\nTEXT\n</document>"
    assert user_message("TEXT", Part(2, 5)) == (
        "Part 2 of 5 of one document.\n<document>\nTEXT\n</document>"
    )


# ---------------------------------------------------------- several parts


async def test_a_long_document_is_extracted_in_numbered_parts() -> None:
    llm = ScriptedLLM()
    progress: list[tuple[int, int]] = []

    result = await extract_document(
        llm,
        document_sections(pages(6), []),
        SMALL,
        lambda d, t: progress.append((d, t)),
    )

    total = len(llm.calls)
    assert total == 6  # one page per part at this size
    assert sorted(part.number for _, part in llm.calls if part) == list(range(1, 7))
    assert {part.total for _, part in llm.calls if part} == {6}
    assert all(len(text) <= SMALL.chunk_chars for text, _ in llm.calls)
    # Lines come back in document order, whatever order the parts finished in.
    assert [item.description for item in result.data.line_items] == [
        f"part {n}" for n in range(1, 7)
    ]
    assert (result.input_tokens, result.output_tokens, result.cache_read_tokens) == (
        600,
        60,
        300,
    )
    assert progress[0] == (0, 6) and progress[-1] == (6, 6)


async def test_parts_run_concurrently_up_to_the_limit() -> None:
    llm = ScriptedLLM()

    await extract_document(llm, document_sections(pages(8), []), SMALL)

    assert llm.max_active == SMALL.concurrency


async def test_too_many_parts_fails_before_any_request() -> None:
    llm = ScriptedLLM()
    config = ChunkingConfig(chunk_chars=200, max_parts=3)

    with pytest.raises(DocumentTooLargeError, match="LLM_MAX_PARTS"):
        await extract_document(llm, document_sections(pages(6), []), config)

    assert llm.calls == []


async def test_a_failing_part_cancels_the_rest_and_keeps_its_error() -> None:
    class Failing(ScriptedLLM):
        async def extract(
            self, document_text: str, part: Part | None = None
        ) -> LLMExtraction:
            if part and part.number == 2:
                raise ConnectionError("API down")
            return await super().extract(document_text, part)

    llm = Failing()

    with pytest.raises(ConnectionError, match="API down"):
        await extract_document(llm, document_sections(pages(8), []), SMALL)

    assert len(llm.calls) < 8  # the rest were cancelled, not all sent


# ------------------------------------------------------- output overflow


async def test_a_part_that_overflows_is_halved_and_retried() -> None:
    class Overflowing(ScriptedLLM):
        async def extract(
            self, document_text: str, part: Part | None = None
        ) -> LLMExtraction:
            if len(document_text) > 300:
                self.calls.append((document_text, part))
                raise OutputLimitError("cut off")
            return await super().extract(document_text, part)

    text = "\n".join(f"row {n} " + "-" * 40 for n in range(12))  # ~560 chars
    llm = Overflowing()

    result = await extract_document(
        llm,
        document_sections([DocumentPage(page_number=1, text=text)], []),
        ChunkingConfig(chunk_chars=10_000),
    )

    answered = [part for text_, part in llm.calls if len(text_) <= 300]
    assert answered == [Part(1, 2), Part(2, 2)]
    assert len(result.data.line_items) == 2


async def test_a_single_line_that_overflows_cannot_be_halved() -> None:
    class AlwaysOverflowing(ScriptedLLM):
        async def extract(
            self, document_text: str, part: Part | None = None
        ) -> LLMExtraction:
            raise OutputLimitError("cut off")

    with pytest.raises(OutputLimitError):
        await extract_document(
            AlwaysOverflowing(),
            document_sections([DocumentPage(page_number=1, text="x" * 50)], []),
        )


async def test_halving_stops_after_a_few_rounds() -> None:
    class AlwaysOverflowing(ScriptedLLM):
        async def extract(
            self, document_text: str, part: Part | None = None
        ) -> LLMExtraction:
            self.calls.append((document_text, part))
            raise OutputLimitError("cut off")

    llm = AlwaysOverflowing()
    text = "\n".join(f"row {n}" for n in range(200))

    with pytest.raises(OutputLimitError):
        await extract_document(
            llm, document_sections([DocumentPage(page_number=1, text=text)], [])
        )

    assert len(llm.calls) == 4  # the whole, then one half per level (3 levels)


# ----------------------------------------------------------------- merging


def test_headers_come_from_the_first_part_and_totals_from_the_last() -> None:
    acme = Party(name="ACME GmbH", address=None, tax_id="DE1", email=None, phone=None)
    merged = merge_parts(
        [
            part_data(
                invoice_number="INV-9",
                invoice_date=sample_invoice().invoice_date,
                currency="EUR",
                vendor=acme,
                line_items=[line("a", 100)],
                subtotal=100.0,  # a page subtotal carried forward
            ),
            part_data(line_items=[line("b", 50)]),
            part_data(
                invoice_number="INV-9",
                line_items=[line("c", 25)],
                subtotal=175.0,
                tax_amount=35.0,
                total_amount=210.0,
            ),
        ]
    )

    assert (merged.invoice_number, merged.currency, merged.vendor.name) == (
        "INV-9",
        "EUR",
        "ACME GmbH",
    )
    assert [item.description for item in merged.line_items] == ["a", "b", "c"]
    assert (merged.subtotal, merged.tax_amount, merged.total_amount) == (175, 35, 210)
    assert merged.uncertain_fields == []


def test_parts_that_disagree_on_a_header_flag_it_for_review() -> None:
    merged = merge_parts(
        [
            part_data(
                invoice_number="INV-1",
                vendor=Party(
                    name="ACME", address=None, tax_id=None, email=None, phone=None
                ),
            ),
            part_data(
                invoice_number="INV-2",
                vendor=Party(
                    name="Globex", address=None, tax_id=None, email=None, phone=None
                ),
            ),
        ]
    )

    assert merged.invoice_number == "INV-1"
    assert merged.uncertain_fields == ["invoice_number", "vendor"]


def test_the_same_value_written_differently_is_not_a_conflict() -> None:
    merged = merge_parts(
        [
            part_data(invoice_number="INV-2026/001", currency="eur"),
            part_data(invoice_number="inv 2026 001", currency="EUR"),
        ]
    )

    assert merged.uncertain_fields == []


def test_uncertain_fields_of_every_part_are_kept_once() -> None:
    merged = merge_parts(
        [
            part_data(uncertain_fields=["invoice_date"]),
            part_data(uncertain_fields=["line_items", "invoice_date"]),
        ]
    )

    assert merged.uncertain_fields == ["invoice_date", "line_items"]


def test_appendix_pages_that_are_not_billing_are_ignored() -> None:
    merged = merge_parts(
        [
            part_data(invoice_number="INV-1", line_items=[line("a", 10)]),
            part_data(document_type="other", is_invoice=False),
        ]
    )

    assert merged.document_type == "invoice"
    assert merged.invoice_number == "INV-1"


def test_a_document_with_no_billing_part_is_other() -> None:
    other = part_data(document_type="other", is_invoice=False)

    assert merge_parts([other, other]).document_type == "other"


def test_a_long_credit_note_ends_up_positive_throughout() -> None:
    """The title is on the first part; a continuation part reads like an
    invoice and reports its lines negative, as printed."""
    merged = merge_parts(
        [
            part_data(document_type="credit_note", line_items=[line("a", -100)]),
            part_data(document_type="invoice", line_items=[line("b", -50)]),
            part_data(
                document_type="invoice",
                line_items=[line("Restocking fee", 15)],
                subtotal=-135.0,
                total_amount=-135.0,
            ),
        ]
    )

    assert merged.document_type == "credit_note"
    assert [item.amount for item in merged.line_items] == [100, 50, -15]
    assert merged.total_amount == 135
