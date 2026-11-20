import pytest

from app.extraction.normalize import invoice_number_key, normalize_invoice, vendor_key
from app.extraction.schema import Party
from tests.support.fakes import sample_invoice


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
