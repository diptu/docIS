import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.extraction.document_text import EmptyDocumentError
from app.extraction.records import data_from_invoice
from app.extraction.service import InvoiceExtractor
from app.models import Document, DocumentPage, DocumentStatus, Invoice
from app.processing.processor import DocumentNotProcessableError
from app.repositories.document import DocumentRepository
from tests.fakes import FakeInvoiceLLM, sample_invoice

Factory = async_sessionmaker[AsyncSession]


async def add_document(
    factory: Factory, text: str = "Invoice INV-2026-001"
) -> uuid.UUID:
    document_id = uuid.uuid4()
    async with factory() as session:
        session.add(
            Document(
                id=document_id,
                file_name="inv.pdf",
                file_type="application/pdf",
                storage_path="x.pdf",
                status=DocumentStatus.PROCESSED,
            )
        )
        await session.flush()
        session.add(
            DocumentPage(
                document_id=document_id,
                page_number=1,
                text=text,
                char_count=len(text),
                extraction_method="text_layer",
                needs_ocr=False,
            )
        )
        await session.commit()
    return document_id


async def invoice_of(factory: Factory, document_id: uuid.UUID) -> Invoice:
    async with factory() as session:
        invoice = await DocumentRepository(session).get_invoice(document_id)
    assert invoice is not None
    return invoice


async def errors_of(
    factory: Factory, document_id: uuid.UUID
) -> list[tuple[str, str, dict[str, Any]]]:
    async with factory() as session:
        errors = await DocumentRepository(session).list_validation_errors(document_id)
    return [(e.code, e.severity, e.details) for e in errors]


async def extract(factory: Factory, document_id: uuid.UUID, **overrides: Any) -> None:
    llm = FakeInvoiceLLM(sample_invoice(**overrides))
    await InvoiceExtractor(factory, llm).extract(document_id)


async def set_sha(factory: Factory, document_id: uuid.UUID, sha: str) -> None:
    async with factory() as session, session.begin():
        await session.execute(
            update(Document).where(Document.id == document_id).values(sha256=sha)
        )


@pytest.mark.asyncio
async def test_extract_stores_invoice_and_line_items(session_factory: Factory) -> None:
    document_id = await add_document(session_factory)
    llm = FakeInvoiceLLM()
    steps: list[tuple[str, int, int | None]] = []

    await InvoiceExtractor(session_factory, llm).extract(
        document_id, lambda *step: steps.append(step)
    )

    assert llm.texts == ["--- Page 1 ---\nInvoice INV-2026-001"]
    invoice = await invoice_of(session_factory, document_id)
    assert (invoice.invoice_number, invoice.customer_name, invoice.currency) == (
        "INV-2026-001",
        "Globex Ltd",
        "EUR",
    )
    assert (invoice.tax, invoice.total_amount) == (Decimal("237.5"), Decimal("1487.5"))
    assert [
        (line.line_number, line.quantity, line.amount) for line in invoice.line_items
    ] == [(1, Decimal("10"), Decimal("1250.0"))]
    assert data_from_invoice(invoice) == sample_invoice()
    assert (invoice.model, invoice.input_tokens, invoice.cache_read_tokens) == (
        "claude-opus-5-5",
        1200,
        800,
    )
    assert await errors_of(session_factory, document_id) == []
    assert steps == [
        ("extracting_invoice", 0, 1),
        ("extracting_invoice", 1, 1),
        ("saving", 0, 1),
        ("saving", 1, 1),
    ]


@pytest.mark.asyncio
async def test_re_extraction_replaces_invoice_lines_and_errors(
    session_factory: Factory,
) -> None:
    document_id = await add_document(session_factory)
    await extract(session_factory, document_id, total_amount=1500.0)  # one error
    assert [code for code, *_ in await errors_of(session_factory, document_id)] == [
        "total"
    ]

    await extract(session_factory, document_id, line_items=[], total_amount=1487.5)

    invoice = await invoice_of(session_factory, document_id)
    assert invoice.line_items == []
    assert await errors_of(session_factory, document_id) == []
    async with session_factory() as session:
        from sqlalchemy import func, select

        from app.models import InvoiceLineItem

        assert await session.scalar(select(func.count()).select_from(Invoice)) == 1
        assert (
            await session.scalar(select(func.count()).select_from(InvoiceLineItem)) == 0
        )


@pytest.mark.asyncio
async def test_model_output_is_normalised_before_saving(
    session_factory: Factory,
) -> None:
    document_id = await add_document(session_factory)

    await extract(session_factory, document_id, currency="eur", payment_terms="  ")

    invoice = await invoice_of(session_factory, document_id)
    assert (invoice.currency, invoice.payment_terms) == ("EUR", None)


@pytest.mark.asyncio
async def test_failed_checks_become_validation_errors(session_factory: Factory) -> None:
    document_id = await add_document(session_factory)

    await extract(session_factory, document_id, total_amount=1500.0)

    assert await errors_of(session_factory, document_id) == [
        ("total", "error", {"expected": "1487.5", "actual": "1500.0"})
    ]


@pytest.mark.asyncio
async def test_missing_or_empty_documents_are_rejected(
    session_factory: Factory,
) -> None:
    extractor = InvoiceExtractor(session_factory, FakeInvoiceLLM())

    with pytest.raises(DocumentNotProcessableError):
        await extractor.extract(uuid.uuid4())
    with pytest.raises(EmptyDocumentError):
        await extractor.extract(await add_document(session_factory, text="  "))


# ---- duplicates --------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_invoice_uploaded_twice_flags_both(session_factory: Factory) -> None:
    first = await add_document(session_factory)
    second = await add_document(session_factory)

    await extract(session_factory, first)
    await extract(
        session_factory, second, invoice_number="inv 2026 001"
    )  # other format

    assert await errors_of(session_factory, second) == [
        ("duplicate", "error", {"document_ids": [str(first)]})
    ]
    assert await errors_of(session_factory, first) == [
        ("duplicate", "error", {"document_ids": [str(second)]})
    ]


@pytest.mark.asyncio
async def test_third_copy_extends_the_existing_duplicate_error(
    session_factory: Factory,
) -> None:
    first, second, third = [await add_document(session_factory) for _ in range(3)]
    for document_id in (first, second, third):
        await extract(session_factory, document_id)

    [(code, _, details)] = await errors_of(session_factory, first)
    assert code == "duplicate"
    assert details["document_ids"] == [str(second), str(third)]


@pytest.mark.asyncio
async def test_same_file_is_a_duplicate_even_before_the_copy_is_extracted(
    session_factory: Factory,
) -> None:
    first = await add_document(session_factory)
    second = await add_document(session_factory)
    for document_id in (first, second):
        await set_sha(session_factory, document_id, "same")

    await extract(session_factory, first)

    assert await errors_of(session_factory, first) == [
        ("duplicate", "error", {"document_ids": [str(second)]})
    ]


@pytest.mark.asyncio
async def test_same_vendor_date_and_total_is_a_possible_duplicate(
    session_factory: Factory,
) -> None:
    first = await add_document(session_factory)
    second = await add_document(session_factory)

    await extract(session_factory, first)
    await extract(session_factory, second, invoice_number="INV-2026-777")

    assert await errors_of(session_factory, second) == [
        ("possible_duplicate", "warning", {"document_ids": [str(first)]})
    ]
    assert await errors_of(session_factory, first) == [
        ("possible_duplicate", "warning", {"document_ids": [str(second)]})
    ]


@pytest.mark.asyncio
async def test_different_vendor_is_not_a_duplicate(session_factory: Factory) -> None:
    first = await add_document(session_factory)
    second = await add_document(session_factory)
    other = sample_invoice().vendor.model_copy(
        update={"tax_id": "FR999", "name": "Other"}
    )

    await extract(session_factory, first)
    await extract(session_factory, second, vendor=other)

    assert await errors_of(session_factory, first) == []
    assert await errors_of(session_factory, second) == []


@pytest.mark.asyncio
async def test_non_invoice_does_not_flag_other_documents(
    session_factory: Factory,
) -> None:
    first = await add_document(session_factory)
    second = await add_document(session_factory)

    await extract(session_factory, first)
    await extract(session_factory, second, is_invoice=False)

    assert await errors_of(session_factory, first) == []
    assert await errors_of(session_factory, second) == [("not_invoice", "warning", {})]


@pytest.mark.asyncio
async def test_resolved_duplicate_gets_a_fresh_error_for_a_new_copy(
    session_factory: Factory,
) -> None:
    first, second, third = [await add_document(session_factory) for _ in range(3)]
    await extract(session_factory, first)
    await extract(session_factory, second)
    async with session_factory() as session:
        repository = DocumentRepository(session)
        [error] = await repository.list_validation_errors(first)
        await repository.set_resolved(error, True)

    await extract(session_factory, third)

    errors = await errors_of(session_factory, first)
    assert [(code, details["document_ids"]) for code, _, details in errors] == [
        ("duplicate", [str(third)]),  # new, unresolved
        ("duplicate", [str(second)]),  # resolved earlier, kept
    ]


@pytest.mark.asyncio
async def test_low_ocr_confidence_pages_are_flagged(session_factory: Factory) -> None:
    document_id = await add_document(session_factory)
    async with session_factory() as session, session.begin():
        await session.execute(
            update(DocumentPage)
            .where(DocumentPage.document_id == document_id)
            .values(ocr_confidence=55.0, extraction_method="ocr")
        )

    await extract(session_factory, document_id)

    assert await errors_of(session_factory, document_id) == [
        ("low_ocr_confidence", "warning", {"pages": [1]})
    ]


@pytest.mark.asyncio
async def test_resolving_and_reopening_an_error(session_factory: Factory) -> None:
    document_id = await add_document(session_factory)
    await extract(session_factory, document_id, total_amount=1500.0)

    async with session_factory() as session:
        repository = DocumentRepository(session)
        [error] = await repository.list_validation_errors(document_id)
        resolved = await repository.set_resolved(error, True)
        assert (resolved.resolved, resolved.resolved_at is not None) == (True, True)
        reopened = await repository.set_resolved(error, False)
        assert (reopened.resolved, reopened.resolved_at) == (False, None)
