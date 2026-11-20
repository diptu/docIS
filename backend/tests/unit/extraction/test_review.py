from app.extraction.review import (
    Duplicate,
    DuplicateMatch,
    ReasonCode,
    build_validation_errors,
)
from app.extraction.validation import validate_amounts
from app.models import Severity
from tests.support.fakes import sample_invoice


def errors(**kwargs: object) -> list[tuple[str, str]]:
    data = kwargs.pop("data", sample_invoice())
    specs = build_validation_errors(
        data,  # type: ignore[arg-type]
        kwargs.pop("checks", validate_amounts(data)),  # type: ignore[arg-type]
        kwargs.pop("duplicates", []),  # type: ignore[arg-type]
        kwargs.pop("ocr", []),  # type: ignore[arg-type]
        80.0,
    )
    return [(spec.code, spec.severity.value) for spec in specs]


def test_clean_invoice_has_no_errors() -> None:
    assert errors() == []


def test_not_an_invoice_is_a_warning() -> None:
    assert errors(data=sample_invoice(is_invoice=False)) == [("not_invoice", "warning")]


def test_every_problem_becomes_an_error_with_a_severity() -> None:
    data = sample_invoice(
        invoice_number=None,
        total_amount=1500.0,  # subtotal + tax no longer adds up
        uncertain_fields=["invoice_date"],
    )

    assert errors(
        data=data,
        duplicates=[
            Duplicate("a", DuplicateMatch.SAME_FILE),
            Duplicate("b", DuplicateMatch.SAME_INVOICE_NUMBER),
            Duplicate("c", DuplicateMatch.POSSIBLE),
        ],
        ocr=[(1, 95.0), (2, 61.5)],
    ) == [
        ("missing_fields", "error"),
        ("total", "error"),  # each failed check is its own error
        ("duplicate", "error"),
        ("possible_duplicate", "warning"),
        ("low_ocr_confidence", "warning"),
        ("model_uncertain", "warning"),
    ]


def test_details_carry_what_a_reviewer_needs() -> None:
    data = sample_invoice(invoice_number=None, total_amount=1500.0)
    specs = build_validation_errors(
        data,
        validate_amounts(data),
        [
            Duplicate("a", DuplicateMatch.SAME_FILE),
            Duplicate("b", DuplicateMatch.SAME_INVOICE_NUMBER),
        ],
        [(3, 40.0)],
        80.0,
    )
    by_code = {spec.code: spec for spec in specs}

    assert by_code[ReasonCode.MISSING_FIELDS].details == {"fields": ["invoice_number"]}
    assert by_code["total"].details == {"expected": "1487.5", "actual": "1500.0"}
    assert by_code["total"].severity == Severity.ERROR
    # Both exact match kinds are merged into one duplicate error.
    assert by_code[ReasonCode.DUPLICATE].details == {"document_ids": ["a", "b"]}
    assert by_code[ReasonCode.LOW_OCR_CONFIDENCE].details == {"pages": [3]}
    assert by_code[ReasonCode.MISSING_FIELDS].message == (
        "Key fields were not found: invoice_number."
    )


def test_missing_vendor_name_is_a_missing_key_field() -> None:
    vendor = sample_invoice().vendor.model_copy(update={"name": None})
    [spec] = build_validation_errors(sample_invoice(vendor=vendor), [], [], [], 80.0)

    assert spec.details == {"fields": ["vendor.name"]}
