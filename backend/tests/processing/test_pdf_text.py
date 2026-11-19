from pathlib import Path

import pytest

from app.processing.pdf_text import extract_pdf_pages, normalize_text
from tests.helpers import concat_pdfs, make_pdf, make_scanned_pdf, make_text_pdf

INVOICE = "Invoice 2026-001\nCustomer: ACME GmbH\nTotal due: 1,250.00 EUR"


def write(tmp_path: Path, data: bytes) -> Path:
    path = tmp_path / "doc.pdf"
    path.write_bytes(data)
    return path


def test_extracts_text_of_every_page(tmp_path: Path) -> None:
    pdf = make_text_pdf(INVOICE, "Page two\nTerms and conditions apply.")

    pages = extract_pdf_pages(write(tmp_path, pdf))

    assert [p.page_number for p in pages] == [1, 2]
    assert pages[0].text == INVOICE
    assert pages[1].text == "Page two\nTerms and conditions apply."
    assert pages[0].char_count == len(INVOICE)
    assert not any(p.needs_ocr for p in pages)


def test_scanned_pages_are_flagged_for_ocr(tmp_path: Path) -> None:
    pages = extract_pdf_pages(write(tmp_path, make_scanned_pdf(pages=2)))

    assert [(p.text, p.needs_ocr) for p in pages] == [("", True), ("", True)]


def test_mixed_digital_and_scanned_pages(tmp_path: Path) -> None:
    pdf = concat_pdfs(make_text_pdf(INVOICE), make_scanned_pdf())

    pages = extract_pdf_pages(write(tmp_path, pdf))

    assert [p.needs_ocr for p in pages] == [False, True]


def test_blank_page_without_images_is_not_sent_to_ocr(tmp_path: Path) -> None:
    pages = extract_pdf_pages(write(tmp_path, make_pdf()))

    assert pages[0].text == ""
    assert pages[0].needs_ocr is False


def test_short_digital_page_is_not_sent_to_ocr(tmp_path: Path) -> None:
    pages = extract_pdf_pages(write(tmp_path, make_text_pdf("3")))

    assert pages[0].text == "3"
    assert pages[0].needs_ocr is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a\x00b", "ab"),
        ("line1\r\nline2\rline3", "line1\nline2\nline3"),
        ("trailing   \nspaces\t\n", "trailing\nspaces"),
        ("a\n\n\n\n\nb", "a\n\nb"),
        ("  \n  padded  \n  ", "padded"),
    ],
)
def test_normalize_text(raw: str, expected: str) -> None:
    assert normalize_text(raw) == expected


def test_page_without_resources_has_no_images() -> None:
    from pypdf import PageObject
    from pypdf.generic import NameObject

    from app.processing.pdf_text import _has_images

    page = PageObject.create_blank_page(width=100, height=100)
    page.pop(NameObject("/Resources"), None)

    assert _has_images(page) is False
