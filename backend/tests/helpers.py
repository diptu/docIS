"""Builders for small, genuine sample files used in tests."""

import io
import zipfile
from pathlib import Path

import openpyxl
from PIL import Image
from pypdf import PdfWriter

FIXTURES = Path(__file__).parent / "fixtures"
XLS_BYTES = (FIXTURES / "sample.xls").read_bytes()
CSV_BYTES = b"name,amount\nalice,10\nbob,20\n"


def make_pdf(pages: int = 1, password: str | None = None) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    if password:
        writer.encrypt(password)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def make_image(fmt: str, size: tuple[int, int] = (8, 8)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buffer, format=fmt)
    return buffer.getvalue()


def make_xlsx() -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(["name", "amount"])
    sheet.append(["alice", 10])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def make_zip(parts: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def truncate(data: bytes, keep: float = 0.5) -> bytes:
    return data[: int(len(data) * keep)]


PDF_BYTES = make_pdf()
JPEG_BYTES = make_image("JPEG")
PNG_BYTES = make_image("PNG")
TIFF_BYTES = make_image("TIFF")
XLSX_BYTES = make_xlsx()


def make_text_pdf(*pages: str) -> bytes:
    """A digital PDF with one page per string (lines split on newlines)."""
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    for page_text in pages:
        y = 750
        for line in page_text.splitlines():
            pdf.drawString(72, y, line)
            y -= 16
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def make_scanned_pdf(pages: int = 1) -> bytes:
    """An image-only PDF, like a scanner produces (no text layer)."""
    images = [Image.new("RGB", (200, 260), "white") for _ in range(pages)]
    buffer = io.BytesIO()
    images[0].save(buffer, format="PDF", save_all=True, append_images=images[1:])
    return buffer.getvalue()


def concat_pdfs(*documents: bytes) -> bytes:
    from pypdf import PdfReader

    writer = PdfWriter()
    for document in documents:
        for page in PdfReader(io.BytesIO(document)).pages:
            writer.add_page(page)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


# ----------------------------------------------------------------- OCR

import os  # noqa: E402
import shutil  # noqa: E402

import pytest  # noqa: E402
from PIL import ImageDraw, ImageFont  # noqa: E402

# Real-OCR tests need the Tesseract binary. They are skipped locally if it
# is missing, but never in CI, where a missing engine must fail the build.
requires_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None and not os.environ.get("CI"),
    reason="Tesseract is not installed",
)


def make_text_image(
    lines: list[str],
    size: tuple[int, int] = (1400, 500),
    mode: str = "RGB",
) -> Image.Image:
    """Black text on white (or on transparent, for mode RGBA)."""
    background = (0, 0, 0, 0) if mode == "RGBA" else "white"
    image = Image.new(mode, size, background)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=40)
    for i, line in enumerate(lines):
        draw.text((60, 60 + i * 80), line, fill="black", font=font)
    return image


def encode(image: Image.Image, fmt: str, **options: object) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **options)
    return buffer.getvalue()


def make_scanned_text_pdf(*pages: list[str]) -> bytes:
    """An image-only PDF whose pages contain rendered (not selectable) text."""
    images = [make_text_image(lines) for lines in pages]
    return encode(
        images[0], "PDF", save_all=True, append_images=images[1:], resolution=150
    )


# --------------------------------------------------------- spreadsheets

MULTI_SHEET_XLS_BYTES = (FIXTURES / "multi_sheet.xls").read_bytes()


def make_workbook(
    sheets: dict[str, list[list[object]]], hidden: tuple[str, ...] = ()
) -> bytes:
    """An XLSX workbook with the given sheets (name -> rows)."""
    workbook = openpyxl.Workbook()
    assert workbook.active is not None
    workbook.remove(workbook.active)
    for name, rows in sheets.items():
        sheet = workbook.create_sheet(name)
        for row in rows:
            sheet.append(row)
        if name in hidden:
            sheet.sheet_state = "hidden"
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
