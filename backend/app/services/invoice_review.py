"""
Manual correction of extracted invoices.
"""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from app.core.exceptions import NotFoundError
from app.extraction.llm import LLMExtraction
from app.extraction.normalize import invoice_number_key, normalize_invoice, vendor_key
from app.extraction.records import data_from_invoice, invoice_from_data
from app.extraction.review import Duplicate, build_validation_errors
from app.extraction.schema import InvoiceData
from app.extraction.validation import validate_amounts
from app.models.invoice import InvoiceCorrection
from app.repositories.document import DocumentRepository


def diff_invoices(old: InvoiceData, new: InvoiceData) -> dict[str, dict[str, Any]]:
    """Changed fields as ``{field: {"old": ..., "new": ...}}`` (JSON values).

    Vendor and customer fields are reported individually
    (``vendor.tax_id``); line items are compared as a whole list.
    """
    before, after = old.model_dump(mode="json"), new.model_dump(mode="json")
    changes: dict[str, dict[str, Any]] = {}
    for field, value in after.items():
        if field in ("vendor", "customer"):
            for key, sub_value in value.items():
                if before[field][key] != sub_value:
                    changes[f"{field}.{key}"] = {
                        "old": before[field][key],
                        "new": sub_value,
                    }
        elif before[field] != value:
            changes[field] = {"old": before[field], "new": value}
    return changes


class InvoiceCorrectionService:
    """Apply a person's corrections to a document's extracted invoice."""

    def __init__(self, repository: DocumentRepository, tolerance: Decimal) -> None:
        self.repository = repository
        self.tolerance = tolerance

    async def correct(
        self, document_id: uuid.UUID, corrected: InvoiceData
    ) -> dict[str, dict[str, Any]]:
        """Replace the invoice with the corrected values; returns the changes.

        The corrected invoice is re-validated and re-checked for duplicates,
        and the document's validation errors are replaced with the new
        result. Warnings about the *model's* reading (low OCR confidence,
        model uncertainty) are dropped: a person has now checked the values.
        Nothing is written when nothing changed.
        """
        document = await self.repository.get(document_id)
        if document is None:
            raise NotFoundError(f"Document {document_id} not found.")
        invoice = await self.repository.get_invoice(document_id)
        if invoice is None:
            raise NotFoundError(
                f"Document {document_id} has no extracted invoice to correct."
            )

        # The values are now human-verified, so nothing is uncertain.
        new = normalize_invoice(corrected).model_copy(update={"uncertain_fields": []})
        changes = diff_invoices(data_from_invoice(invoice), new)
        if not changes:
            return {}

        duplicates = await self.repository.find_duplicates(
            document_id,
            sha256=document.sha256,
            number_key=invoice_number_key(new.invoice_number),
            vendor_key=vendor_key(new.vendor),
            invoice_date=new.invoice_date,
            total_amount=(
                Decimal(str(new.total_amount)) if new.total_amount is not None else None
            ),
        )
        errors = build_validation_errors(
            new,
            validate_amounts(new, self.tolerance),
            [Duplicate(str(other), match) for other, match in duplicates],
            ocr_confidences=[],
            min_ocr_confidence=0,
        )
        replacement = invoice_from_data(
            document_id,
            new,
            # Keep the original extraction's model and usage.
            LLMExtraction(
                data=new,
                model=invoice.model,
                input_tokens=invoice.input_tokens,
                output_tokens=invoice.output_tokens,
                cache_read_tokens=invoice.cache_read_tokens,
            ),
        )
        replacement.corrected_at = dt.datetime.now(dt.UTC)
        await self.repository.save_invoice(
            replacement,
            errors,
            flag_documents=duplicates if new.is_invoice else (),
            correction=InvoiceCorrection(document_id=document_id, changes=changes),
        )
        return changes
