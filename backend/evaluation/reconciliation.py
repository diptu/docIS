"""
Labelled cross-document comparison pairs, and how well the app's
comparison features find their differences.

Two kinds of pair, generated deterministically from synthetic invoices
(``generate.make_invoice``), so every label follows from the data:

* ``pdf_pdf``: an invoice PDF (left) and a second version of it (right),
  digital or scanned, with one change: none, a quantity, a unit price, a
  removed, added or reordered line, the invoice number, the dates, or only
  the total (a typo). Totals are recomputed after line changes, so both
  documents are internally consistent unless the change is the total.
* ``pdf_excel``: an invoice PDF, digital or scanned, and a spreadsheet of
  its lines (XLSX with number cells, or CSV with locale-formatted text),
  with one difference: none, an amount, a quantity, a unit price, an extra
  row, or a missing row.

Evaluation runs the app's own code on the files:

* PDF vs Excel: text extraction/OCR and spreadsheet parsing
  (``extract_content``), then ``reconcile_rows``, scored per data row
  (should it be flagged?) and per pair. Heading rows are not scored but
  their false alarms are counted.
* PDF vs PDF, text: ``compare_text`` on the extracted text; a pair is
  flagged when the diff has any changed line.
* PDF vs PDF, invoice: ``compare_invoices`` on the two invoices, scored per
  field, per line pairing and per pair. ``extraction="exact"`` uses the
  labelled invoices (measures the comparison logic alone); ``"claude"``
  extracts both PDFs with the app's pipeline first (costs API credit).

Labels never come from the code under test: field differences compare the
two labelled invoices value by value, and line pairs are known from how the
right-hand invoice was built.
"""

import asyncio
import csv
import datetime as dt
import io
import json
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import openpyxl

from app.compare.service import (
    HEADER_FIELDS,
    LINE_FIELDS,
    PARTY_FIELDS,
    TOTAL_FIELDS,
    Status,
    compare_invoices,
    compare_text,
    reconcile_rows,
)
from app.extraction.chunked import extract_document
from app.extraction.document_text import document_sections
from app.extraction.llm import InvoiceLLM
from app.extraction.normalize import normalize_invoice
from app.extraction.schema import InvoiceData, InvoiceLineItem
from app.models import DocumentPage, DocumentSheet
from app.processing.extract import extract_content
from app.processing.ocr import OcrConfig
from evaluation.generate import (
    ITEMS,
    LABELS,
    Locale,
    _q,
    make_invoice,
    render_pdf,
    render_scan,
)
from evaluation.reporting import ROOT, write_json

PAIRS_DIR = ROOT / "reconciliation_eval"
MEDIA = {
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
}

PDF_CHANGES = (
    "identical",
    "quantity_changed",
    "unit_price_changed",
    "line_removed",
    "line_added",
    "lines_reordered",
    "invoice_number_changed",
    "dates_changed",
    "total_changed",
)
SHEET_CHANGES = (
    "matching",
    "amount_changed",
    "quantity_changed",
    "unit_price_changed",
    "extra_row",
    "row_missing",
)

# ------------------------------------------------------------------ building


def _dec(value: float | None) -> Decimal:
    return Decimal(str(value or 0))


def with_lines(base: InvoiceData, lines: list[InvoiceLineItem]) -> InvoiceData:
    """``base`` with other lines and totals recomputed (a deposit is kept)."""
    subtotal = sum((_dec(line.amount) for line in lines), Decimal(0))
    tax = sum(
        (_q(_dec(line.amount) * _dec(line.tax_rate) / 100) for line in lines),
        Decimal(0),
    )
    total = subtotal + tax
    amount_due = None
    if base.amount_due is not None:
        deposit = _dec(base.total_amount) - _dec(base.amount_due)
        amount_due = float(total - deposit)
    return base.model_copy(
        update={
            "line_items": lines,
            "subtotal": float(subtotal),
            "tax_amount": float(tax),
            "total_amount": float(total),
            "amount_due": amount_due,
        }
    )


def _new_item(rng: random.Random, base: InvoiceData) -> InvoiceLineItem:
    taken = {line.description for line in base.line_items}
    description, code, unit, (low, high) = rng.choice(
        [item for item in ITEMS if item[0] not in taken]
    )
    quantity = Decimal(rng.choice([1, 2, 3, 5, 10]))
    price = _q(Decimal(str(rng.uniform(low, high))))
    rate = base.line_items[0].tax_rate if base.line_items else 0.0
    return InvoiceLineItem(
        description=description,
        product_code=code,
        quantity=float(quantity),
        unit=unit,
        unit_price=float(price),
        tax_rate=rate,
        amount=float(_q(quantity * price)),
    )


def _requantified(line: InvoiceLineItem, quantity: Decimal) -> InvoiceLineItem:
    return line.model_copy(
        update={
            "quantity": float(quantity),
            "amount": float(_q(quantity * _dec(line.unit_price))),
        }
    )


def _repriced(line: InvoiceLineItem, price: Decimal) -> InvoiceLineItem:
    return line.model_copy(
        update={
            "unit_price": float(price),
            "amount": float(_q(_dec(line.quantity) * price)),
        }
    )


def change_invoice(
    rng: random.Random, base: InvoiceData, change: str
) -> tuple[InvoiceData, list[int | None]]:
    """The right-hand invoice, and for each of its lines the index of the
    base line it corresponds to (None for an added line)."""
    lines = list(base.line_items)
    origin: list[int | None] = list(range(len(lines)))
    k = rng.randrange(len(lines))
    if change == "identical":
        return base, origin
    if change == "quantity_changed":
        lines[k] = _requantified(
            lines[k], _dec(lines[k].quantity) + rng.choice([1, 2, 5])
        )
    elif change == "unit_price_changed":
        lines[k] = _repriced(lines[k], _q(_dec(lines[k].unit_price) * Decimal("1.07")))
    elif change == "line_removed":
        del lines[k], origin[k]
    elif change == "line_added":
        lines.append(_new_item(rng, base))
        origin.append(None)
    elif change == "lines_reordered":
        order = list(range(len(lines)))
        while len(order) > 1 and order == sorted(order):
            rng.shuffle(order)
        lines, origin = [lines[i] for i in order], [origin[i] for i in order]
        return base.model_copy(update={"line_items": lines}), origin
    elif change == "invoice_number_changed":
        assert base.invoice_number is not None
        return base.model_copy(
            update={"invoice_number": base.invoice_number + "-R1"}
        ), origin
    elif change == "dates_changed":
        assert base.invoice_date is not None
        shift = rng.choice([1, 3, 7])
        due = base.due_date
        return base.model_copy(
            update={
                "invoice_date": base.invoice_date + dt.timedelta(days=shift),
                "due_date": due + dt.timedelta(days=shift) if due else None,
            }
        ), origin
    elif change == "total_changed":
        bumped = _dec(base.total_amount) + Decimal("10.00")
        balance = base.amount_due
        return base.model_copy(
            update={
                "total_amount": float(bumped),
                "amount_due": float(_dec(balance) + Decimal("10.00"))
                if balance is not None
                else None,
            }
        ), origin
    else:
        raise ValueError(f"unknown change {change!r}")
    return with_lines(base, lines), origin


def sheet_rows(
    rng: random.Random, invoice: InvoiceData, change: str
) -> tuple[list[InvoiceLineItem], list[bool]]:
    """The spreadsheet's data rows and, for each, whether it agrees with the
    invoice (``row_missing`` drops a row: every remaining row agrees)."""
    rows = list(invoice.line_items)
    agrees = [True] * len(rows)
    k = rng.randrange(len(rows))
    if change == "matching":
        pass
    elif change == "amount_changed":
        rows[k] = rows[k].model_copy(
            update={"amount": float(_dec(rows[k].amount) + Decimal("12.50"))}
        )
        agrees[k] = False
    elif change == "quantity_changed":
        rows[k] = _requantified(rows[k], _dec(rows[k].quantity) + 1)
        agrees[k] = False
    elif change == "unit_price_changed":
        rows[k] = _repriced(rows[k], _q(_dec(rows[k].unit_price) * Decimal("0.95")))
        agrees[k] = False
    elif change == "extra_row":
        rows.append(_new_item(rng, invoice))
        agrees.append(False)
    elif change == "row_missing":
        del rows[k], agrees[k]
    else:
        raise ValueError(f"unknown change {change!r}")
    return rows, agrees


def _heading(locale: Locale) -> list[str]:
    t = LABELS[locale.language]
    return [t["description"], t["code"], t["qty"], t["price"], t["amount"]]


def render_sheet(lines: list[InvoiceLineItem], locale: Locale, kind: str) -> bytes:
    """A line-item export: heading row, then one row per line."""
    if kind == "xlsx":
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        assert sheet is not None
        sheet.append(_heading(locale))
        for line in lines:
            sheet.append(
                [
                    line.description,
                    line.product_code,
                    line.quantity,
                    line.unit_price,
                    line.amount,
                ]
            )
        buffer = io.BytesIO()
        workbook.save(buffer)
        return buffer.getvalue()
    text = io.StringIO()
    writer = csv.writer(text, delimiter=";" if locale.code == "de" else ",")
    writer.writerow(_heading(locale))
    for line in lines:
        writer.writerow(
            [
                line.description or "",
                line.product_code or "",
                locale.number(_dec(line.quantity), 0),
                locale.number(_dec(line.unit_price)),
                locale.number(_dec(line.amount)),
            ]
        )
    return text.getvalue().encode("utf-8")


def _render_pdf(
    invoice: InvoiceData, locale: Locale, kind: str, rng: random.Random
) -> bytes:
    return (
        render_pdf(invoice, locale)
        if kind == "pdf"
        else render_scan(invoice, locale, rng)
    )


def _base_invoice(rng: random.Random, number: int) -> tuple[InvoiceData, Locale]:
    """A generated invoice with at least two lines (so a line can change
    while another stays)."""
    while True:
        invoice, locale = make_invoice(rng, number)
        if len(invoice.line_items) >= 2:
            return invoice, locale


def generate_pairs(out: Path = PAIRS_DIR, seed: int = 2026, repeats: int = 3) -> int:
    """Write labelled pairs to ``out/<pair_id>/``; returns how many.

    PDF vs PDF: every change x right side (digital, scanned) x ``repeats``.
    PDF vs Excel: every difference x left (digital, scanned) x sheet (XLSX,
    CSV) x ``repeats - 1`` (at least 1).
    """
    rng = random.Random(seed)
    count = 0
    number = 1
    for change in PDF_CHANGES:
        for right_kind in ("pdf", "scan"):
            for _ in range(repeats):
                base, locale = _base_invoice(rng, number)
                right, origin = change_invoice(rng, base, change)
                pair_id = f"pdf-pdf-{count + 1:03d}"
                folder = out / pair_id
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "left.pdf").write_bytes(render_pdf(base, locale))
                (folder / "right.pdf").write_bytes(
                    _render_pdf(right, locale, right_kind, rng)
                )
                write_json(
                    folder / "label.json",
                    {
                        "pair_id": pair_id,
                        "kind": "pdf_pdf",
                        "change": change,
                        "left": "left.pdf",
                        "right": "right.pdf",
                        "left_format": "pdf",
                        "right_format": right_kind,
                        "locale": locale.code,
                        "left_invoice": base.model_dump(mode="json"),
                        "right_invoice": right.model_dump(mode="json"),
                        "expected": pdf_pdf_expectation(base, right, origin),
                    },
                )
                count += 1
                number += 1
    for change in SHEET_CHANGES:
        for left_kind in ("pdf", "scan"):
            for sheet_kind in ("xlsx", "csv"):
                for _ in range(max(1, repeats - 1)):
                    base, locale = _base_invoice(rng, number)
                    rows, agrees = sheet_rows(rng, base, change)
                    pair_id = f"pdf-excel-{count + 1:03d}"
                    folder = out / pair_id
                    folder.mkdir(parents=True, exist_ok=True)
                    (folder / "left.pdf").write_bytes(
                        _render_pdf(base, locale, left_kind, rng)
                    )
                    (folder / f"right.{sheet_kind}").write_bytes(
                        render_sheet(rows, locale, sheet_kind)
                    )
                    write_json(
                        folder / "label.json",
                        {
                            "pair_id": pair_id,
                            "kind": "pdf_excel",
                            "change": change,
                            "left": "left.pdf",
                            "right": f"right.{sheet_kind}",
                            "left_format": left_kind,
                            "right_format": sheet_kind,
                            "locale": locale.code,
                            "left_invoice": base.model_dump(mode="json"),
                            "expected": {
                                "differ": change != "matching",
                                "heading_rows": [1],
                                # Sheet row numbers (1-based; row 1 is the heading).
                                "rows": [
                                    {"row": i + 2, "agrees": ok}
                                    for i, ok in enumerate(agrees)
                                ],
                            },
                        },
                    )
                    count += 1
                    number += 1
    return count


# ------------------------------------------------------------------ labels


def _differs(a: Any, b: Any) -> bool:
    if isinstance(a, int | float) and isinstance(b, int | float):
        return abs(Decimal(str(a)) - Decimal(str(b))) > Decimal("0.005")
    return bool(a != b)


def _field_values(invoice: InvoiceData) -> dict[str, Any]:
    values = {name: getattr(invoice, name) for name in (*HEADER_FIELDS, *TOTAL_FIELDS)}
    for party in ("vendor", "customer"):
        for name in PARTY_FIELDS:
            values[f"{party}.{name}"] = getattr(getattr(invoice, party), name)
    return values


def pdf_pdf_expectation(
    left: InvoiceData, right: InvoiceData, origin: list[int | None]
) -> dict[str, Any]:
    """What a correct comparison reports, from the two labelled invoices."""
    a, b = _field_values(left), _field_values(right)
    different_fields = sorted(
        name
        for name in a
        if not (a[name] is None and b[name] is None) and _differs(a[name], b[name])
    )
    pairs = [[i + 1, j + 1] for j, i in enumerate(origin) if i is not None]
    different_lines = [
        [i, j]
        for i, j in pairs
        if any(
            _differs(
                getattr(left.line_items[i - 1], f), getattr(right.line_items[j - 1], f)
            )
            for f in LINE_FIELDS
        )
    ]
    paired_left = {i for i, _ in pairs}
    left_only = [i + 1 for i in range(len(left.line_items)) if i + 1 not in paired_left]
    right_only = [j + 1 for j, i in enumerate(origin) if i is None]
    return {
        "differ": bool(different_fields or different_lines or left_only or right_only),
        "different_fields": different_fields,
        "compared_fields": sorted(
            name for name in a if not (a[name] is None and b[name] is None)
        ),
        "line_pairs": pairs,
        "different_lines": different_lines,
        "left_only": left_only,
        "right_only": right_only,
    }


# -------------------------------------------------------------- evaluation


@dataclass
class Counts:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    def add(self, predicted: bool, actual: bool) -> None:
        if predicted and actual:
            self.tp += 1
        elif predicted:
            self.fp += 1
        elif actual:
            self.fn += 1
        else:
            self.tn += 1

    def summary(self) -> dict[str, Any]:
        def pct(n: int, d: int) -> float | None:
            return round(100 * n / d, 1) if d else None

        total = self.tp + self.fp + self.fn + self.tn
        return {
            "n": total,
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "tn": self.tn,
            "accuracy": pct(self.tp + self.tn, total),
            "precision": pct(self.tp, self.tp + self.fp),
            "recall": pct(self.tp, self.tp + self.fn),
            "false_alarm_rate": pct(self.fp, self.fp + self.tn),
        }


def _content(
    path: Path, config: OcrConfig
) -> tuple[str, list[tuple[str, list[list[Any]]]]]:
    result = extract_content(path, MEDIA[path.suffix], config, path.name)
    text = "\n\n".join(page.text for page in result.pages)
    return text, [(sheet.name, sheet.rows) for sheet in result.sheets]


async def _extract(llm: InvoiceLLM, path: Path, config: OcrConfig) -> InvoiceData:
    result = await asyncio.to_thread(
        extract_content, path, MEDIA[path.suffix], config, path.name
    )
    pages = [DocumentPage(page_number=p.page_number, text=p.text) for p in result.pages]
    sheets = [
        DocumentSheet(sheet_number=s.sheet_number, name=s.name, rows=s.rows)
        for s in result.sheets
    ]
    extraction = await extract_document(llm, document_sections(pages, sheets))
    return normalize_invoice(extraction.data)


def _load(folder: Path) -> dict[str, Any]:
    label: dict[str, Any] = json.loads((folder / "label.json").read_text())
    return label


def evaluate_pairs(
    pairs_dir: Path = PAIRS_DIR,
    config: OcrConfig | None = None,
    extraction: str = "exact",
    llm: InvoiceLLM | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Run the comparisons on every pair and score them against the labels."""
    config = config or OcrConfig()
    folders = sorted(p for p in pairs_dir.iterdir() if (p / "label.json").exists())
    text_pairs: dict[str, Counts] = {"all": Counts()}
    invoice_pairs, field_counts, line_diff = Counts(), Counts(), Counts()
    pairing = {"expected": 0, "predicted": 0, "correct": 0}
    excel_rows: dict[str, Counts] = {"all": Counts()}
    excel_pairs: dict[str, Counts] = {"all": Counts()}
    heading_alarms = heading_total = 0
    by_change: dict[str, dict[str, Any]] = {}
    cases: list[dict[str, Any]] = []
    seconds: list[float] = []

    for index, folder in enumerate(folders, start=1):
        label = _load(folder)
        expected = label["expected"]
        started = time.perf_counter()
        left_text, _ = _content(folder / label["left"], config)
        right_text, right_sheets = _content(folder / label["right"], config)
        case: dict[str, Any] = {
            "pair_id": label["pair_id"],
            "kind": label["kind"],
            "change": label["change"],
            "left_format": label["left_format"],
            "right_format": label["right_format"],
        }
        stats = by_change.setdefault(
            f"{label['kind']}:{label['change']}",
            {"pairs": 0, "detected": 0, "differ": expected["differ"]},
        )
        stats["pairs"] += 1

        if label["kind"] == "pdf_pdf":
            text = compare_text(left_text, right_text)
            text_flag = text.added + text.removed > 0
            for key in ("all", f"right_{label['right_format']}"):
                text_pairs.setdefault(key, Counts()).add(text_flag, expected["differ"])

            if extraction == "exact":
                left_inv = InvoiceData.model_validate(label["left_invoice"])
                right_inv = InvoiceData.model_validate(label["right_invoice"])
            else:
                assert llm is not None
                left_inv = asyncio.run(_extract(llm, folder / label["left"], config))
                right_inv = asyncio.run(_extract(llm, folder / label["right"], config))
            fields, lines = compare_invoices(left_inv, right_inv)
            status = {f.field: f.status for f in fields}
            for name in expected["compared_fields"]:
                field_counts.add(
                    status[name] not in (Status.EQUAL, Status.BOTH_MISSING),
                    name in expected["different_fields"],
                )
            predicted_pairs = {
                (p.left_index, p.right_index)
                for p in lines
                if p.left_index is not None and p.right_index is not None
            }
            expected_pairs = {tuple(p) for p in expected["line_pairs"]}
            pairing["expected"] += len(expected_pairs)
            pairing["predicted"] += len(predicted_pairs)
            pairing["correct"] += len(predicted_pairs & expected_pairs)
            different = {tuple(p) for p in expected["different_lines"]}
            for p in lines:
                if (p.left_index, p.right_index) in expected_pairs:
                    line_diff.add(
                        p.status == "different",
                        (p.left_index, p.right_index) in different,
                    )
            invoice_flag = any(
                f.status not in (Status.EQUAL, Status.BOTH_MISSING) for f in fields
            ) or any(p.status != "equal" for p in lines)
            invoice_pairs.add(invoice_flag, expected["differ"])
            stats["detected"] += invoice_flag
            case.update(
                text_flagged=text_flag,
                text_similarity=text.similarity,
                invoice_flagged=invoice_flag,
                pairs_correct=predicted_pairs == expected_pairs,
            )
        else:
            checks = reconcile_rows(right_sheets, left_text)
            by_row = {c.row_number: c for c in checks}
            for row in expected["heading_rows"]:
                heading_total += 1
                heading_alarms += row in by_row and by_row[row].status != "found"
            pair_flag = False
            wrong_rows = []
            for row in expected["rows"]:
                check = by_row.get(row["row"])
                flagged = check is None or check.status != "found"
                pair_flag |= flagged
                for key in (
                    "all",
                    f"left_{label['left_format']}",
                    f"sheet_{label['right_format']}",
                ):
                    excel_rows.setdefault(key, Counts()).add(flagged, not row["agrees"])
                if flagged != (not row["agrees"]):
                    wrong_rows.append(
                        {
                            "row": row["row"],
                            "agrees": row["agrees"],
                            "status": check.status if check else None,
                            "missing": [str(v) for v in check.missing] if check else [],
                        }
                    )
            for key in (
                "all",
                f"left_{label['left_format']}",
                f"sheet_{label['right_format']}",
            ):
                excel_pairs.setdefault(key, Counts()).add(pair_flag, expected["differ"])
            stats["detected"] += pair_flag
            case.update(flagged=pair_flag, row_errors=wrong_rows)

        seconds.append(time.perf_counter() - started)
        cases.append(case)
        log(f"  [{index}/{len(folders)}] {label['pair_id']} {label['change']}")

    def counts(group: dict[str, Counts]) -> dict[str, Any]:
        return {key: value.summary() for key, value in sorted(group.items())}

    pdf_pdf_pairs = sum(1 for c in cases if c["kind"] == "pdf_pdf")
    ordered = sorted(seconds)
    return {
        "pairs": len(cases),
        "extraction": extraction,
        "median_seconds_per_pair": round(ordered[len(ordered) // 2], 3)
        if ordered
        else None,
        "pdf_pdf": {
            "pairs": pdf_pdf_pairs,
            "text_diff": counts(text_pairs),
            "invoice_comparison": {
                "pairs": invoice_pairs.summary(),
                "fields": field_counts.summary(),
                "line_pairing": {
                    **pairing,
                    "precision": round(
                        100 * pairing["correct"] / pairing["predicted"], 1
                    )
                    if pairing["predicted"]
                    else None,
                    "recall": round(100 * pairing["correct"] / pairing["expected"], 1)
                    if pairing["expected"]
                    else None,
                },
                "line_differences": line_diff.summary(),
            },
        },
        "pdf_excel": {
            "pairs": len(cases) - pdf_pdf_pairs,
            "rows": counts(excel_rows),
            "pair_level": counts(excel_pairs),
            "heading_rows": {
                "rows": heading_total,
                "flagged": heading_alarms,
            },
        },
        "by_change": dict(sorted(by_change.items())),
        "cases": cases,
    }


# ------------------------------------------------------------------ report


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value}%"


def render_summary(results: dict[str, Any]) -> str:
    """The measured results as Markdown tables."""
    pp, pe = results["pdf_pdf"], results["pdf_excel"]
    inv = pp["invoice_comparison"]
    lines = [
        "# Cross-document comparison",
        "",
        f"{results['pairs']} labelled pairs · invoice extraction: "
        f"{results['extraction']} · median "
        f"{results['median_seconds_per_pair']} s per pair",
        "",
        "## PDF vs Excel: row reconciliation (end to end, no AI)",
        "",
        "| Scope | Rows/pairs | Accuracy | Precision | Recall | False-alarm rate |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for scope, group in (("Rows", pe["rows"]), ("Pairs", pe["pair_level"])):
        for key, c in group.items():
            lines.append(
                f"| {scope}: {key} | {c['n']} | {_pct(c['accuracy'])} | "
                f"{_pct(c['precision'])} | {_pct(c['recall'])} | "
                f"{_pct(c['false_alarm_rate'])} |"
            )
    heading = pe["heading_rows"]
    lines += [
        "",
        "Heading rows flagged (not scored): "
        f"{heading['flagged']} of {heading['rows']}.",
        "",
        "## PDF vs PDF",
        "",
        "| Method | Scope | n | Accuracy | Precision | Recall | False-alarm rate |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for key, c in pp["text_diff"].items():
        lines.append(
            f"| Text diff | pairs: {key} | {c['n']} | {_pct(c['accuracy'])} | "
            f"{_pct(c['precision'])} | {_pct(c['recall'])} | "
            f"{_pct(c['false_alarm_rate'])} |"
        )
    for scope, c in (
        ("pairs", inv["pairs"]),
        ("fields", inv["fields"]),
        ("line differences", inv["line_differences"]),
    ):
        lines.append(
            f"| Invoice comparison | {scope} | {c['n']} | {_pct(c['accuracy'])} | "
            f"{_pct(c['precision'])} | {_pct(c['recall'])} | "
            f"{_pct(c['false_alarm_rate'])} |"
        )
    pairing = inv["line_pairing"]
    lines += [
        "",
        f"Line pairing: {pairing['correct']} of {pairing['expected']} expected pairs "
        f"found ({_pct(pairing['recall'])}), {pairing['predicted']} predicted "
        f"(precision {_pct(pairing['precision'])}).",
        "",
        "## By change",
        "",
        "| Pair kind: change | Pairs | Really differ | Flagged |",
        "| --- | --- | --- | --- |",
    ]
    for key, s in results["by_change"].items():
        lines.append(
            f"| {key} | {s['pairs']} | {'yes' if s['differ'] else 'no'} "
            f"| {s['detected']} |"
        )
    return "\n".join(lines) + "\n"


def write_reconciliation(results: dict[str, Any], out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "reconciliation_results.json", results)
    path = out / "reconciliation_summary.md"
    path.write_text(render_summary(results))
    return path
