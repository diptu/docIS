"""
Convert the third-party invoice set (``evaluation/third_party``) into the
harness layout, leaving the source untouched:

    <out>/dataset/documents/<doc_id>.<ext>        copied byte-for-byte
    <out>/dataset/ground_truth/<doc_id>.json      partial InvoiceData label
    <out>/validation_expectations/<doc_id>.json

Source labels: ``ground_truth/<doc_id>.json`` with ``file``, ``category`` and
``expected`` = {vendor_name, invoice_number, invoice_date, due_date, currency,
subtotal, tax, total, document_type, lines: [{description, amount,
alt_amounts}], should_flag, references, scored: [...], ...}.

Mapping (only fields named in ``scored`` are scored; the rest are null and
ignored):

* vendor_name -> vendor.name; tax -> tax_amount; total -> total_amount;
  invoice_number, invoice_date, due_date, currency, subtotal unchanged.
* document_type -> is_invoice: "invoice" and "credit_note" are True, "other"
  (statements and the like) is False. For "other" only is_invoice is scored:
  this app leaves every field of a non-invoice empty by design.
* line_items -> line.description and line.amount (the labels have nothing
  else per line). ``alt_amounts`` (a line's amount in a second currency) is
  not accepted as an alternative: the primary amount must match.
* flag -> ``should_flag``: the extraction's amount checks should flag it.
* Not mapped (no field in this app): references, secondary (dual-currency
  totals).

``category`` (clean, photo, scan, handwritten, credit, ...) becomes the
case's format, so the report breaks results down by category.
"""

import json
import shutil
from pathlib import Path
from typing import Any

from app.extraction.schema import InvoiceData
from evaluation.evaluate_validation import Expectation, expectation_for
from evaluation.reporting import ROOT, write_json

SOURCE_DIR = ROOT / "third_party"
OUT_DIR = ROOT / "third_party_eval"

# Source "scored" names -> harness field names (metrics.FieldScore.field).
SCORED = {
    "vendor_name": ("vendor.name",),
    "invoice_number": ("invoice_number",),
    "invoice_date": ("invoice_date",),
    "due_date": ("due_date",),
    "currency": ("currency",),
    "subtotal": ("subtotal",),
    "tax": ("tax_amount",),
    "total": ("total_amount",),
    "document_type": ("is_invoice",),
    "line_items": ("line.description", "line.amount"),
}
UNMAPPED = {"flag", "references"}  # flag becomes should_flag


def _party(name: str | None = None) -> dict[str, Any]:
    return {"name": name, "address": None, "tax_id": None, "email": None, "phone": None}


def convert_label(source: dict[str, Any]) -> dict[str, Any]:
    """One source label as a harness ground-truth label."""
    e = source["expected"]
    is_invoice = e["document_type"] != "other"
    scored = [name for key in e["scored"] for name in SCORED.get(key, ())]
    if unknown := set(e["scored"]) - SCORED.keys() - UNMAPPED:
        raise ValueError(f"{source['doc_id']}: unknown scored field(s) {unknown}")
    if not is_invoice:
        scored = ["is_invoice"]

    expected = InvoiceData.model_validate(
        {
            "is_invoice": is_invoice,
            "invoice_number": e["invoice_number"] if is_invoice else None,
            "invoice_date": e["invoice_date"] if is_invoice else None,
            "due_date": e["due_date"] if is_invoice else None,
            "currency": e["currency"] if is_invoice else None,
            "purchase_order_number": None,
            "payment_terms": None,
            "vendor": _party(e["vendor_name"] if is_invoice else None),
            "customer": _party(),
            "line_items": [
                {
                    "description": line["description"],
                    "product_code": None,
                    "quantity": None,
                    "unit": None,
                    "unit_price": None,
                    "tax_rate": None,
                    "amount": line["amount"],
                }
                for line in (e["lines"] if is_invoice else [])
            ],
            "subtotal": e["subtotal"] if is_invoice else None,
            "tax_amount": e["tax"] if is_invoice else None,
            "total_amount": e["total"] if is_invoice else None,
            "amount_due": None,
            "uncertain_fields": [],
        }
    )
    label: dict[str, Any] = {
        "format": source.get("category", "unknown"),
        "locale": (source.get("currency") or "unknown").lower(),
        "source": {
            "doc_id": source["doc_id"],
            "kind": source.get("kind"),
            "difficulty": source.get("difficulty"),
            "description": source.get("description"),
        },
        "scored_fields": scored,
        "expected": expected.model_dump(mode="json"),
    }
    if "flag" in e["scored"]:
        label["should_flag"] = bool(e["should_flag"])
    return label


def expectation(label: dict[str, Any], name: str) -> Expectation | None:
    """Validation expectations: a flagged invoice must fail some check; any
    other invoice must pass, and gets the usual injected errors. Non-invoices
    have no checks, so no expectations."""
    data = InvoiceData.model_validate(label["expected"])
    if not data.is_invoice:
        return None
    if label.get("should_flag"):
        return Expectation(name, (), (), should_flag=True)
    generated = expectation_for(data)
    return Expectation(name, (), generated.injected_errors)


def convert(source: Path = SOURCE_DIR, out: Path = OUT_DIR) -> int:
    """Write the harness layout to ``out``; returns the number of cases."""
    documents = out / "dataset" / "documents"
    labels = out / "dataset" / "ground_truth"
    expectations = out / "validation_expectations"
    for directory in (documents, labels, expectations):
        if directory.exists():
            shutil.rmtree(directory)  # stale cases from an earlier conversion
        directory.mkdir(parents=True)

    count = 0
    for path in sorted((source / "ground_truth").glob("*.json")):
        raw = json.loads(path.read_text())
        name = raw["doc_id"]
        document = source / raw["file"]
        if not document.is_file():
            raise FileNotFoundError(f"{path.name}: {raw['file']} is missing")
        shutil.copy2(document, documents / f"{name}{document.suffix.lower()}")
        label = convert_label(raw)
        write_json(labels / f"{name}.json", label)
        if (exp := expectation(label, name)) is not None:
            write_json(expectations / f"{name}.json", exp.to_json())
        count += 1
    return count
