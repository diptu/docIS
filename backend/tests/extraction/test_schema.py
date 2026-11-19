"""The schema sent to Claude makes every field explicit and nullable."""

import json

from anthropic.lib._parse._transform import transform_schema

from app.extraction.schema import InvoiceData
from tests.fakes import sample_invoice


def _objects(schema: dict[str, object]) -> list[dict[str, object]]:
    found = [schema]
    for definition in schema.get("$defs", {}).values():  # type: ignore[attr-defined]
        found.append(definition)
    return found


def test_every_field_is_required_and_objects_are_closed() -> None:
    """Required + nullable means a missing value comes back as null, not absent."""
    schema = transform_schema(InvoiceData)

    for obj in _objects(schema):
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])  # type: ignore[arg-type]


def test_optional_values_are_nullable_and_dates_have_a_format() -> None:
    props = transform_schema(InvoiceData)["properties"]

    assert {"type": "null"} in props["invoice_number"]["anyOf"]
    assert {"type": "string", "format": "date"} in props["invoice_date"]["anyOf"]
    assert props["is_invoice"]["type"] == "boolean"
    assert props["line_items"]["type"] == "array"


def test_schema_uses_only_supported_keywords() -> None:
    """Numeric/string constraints are unsupported by structured outputs."""
    text = json.dumps(transform_schema(InvoiceData))

    for keyword in ("minimum", "maximum", "minLength", "maxLength", "pattern"):
        assert f'"{keyword}"' not in text


def test_round_trip_through_json() -> None:
    invoice = sample_invoice()

    assert InvoiceData.model_validate_json(invoice.model_dump_json()) == invoice


# ---- T2.3: results are schema-valid; anything else is rejected -------------

import pytest  # noqa: E402
from pydantic import ValidationError  # noqa: E402


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(unexpected="field"),  # extra key
        lambda d: d.pop("currency"),  # missing key (must be explicit null)
        lambda d: d.update(total_amount="lots"),  # wrong type
        lambda d: d.update(invoice_date="03/11/2026"),  # not ISO date
        lambda d: d["vendor"].update(iban="DE00"),  # extra nested key
        lambda d: d.update(uncertain_fields=["colour"]),  # not an allowed field
    ],
)
def test_off_schema_data_is_rejected(mutate: object) -> None:
    data = sample_invoice().model_dump(mode="json")
    mutate(data)  # type: ignore[operator]

    with pytest.raises(ValidationError):
        InvoiceData.model_validate(data)


def test_uncertain_fields_is_a_closed_list() -> None:
    items = transform_schema(InvoiceData)["properties"]["uncertain_fields"]["items"]

    assert "total_amount" in items["enum"] and "vendor" in items["enum"]
