"""
Turning an extraction into validation errors for human review.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.extraction.schema import InvoiceData
from app.extraction.validation import Check, CheckStatus
from app.models.invoice import Severity


class ReasonCode(StrEnum):
    """Validation error codes besides the amount-check codes
    (``line_amounts``, ``lines_subtotal``, ``tax_amount``, ``total``,
    ``amount_due``), which are used as-is for failed checks."""

    NOT_INVOICE = "not_invoice"
    MISSING_FIELDS = "missing_fields"
    DUPLICATE = "duplicate"
    POSSIBLE_DUPLICATE = "possible_duplicate"
    LOW_OCR_CONFIDENCE = "low_ocr_confidence"
    MODEL_UNCERTAIN = "model_uncertain"


class DuplicateMatch(StrEnum):
    SAME_FILE = "same_file"  # byte-identical upload (SHA-256)
    SAME_INVOICE_NUMBER = "same_invoice_number"  # same vendor + number
    POSSIBLE = "possible"  # same vendor, date and total; number differs


@dataclass(frozen=True)
class Duplicate:
    document_id: str
    match: DuplicateMatch


@dataclass(frozen=True)
class ErrorSpec:
    """A validation error to store for a document."""

    code: str
    severity: Severity
    message: str
    details: dict[str, Any] = field(default_factory=dict)


# Fields an invoice must have to be booked.
KEY_FIELDS = ("invoice_number", "invoice_date", "currency", "total_amount")

POSSIBLE_DUPLICATE_MESSAGE = (
    "Another invoice from the same vendor has the same date and total but a "
    "different invoice number."
)


def duplicate_code(match: DuplicateMatch) -> ReasonCode:
    if match == DuplicateMatch.POSSIBLE:
        return ReasonCode.POSSIBLE_DUPLICATE
    return ReasonCode.DUPLICATE


def duplicate_severity(match: DuplicateMatch) -> Severity:
    return Severity.WARNING if match == DuplicateMatch.POSSIBLE else Severity.ERROR


def _missing_key_fields(data: InvoiceData) -> list[str]:
    missing = [name for name in KEY_FIELDS if getattr(data, name) is None]
    if data.vendor.name is None:
        missing.append("vendor.name")
    return missing


def build_validation_errors(
    data: InvoiceData,
    checks: Sequence[Check],
    duplicates: Sequence[Duplicate],
    ocr_confidences: Sequence[tuple[int, float]],
    min_ocr_confidence: float,
) -> list[ErrorSpec]:
    """Every problem a person should look at (empty = none).

    Severity ``error``: the data is wrong or unusable as-is (failed amount
    check, missing key fields, duplicate). ``warning``: worth checking but
    may well be right (possible duplicate, low OCR confidence, model
    uncertainty, not an invoice). ``ocr_confidences`` holds (page, score).
    """
    if not data.is_invoice:
        return [
            ErrorSpec(
                ReasonCode.NOT_INVOICE,
                Severity.WARNING,
                "The model classified this document as not an invoice.",
            )
        ]

    errors: list[ErrorSpec] = []
    if missing := _missing_key_fields(data):
        errors.append(
            ErrorSpec(
                ReasonCode.MISSING_FIELDS,
                Severity.ERROR,
                "Key fields were not found: " + ", ".join(missing) + ".",
                {"fields": missing},
            )
        )
    for check in checks:
        if check.status == CheckStatus.FAILED:
            errors.append(
                ErrorSpec(
                    check.code.value,
                    Severity.ERROR,
                    check.message,
                    {
                        "expected": _str(check.expected),
                        "actual": _str(check.actual),
                    },
                )
            )
    for match in DuplicateMatch:
        ids = [d.document_id for d in duplicates if d.match == match]
        if not ids:
            continue
        code = duplicate_code(match)
        existing = next((e for e in errors if e.code == code), None)
        if existing is not None:
            existing.details["document_ids"].extend(ids)
            continue
        message = (
            POSSIBLE_DUPLICATE_MESSAGE
            if code == ReasonCode.POSSIBLE_DUPLICATE
            else "This invoice was already uploaded."
        )
        errors.append(
            ErrorSpec(code, duplicate_severity(match), message, {"document_ids": ids})
        )
    low = [
        (page, score) for page, score in ocr_confidences if score < min_ocr_confidence
    ]
    if low:
        errors.append(
            ErrorSpec(
                ReasonCode.LOW_OCR_CONFIDENCE,
                Severity.WARNING,
                f"OCR confidence is below {min_ocr_confidence:g} on page(s) "
                + ", ".join(str(page) for page, _ in low)
                + "; values may be misread.",
                {"pages": [page for page, _ in low]},
            )
        )
    if data.uncertain_fields:
        errors.append(
            ErrorSpec(
                ReasonCode.MODEL_UNCERTAIN,
                Severity.WARNING,
                "The model was unsure about: " + ", ".join(data.uncertain_fields) + ".",
                {"fields": list(data.uncertain_fields)},
            )
        )
    return errors


def _str(value: object) -> str | None:
    return None if value is None else str(value)
