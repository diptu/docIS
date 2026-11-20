"""InvoiceData <-> invoices / invoice_line_items rows."""

import uuid
from decimal import Decimal

from app.extraction.llm import LLMExtraction
from app.extraction.records import data_from_invoice, invoice_from_data
from tests.support.fakes import sample_invoice

RESULT = LLMExtraction(
    data=sample_invoice(),
    model="claude-opus-5-5",
    input_tokens=1200,
    output_tokens=300,
    cache_read_tokens=800,
)


def test_every_field_survives_the_round_trip() -> None:
    data = sample_invoice(uncertain_fields=["due_date"], amount_due=100.0)

    invoice = invoice_from_data(uuid.uuid4(), data, RESULT)

    assert data_from_invoice(invoice) == data


def test_columns_hold_exact_decimals_and_keys() -> None:
    invoice = invoice_from_data(uuid.uuid4(), sample_invoice(), RESULT)

    assert (invoice.subtotal, invoice.tax, invoice.total_amount) == (
        Decimal("1250.0"),
        Decimal("237.5"),
        Decimal("1487.5"),
    )
    assert (invoice.customer_name, invoice.vendor_tax_id) == (
        "Globex Ltd",
        "DE123456789",
    )
    assert (invoice.invoice_number_key, invoice.vendor_key) == (
        "inv2026001",
        "tax:de123456789",
    )
    assert (invoice.model, invoice.input_tokens, invoice.cache_read_tokens) == (
        "claude-opus-5-5",
        1200,
        800,
    )
    [line] = invoice.line_items
    assert (line.line_number, line.quantity, line.unit_price, line.amount) == (
        1,
        Decimal("10"),
        Decimal("125.0"),
        Decimal("1250.0"),
    )


def test_line_items_are_numbered_in_document_order() -> None:
    data = sample_invoice(line_items=[*sample_invoice().line_items] * 3)

    invoice = invoice_from_data(uuid.uuid4(), data, RESULT)

    assert [line.line_number for line in invoice.line_items] == [1, 2, 3]


def test_long_text_is_cut_to_column_size() -> None:
    data = sample_invoice(invoice_number="N" * 150, currency="EURO-DOLLARS")

    invoice = invoice_from_data(uuid.uuid4(), data, RESULT)

    assert invoice.invoice_number is not None and len(invoice.invoice_number) == 100
    assert invoice.currency == "EURO-DOLLA"
