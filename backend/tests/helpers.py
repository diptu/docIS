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
