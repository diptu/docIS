from typing import Any

import pytest

from app.extraction.normalize import invoice_number_key, normalize_invoice, vendor_key
from app.extraction.schema import InvoiceData, InvoiceLineItem, Party
from app.extraction.validation import validate_amounts
from tests.support.fakes import sample_invoice


def line(**values: Any) -> InvoiceLineItem:
    fields: dict[str, Any] = {
        "description": "Item",
        "product_code": None,
        "quantity": 1,
        "unit": None,
        "unit_price": None,
        "tax_rate": None,
        "amount": None,
    }
    return InvoiceLineItem(**{**fields, **values})


def party(**values: str | None) -> Party:
    fields = {
        "name": None,
        "address": None,
        "tax_id": None,
        "email": None,
        "phone": None,
    }
    return Party(**{**fields, **values})


def test_blank_strings_become_null_and_currency_is_upper_case() -> None:
    raw = sample_invoice(
        invoice_number="  INV-1 ",
        currency=" eur ",
        payment_terms="   ",
        vendor=party(name="ACME", address="", email=" a@b.c "),
        uncertain_fields=["total_amount", "total_amount", "invoice_date"],
    )

    data = normalize_invoice(raw)

    assert data.invoice_number == "INV-1"
    assert data.currency == "EUR"
    assert data.payment_terms is None
    assert (data.vendor.address, data.vendor.email) == (None, "a@b.c")
    assert data.uncertain_fields == ["total_amount", "invoice_date"]


def test_blank_line_item_text_becomes_null() -> None:
    raw = sample_invoice()
    raw.line_items[0] = raw.line_items[0].model_copy(
        update={"unit": " ", "product_code": ""}
    )

    item = normalize_invoice(raw).line_items[0]

    assert (item.unit, item.product_code) == (None, None)


@pytest.mark.parametrize(
    ("number", "key"),
    [
        ("INV-2026/001", "inv2026001"),
        ("inv 2026 001", "inv2026001"),
        ("INV2026001", "inv2026001"),
        ("---", None),
        (None, None),
    ],
)
def test_invoice_number_key(number: str | None, key: str | None) -> None:
    assert invoice_number_key(number) == key


@pytest.mark.parametrize(
    ("vendor", "key"),
    [
        (party(name="ACME GmbH", tax_id="DE 123 456 789"), "tax:de123456789"),
        (party(name="ACME GmbH"), "name:acme"),
        (party(name="Acme"), "name:acme"),
        (party(name="ACME Ltd."), "name:acme"),
        (party(name="Müller & Söhne GmbH"), "name:mllershne"),
        (party(name="GmbH"), None),
        (party(tax_id="--", name="Globex Inc"), "name:globex"),
        (party(), None),
    ],
)
def test_vendor_key(vendor: Party, key: str | None) -> None:
    assert vendor_key(vendor) == key


# ---- document type and amount signs -------------------------------------


def printed_negative(data: InvoiceData) -> InvoiceData:
    """A credit note as many documents print it: every amount negative."""
    return data.model_copy(
        update={
            "subtotal": -data.subtotal if data.subtotal else None,
            "tax_amount": -data.tax_amount if data.tax_amount else None,
            "total_amount": -data.total_amount if data.total_amount else None,
            "line_items": [
                line.model_copy(
                    update={
                        "amount": -line.amount if line.amount else None,
                        "unit_price": -line.unit_price if line.unit_price else None,
                    }
                )
                for line in data.line_items
            ],
        }
    )


def test_credit_note_amounts_become_positive_magnitudes() -> None:
    credit = sample_invoice(document_type="credit_note")

    normalised = normalize_invoice(printed_negative(credit))

    assert normalised.total_amount == credit.total_amount > 0
    assert normalised.subtotal == credit.subtotal
    assert normalised.tax_amount == credit.tax_amount
    assert [
        (li.quantity, li.unit_price, li.amount) for li in normalised.line_items
    ] == [(li.quantity, li.unit_price, li.amount) for li in credit.line_items]
    assert validate_amounts(normalised) == validate_amounts(credit)


def test_positive_credit_notes_are_left_alone() -> None:
    credit = sample_invoice(document_type="credit_note")

    assert normalize_invoice(credit).total_amount == credit.total_amount


def test_a_fee_deducted_on_a_credit_note_runs_against_it() -> None:
    """Printed: returned goods -500.00, restocking fee 50.00, total -450.00."""
    credit = sample_invoice(
        document_type="credit_note",
        line_items=[
            line(
                description="Returned goods", quantity=5, unit_price=-100, amount=-500
            ),
            line(description="Restocking fee", quantity=1, unit_price=50, amount=50),
        ],
        subtotal=-450.0,
        tax_amount=None,
        total_amount=-450.0,
        amount_due=None,
    )

    normalised = normalize_invoice(credit)

    assert [
        (li.quantity, li.unit_price, li.amount) for li in normalised.line_items
    ] == [
        (5, 100, 500),
        (1, -50, -50),
    ]
    assert normalised.total_amount == 450.0


def test_negative_quantities_are_turned_positive_instead_of_prices() -> None:
    credit = sample_invoice(
        document_type="credit_note",
        line_items=[line(quantity=-2, unit_price=40, amount=-80)],
        subtotal=-80.0,
        tax_amount=None,
        total_amount=-80.0,
        amount_due=None,
    )

    (item,) = normalize_invoice(credit).line_items

    assert (item.quantity, item.unit_price, item.amount) == (2, 40, 80)


def test_the_lines_decide_when_no_total_is_given() -> None:
    credit = sample_invoice(
        document_type="credit_note",
        line_items=[line(quantity=None, unit_price=None, amount=-80)],
        subtotal=None,
        tax_amount=None,
        total_amount=None,
        amount_due=None,
    )

    assert normalize_invoice(credit).line_items[0].amount == 80


def test_discount_lines_on_invoices_stay_negative() -> None:
    invoice = sample_invoice(
        line_items=[
            line(quantity=1, unit_price=1000, amount=1000),
            line(description="Discount", quantity=None, unit_price=None, amount=-100),
        ],
        subtotal=900.0,
        tax_amount=None,
        total_amount=900.0,
        amount_due=None,
    )

    assert [li.amount for li in normalize_invoice(invoice).line_items] == [1000, -100]


def test_invoices_are_never_sign_flipped() -> None:
    odd = printed_negative(sample_invoice())

    assert normalize_invoice(odd).total_amount == odd.total_amount


@pytest.mark.parametrize(
    ("document_type", "claimed", "expected"),
    [
        ("credit_note", False, True),  # a credit note is a billing document
        ("invoice", False, True),
        ("other", True, False),
    ],
)
def test_is_invoice_follows_the_document_type(
    document_type: str, claimed: bool, expected: bool
) -> None:
    data = sample_invoice(document_type=document_type, is_invoice=claimed)

    assert normalize_invoice(data).is_invoice is expected


def test_a_line_printed_with_negative_quantity_and_price_ends_up_positive() -> None:
    credit = sample_invoice(
        document_type="credit_note",
        line_items=[line(quantity=-2, unit_price=-40, amount=-80)],
        subtotal=-80.0,
        tax_amount=None,
        total_amount=-80.0,
        amount_due=None,
    )

    (item,) = normalize_invoice(credit).line_items

    assert (item.quantity, item.unit_price, item.amount) == (2, 40, 80)


def test_unsigned_unit_prices_stay_positive() -> None:
    """Printed: 2 x 40.00 = -80.00 (only the line amount carries the sign)."""
    credit = sample_invoice(
        document_type="credit_note",
        line_items=[line(quantity=2, unit_price=40, amount=-80)],
        subtotal=-80.0,
        tax_amount=None,
        total_amount=-80.0,
        amount_due=None,
    )

    (item,) = normalize_invoice(credit).line_items

    assert (item.quantity, item.unit_price, item.amount) == (2, 40, 80)


def test_line_items_without_any_value_are_dropped() -> None:
    blank = line(description="  ", quantity=None, unit_price=None, amount=None)
    kept = line(description=None, quantity=None, unit_price=None, amount=12.5)

    normalised = normalize_invoice(sample_invoice(line_items=[blank, kept]))

    assert normalised.line_items == [kept]
