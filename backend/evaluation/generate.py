"""
Synthetic labelled invoices for measuring extraction accuracy.


* locale: US, UK or German labels, date formats and number formats;
* format: digital PDF, scanned PDF and scanned TIFF (rasterised, noisy,
  slightly rotated, so they go through OCR), XLSX and CSV;
* content: 1-6 line items, 1-3 tax rates, optional fields (PO number, due
  date, deposit/amount due, contact details) left out, so null handling is
  measured too.

Everything printed is formatted from the ground truth, so the label always
matches the document. Generation is deterministic for a given seed.
"""

import csv
import datetime as dt
import io
import random
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import openpyxl
import pypdfium2 as pdfium
from PIL import Image, ImageFilter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.extraction.schema import InvoiceData, InvoiceLineItem, Party
from evaluation.evaluate_validation import expectation_for
from evaluation.reporting import write_json

CENT = Decimal("0.01")
FORMATS = ("pdf", "scan", "tiff", "xlsx", "csv")

VENDORS = [
    ("ACME GmbH", "Hauptstraße 12, 10115 Berlin, Germany", "DE123456789"),
    (
        "Northwind Traders Ltd",
        "14 King Street, Manchester M2 4LQ, United Kingdom",
        "GB987654321",
    ),
    (
        "Globex Corporation",
        "500 Market Street, San Francisco, CA 94105, USA",
        "94-1234567",
    ),
    ("Müller & Söhne KG", "Industriestraße 4, 70565 Stuttgart, Germany", "DE811234567"),
    ("Initech LLC", "4120 Freidrich Lane, Austin, TX 78744, USA", "74-7654321"),
    (
        "Contoso Supplies plc",
        "1 Canada Square, London E14 5AB, United Kingdom",
        "GB123987456",
    ),
]
CUSTOMERS = [
    ("Stark Industries", "10880 Malibu Point, Malibu, CA 90265, USA"),
    ("Wayne Enterprises", "1007 Mountain Drive, Gotham, NJ 07001, USA"),
    ("Umbrella AG", "Rheinallee 3, 55116 Mainz, Germany"),
    ("Tyrell Systems Ltd", "22 Baker Street, London NW1 6XE, United Kingdom"),
]
ITEMS = [
    ("Consulting services", "SVC-100", "h", (80, 160)),
    ("Software licence (annual)", "LIC-200", "pcs", (300, 2400)),
    ("Steel bolts M8", "BLT-008", "pcs", (0.2, 1.5)),
    ("Office chairs", "FUR-310", "pcs", (90, 450)),
    ("Cloud hosting", "CLD-001", "month", (40, 900)),
    ("Training workshop", "TRN-050", "day", (600, 1800)),
    ("Shipping and handling", "SHP-001", "flat", (10, 90)),
    ("Printer toner", "TON-777", "pcs", (25, 120)),
]

LABELS = {
    "en": {
        "title": "INVOICE",
        "number": "Invoice No.",
        "date": "Invoice date",
        "due": "Due date",
        "po": "PO number",
        "terms": "Payment terms",
        "bill_to": "Bill to",
        "vat": "VAT ID",
        "description": "Description",
        "code": "Item",
        "qty": "Qty",
        "unit": "Unit",
        "price": "Unit price",
        "rate": "Tax %",
        "amount": "Amount",
        "subtotal": "Subtotal",
        "tax": "Tax",
        "total": "Total",
        "deposit": "Deposit paid",
        "due_amount": "Amount due",
        "email": "Email",
        "phone": "Phone",
    },
    "de": {
        "title": "RECHNUNG",
        "number": "Rechnungsnummer",
        "date": "Rechnungsdatum",
        "due": "Fällig am",
        "po": "Bestellnummer",
        "terms": "Zahlungsbedingungen",
        "bill_to": "Rechnungsempfänger",
        "vat": "USt-IdNr.",
        "description": "Beschreibung",
        "code": "Art.-Nr.",
        "qty": "Menge",
        "unit": "Einheit",
        "price": "Einzelpreis",
        "rate": "MwSt. %",
        "amount": "Betrag",
        "subtotal": "Zwischensumme",
        "tax": "MwSt.",
        "total": "Gesamtbetrag",
        "deposit": "Anzahlung",
        "due_amount": "Offener Betrag",
        "email": "E-Mail",
        "phone": "Telefon",
    },
}


@dataclass(frozen=True)
class Locale:
    code: str
    language: str
    currency: str
    rates: tuple[Decimal, ...]

    def date(self, value: dt.date) -> str:
        if self.code == "us":
            return value.strftime("%m/%d/%Y")
        if self.code == "uk":
            return value.strftime("%d %B %Y")
        return value.strftime("%d.%m.%Y")

    def number(self, value: Decimal, places: int = 2) -> str:
        text = f"{value:,.{places}f}"
        if self.code == "de":
            text = text.replace(",", "X").replace(".", ",").replace("X", ".")
        return text

    def money(self, value: Decimal) -> str:
        return f"{self.number(value)} {self.currency}"


LOCALES = (
    Locale("us", "en", "USD", (Decimal(0), Decimal("7.25"))),
    Locale("uk", "en", "GBP", (Decimal(20), Decimal(5))),
    Locale("de", "de", "EUR", (Decimal(19), Decimal(7))),
)


def _q(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def make_invoice(rng: random.Random, number: int) -> tuple[InvoiceData, Locale]:
    """A consistent invoice: line amounts, tax and totals add up."""
    locale = rng.choice(LOCALES)
    vendor_name, vendor_address, vendor_tax = rng.choice(VENDORS)
    customer_name, customer_address = rng.choice(CUSTOMERS)
    issued = dt.date(2026, 1, 1) + dt.timedelta(days=rng.randrange(330))

    lines: list[InvoiceLineItem] = []
    subtotal = tax = Decimal(0)
    for description, code, unit, (low, high) in rng.sample(ITEMS, rng.randint(1, 6)):
        quantity = Decimal(rng.choice([1, 2, 3, 5, 10, 12, 25, 40, 100]))
        price = _q(Decimal(str(rng.uniform(low, high))))
        rate = rng.choice(locale.rates)
        amount = _q(quantity * price)
        subtotal += amount
        tax += _q(amount * rate / 100)
        lines.append(
            InvoiceLineItem(
                description=description,
                product_code=code if rng.random() < 0.8 else None,
                quantity=float(quantity),
                unit=unit,
                unit_price=float(price),
                tax_rate=float(rate),
                amount=float(amount),
            )
        )
    total = subtotal + tax
    deposit = _q(total * Decimal("0.3")) if rng.random() < 0.2 else None

    data = InvoiceData(
        document_type="invoice",
        is_invoice=True,
        invoice_number=f"{rng.choice(['INV', 'RE', 'NW', 'GX'])}-2026-{number:04d}",
        invoice_date=issued,
        due_date=issued + dt.timedelta(days=30) if rng.random() < 0.7 else None,
        currency=locale.currency,
        purchase_order_number=f"PO-{rng.randint(10000, 99999)}"
        if rng.random() < 0.5
        else None,
        payment_terms=rng.choice(["Net 30", "Net 14", "Due on receipt", None]),
        vendor=Party(
            name=vendor_name,
            address=vendor_address,
            tax_id=vendor_tax,
            email=f"billing@{vendor_name.split()[0].lower().replace('ü', 'ue')}.example"
            if rng.random() < 0.6
            else None,
            phone=f"+49 30 {rng.randint(1000000, 9999999)}"
            if rng.random() < 0.4
            else None,
        ),
        customer=Party(
            name=customer_name,
            address=customer_address,
            tax_id=None,
            email=None,
            phone=None,
        ),
        line_items=lines,
        subtotal=float(subtotal),
        tax_amount=float(tax),
        total_amount=float(total),
        amount_due=float(total - deposit) if deposit is not None else None,
        uncertain_fields=[],
    )
    return data, locale


def _rows(data: InvoiceData, locale: Locale) -> list[list[str]]:
    """The invoice as label/value rows (used by every format)."""
    t = LABELS[locale.language]
    assert data.invoice_date is not None
    rows = [
        [t["title"]],
        [data.vendor.name or ""],
        *[[line] for line in (data.vendor.address or "").split(", ")],
    ]
    rows.append([f"{t['vat']}: {data.vendor.tax_id}"])
    if data.vendor.email:
        rows.append([f"{t['email']}: {data.vendor.email}"])
    if data.vendor.phone:
        rows.append([f"{t['phone']}: {data.vendor.phone}"])
    rows += [
        [""],
        [t["bill_to"]],
        [data.customer.name or ""],
        [data.customer.address or ""],
        [""],
    ]
    rows.append([t["number"], data.invoice_number or ""])
    rows.append([t["date"], locale.date(data.invoice_date)])
    if data.due_date:
        rows.append([t["due"], locale.date(data.due_date)])
    if data.purchase_order_number:
        rows.append([t["po"], data.purchase_order_number])
    if data.payment_terms:
        rows.append([t["terms"], data.payment_terms])
    rows.append([""])
    rows.append(
        [
            t[k]
            for k in ("description", "code", "qty", "unit", "price", "rate", "amount")
        ]
    )
    for item in data.line_items:
        rows.append(
            [
                item.description or "",
                item.product_code or "",
                locale.number(Decimal(str(item.quantity)), 0),
                item.unit or "",
                locale.number(Decimal(str(item.unit_price))),
                locale.number(
                    Decimal(str(item.tax_rate)),
                    2 if Decimal(str(item.tax_rate)) % 1 else 0,
                ),
                locale.number(Decimal(str(item.amount))),
            ]
        )
    rows.append([""])
    for key, value in (
        ("subtotal", data.subtotal),
        ("tax", data.tax_amount),
        ("total", data.total_amount),
    ):
        rows.append([t[key], locale.money(Decimal(str(value)))])
    if data.amount_due is not None and data.total_amount is not None:
        deposit = Decimal(str(data.total_amount)) - Decimal(str(data.amount_due))
        rows.append([t["deposit"], locale.money(deposit)])
        rows.append([t["due_amount"], locale.money(Decimal(str(data.amount_due)))])
    return rows


def render_pdf(data: InvoiceData, locale: Locale) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - 60
    columns = [50, 200, 270, 310, 355, 430, 480]
    table = False
    for row in _rows(data, locale):
        if y < 60:
            pdf.showPage()
            y = height - 60
        if len(row) == 7:
            pdf.setFont("Helvetica-Bold" if not table else "Helvetica", 8)
            for x, cell in zip(columns, row, strict=True):
                pdf.drawString(x, y, cell)
            table = True
        elif len(row) == 2:
            pdf.setFont("Helvetica", 10)
            pdf.drawString(50, y, f"{row[0]}:")
            pdf.drawString(200, y, row[1])
        else:
            is_title = row[0] in ("INVOICE", "RECHNUNG")
            pdf.setFont(
                "Helvetica-Bold" if is_title else "Helvetica", 16 if is_title else 10
            )
            pdf.drawString(50, y, row[0])
            table = False
        y -= 22 if row[0] in ("INVOICE", "RECHNUNG") else 14
    pdf.save()
    return buffer.getvalue()


def _scan_pages(
    data: InvoiceData, locale: Locale, rng: random.Random
) -> list[Image.Image]:
    """Rasterise the PDF like a scanner would: grey, noisy, slightly tilted."""
    document = pdfium.PdfDocument(render_pdf(data, locale))
    pages = []
    for index in range(len(document)):
        image = document[index].render(scale=200 / 72).to_pil().convert("L")
        image = image.rotate(
            rng.uniform(-0.8, 0.8), fillcolor=255, resample=Image.Resampling.BICUBIC
        )
        noise = Image.effect_noise(image.size, 18).convert("L")
        image = Image.blend(image, noise, 0.08).filter(ImageFilter.GaussianBlur(0.4))
        pages.append(image)
    return pages


def render_scan(data: InvoiceData, locale: Locale, rng: random.Random) -> bytes:
    """A scanned PDF: image-only pages, no text layer."""
    pages = [page.convert("RGB") for page in _scan_pages(data, locale, rng)]
    buffer = io.BytesIO()
    pages[0].save(
        buffer, format="PDF", save_all=True, append_images=pages[1:], resolution=200
    )
    return buffer.getvalue()


def render_tiff(data: InvoiceData, locale: Locale, rng: random.Random) -> bytes:
    """A scanned TIFF, as fax and document scanners produce: greyscale,
    LZW-compressed, one frame per page."""
    pages = _scan_pages(data, locale, rng)
    buffer = io.BytesIO()
    pages[0].save(
        buffer,
        format="TIFF",
        save_all=True,
        append_images=pages[1:],
        compression="tiff_lzw",
        dpi=(200, 200),
    )
    return buffer.getvalue()


def render_xlsx(data: InvoiceData, locale: Locale) -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = LABELS[locale.language]["title"].title()
    for row in _rows(data, locale):
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def render_csv(data: InvoiceData, locale: Locale) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";" if locale.code == "de" else ",")
    for row in _rows(data, locale):
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8")


EXTENSIONS = {
    "pdf": ".pdf",
    "scan": ".pdf",
    "tiff": ".tiff",
    "xlsx": ".xlsx",
    "csv": ".csv",
}


def generate(
    dataset: Path, count: int, seed: int = 2026, expectations: Path | None = None
) -> list[Path]:
    """Write ``count`` cases, each named after its invoice number, to
    ``dataset/documents`` and ``dataset/ground_truth``, plus validation
    expectations to ``expectations`` (default: ``validation_expectations``
    next to the dataset). Formats rotate evenly. Returns the document paths."""
    rng = random.Random(seed)
    documents, labels = dataset / "documents", dataset / "ground_truth"
    expectations = expectations or dataset.parent / "validation_expectations"
    for directory in (documents, labels, expectations):
        directory.mkdir(parents=True, exist_ok=True)
    written = []
    for number in range(1, count + 1):
        data, locale = make_invoice(rng, number)
        kind = FORMATS[(number - 1) % len(FORMATS)]
        assert data.invoice_number is not None
        name = data.invoice_number
        if kind == "pdf":
            content = render_pdf(data, locale)
        elif kind == "scan":
            content = render_scan(data, locale, rng)
        elif kind == "tiff":
            content = render_tiff(data, locale, rng)
        elif kind == "xlsx":
            content = render_xlsx(data, locale)
        else:
            content = render_csv(data, locale)
        path = documents / f"{name}{EXTENSIONS[kind]}"
        path.write_bytes(content)
        label = {
            "format": kind,
            "locale": locale.code,
            "expected": data.model_dump(mode="json"),
        }
        write_json(labels / f"{name}.json", label)
        write_json(expectations / f"{name}.json", expectation_for(data).to_json())
        written.append(path)
    return written
