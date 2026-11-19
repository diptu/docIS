import uuid
from decimal import Decimal

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import NotFoundError
from app.models import DocumentPage
from app.repositories.document import DocumentRepository
from app.services.invoice_review import InvoiceCorrectionService, diff_invoices
from tests.extraction.test_service import add_document, errors_of, extract, invoice_of
from tests.fakes import sample_invoice

Factory = async_sessionmaker[AsyncSession]
TOLERANCE = Decimal("0.01")


async def correct(factory: Factory, document_id: uuid.UUID, **changes: object) -> dict:
    async with factory() as session:
        service = InvoiceCorrectionService(DocumentRepository(session), TOLERANCE)
        return await service.correct(document_id, sample_invoice(**changes))


def test_diff_reports_changed_fields_only() -> None:
    old = sample_invoice()
    vendor = old.vendor.model_copy(update={"tax_id": "DE999"})
    new = sample_invoice(total_amount=1500.0, vendor=vendor, line_items=[])

    assert diff_invoices(old, new) == {
        "vendor.tax_id": {"old": "DE123456789", "new": "DE999"},
        "line_items": {
            "old": old.model_dump(mode="json")["line_items"],
            "new": [],
        },
        "total_amount": {"old": 1487.5, "new": 1500.0},
    }
    assert diff_invoices(old, sample_invoice()) == {}


@pytest.mark.asyncio
async def test_correction_fixes_values_and_clears_resolved_problems(
    session_factory: Factory,
) -> None:
    document_id = await add_document(session_factory)
    await extract(session_factory, document_id, total_amount=1500.0)  # misread
    assert [code for code, *_ in await errors_of(session_factory, document_id)] == [
        "total"
    ]

    changes = await correct(session_factory, document_id, total_amount=1487.5)

    assert changes == {"total_amount": {"old": 1500.0, "new": 1487.5}}
    invoice = await invoice_of(session_factory, document_id)
    assert invoice.total_amount == Decimal("1487.5")
    assert invoice.corrected_at is not None
    assert invoice.model == "claude-opus-5-5"  # extraction metadata kept
    assert await errors_of(session_factory, document_id) == []
    async with session_factory() as session:
        [record] = await DocumentRepository(session).list_corrections(document_id)
    assert record.changes == changes


@pytest.mark.asyncio
async def test_a_wrong_correction_is_flagged_too(session_factory: Factory) -> None:
    document_id = await add_document(session_factory)
    await extract(session_factory, document_id)

    await correct(session_factory, document_id, tax_amount=200.0, invoice_number=None)

    assert [code for code, *_ in await errors_of(session_factory, document_id)] == [
        "missing_fields",
        "tax_amount",
        "total",
    ]


@pytest.mark.asyncio
async def test_correction_drops_model_reading_warnings(
    session_factory: Factory,
) -> None:
    document_id = await add_document(session_factory)
    async with session_factory() as session, session.begin():
        await session.execute(
            update(DocumentPage)
            .where(DocumentPage.document_id == document_id)
            .values(ocr_confidence=40.0)
        )
    await extract(session_factory, document_id, uncertain_fields=["invoice_date"])
    assert {code for code, *_ in await errors_of(session_factory, document_id)} == {
        "low_ocr_confidence",
        "model_uncertain",
    }

    await correct(session_factory, document_id, payment_terms="Net 14")

    assert await errors_of(session_factory, document_id) == []
    assert (await invoice_of(session_factory, document_id)).uncertain_fields == []


@pytest.mark.asyncio
async def test_unchanged_values_are_a_no_op(session_factory: Factory) -> None:
    document_id = await add_document(session_factory)
    await extract(session_factory, document_id)
    before = await invoice_of(session_factory, document_id)

    assert await correct(session_factory, document_id) == {}

    after = await invoice_of(session_factory, document_id)
    assert (after.id, after.corrected_at) == (before.id, None)
    async with session_factory() as session:
        assert await DocumentRepository(session).list_corrections(document_id) == []


@pytest.mark.asyncio
async def test_correcting_into_a_duplicate_flags_both(session_factory: Factory) -> None:
    first = await add_document(session_factory)
    second = await add_document(session_factory)
    await extract(session_factory, first)
    await extract(
        session_factory,
        second,
        invoice_number="TYPO-1",
        total_amount=1.0,
        subtotal=1.0,
        tax_amount=0.0,
        line_items=[],
    )

    await correct(session_factory, second)  # the corrected values match `first`

    assert [code for code, *_ in await errors_of(session_factory, second)] == [
        "duplicate"
    ]
    assert [code for code, *_ in await errors_of(session_factory, first)] == [
        "duplicate"
    ]


@pytest.mark.asyncio
async def test_correction_history_is_kept_newest_first(
    session_factory: Factory,
) -> None:
    document_id = await add_document(session_factory)
    await extract(session_factory, document_id)

    await correct(session_factory, document_id, payment_terms="Net 14")
    await correct(session_factory, document_id, payment_terms="Net 7")

    async with session_factory() as session:
        history = await DocumentRepository(session).list_corrections(document_id)
    assert [c.changes["payment_terms"]["new"] for c in history] == ["Net 7", "Net 14"]


@pytest.mark.asyncio
async def test_correcting_missing_documents_or_invoices_fails(
    session_factory: Factory,
) -> None:
    with pytest.raises(NotFoundError, match="not found"):
        await correct(session_factory, uuid.uuid4())
    with pytest.raises(NotFoundError, match="no extracted invoice"):
        await correct(session_factory, await add_document(session_factory))
