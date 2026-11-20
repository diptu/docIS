from pathlib import Path

import pytesseract
import pytest
from PIL import Image

from app.models.document_page import ExtractionMethod
from app.processing import ocr
from app.processing.extract import extract_content
from app.processing.ocr import (
    OcrConfig,
    OcrError,
    _lines_from_data,
    _render_scale,
    ocr_image,
    ocr_image_file,
    prepare_image,
)
from tests.support.helpers import (
    concat_pdfs,
    encode,
    make_scanned_text_pdf,
    make_text_image,
    make_text_pdf,
    requires_tesseract,
)

CONFIG = OcrConfig()
INVOICE = ["ACME GmbH Invoice 2026-001", "Total due: 1,250.00 EUR"]


def write(tmp_path: Path, data: bytes, name: str = "file") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


# ---------------------------------------------------- real Tesseract


@requires_tesseract
@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "TIFF"])
def test_ocr_image_formats(tmp_path: Path, fmt: str) -> None:
    path = write(tmp_path, encode(make_text_image(INVOICE), fmt))

    pages = ocr_image_file(path, CONFIG)

    assert len(pages) == 1
    assert pages[0].text == "\n".join(INVOICE)
    assert pages[0].method == ExtractionMethod.OCR
    assert pages[0].needs_ocr is False
    assert pages[0].confidence is not None and pages[0].confidence > 80


@requires_tesseract
def test_multi_page_tiff_gives_one_page_per_frame(tmp_path: Path) -> None:
    first = make_text_image(["First page of the scan"])
    second = make_text_image(["Second page of the scan"])
    path = write(tmp_path, encode(first, "TIFF", save_all=True, append_images=[second]))

    pages = ocr_image_file(path, CONFIG)

    assert [(p.page_number, p.text) for p in pages] == [
        (1, "First page of the scan"),
        (2, "Second page of the scan"),
    ]


@requires_tesseract
def test_transparent_png_is_read(tmp_path: Path) -> None:
    """Black text on a transparent background must not become black-on-black."""
    image = make_text_image(["Transparent background text"], mode="RGBA")
    path = write(tmp_path, encode(image, "PNG"))

    assert ocr_image_file(path, CONFIG)[0].text == "Transparent background text"


@requires_tesseract
def test_scanned_pdf_pages_are_ocred_and_digital_pages_kept(tmp_path: Path) -> None:
    pdf = concat_pdfs(
        make_text_pdf("Digital page with a real text layer"),
        make_scanned_text_pdf(INVOICE),
    )

    pages = extract_content(write(tmp_path, pdf), "application/pdf", CONFIG).pages

    assert [(p.page_number, p.method, p.needs_ocr) for p in pages] == [
        (1, ExtractionMethod.TEXT_LAYER, False),
        (2, ExtractionMethod.OCR, False),
    ]
    assert pages[0].text == "Digital page with a real text layer"
    assert pages[0].confidence is None
    assert pages[1].text == "\n".join(INVOICE)
    assert pages[1].confidence is not None and pages[1].confidence > 80


@requires_tesseract
def test_blank_scan_yields_empty_text(tmp_path: Path) -> None:
    path = write(tmp_path, encode(Image.new("RGB", (800, 800), "white"), "PNG"))

    page = ocr_image_file(path, CONFIG)[0]

    assert (page.text, page.confidence) == ("", None)


# ---------------------------------------------------- no Tesseract needed


def test_prepare_image_flattens_transparency_onto_white() -> None:
    image = Image.new("RGBA", (2000, 100), (0, 0, 0, 0))

    prepared = prepare_image(image)

    assert prepared.mode == "L"
    assert prepared.getpixel((0, 0)) == 255


def test_prepare_image_upscales_small_images_only() -> None:
    assert prepare_image(Image.new("RGB", (500, 200))).size == (1500, 600)
    assert prepare_image(Image.new("RGB", (2000, 100))).size == (2000, 100)


def test_lines_from_data_rebuilds_paragraphs_and_confidence() -> None:
    data: dict[str, list[object]] = {
        "text": ["", "Hello", "world", "Next", "", "Para", "noise"],
        "conf": ["-1", "90", "80", "70", "-1", "60", "-1"],
        "block_num": [1, 1, 1, 1, 2, 2, 2],
        "par_num": [1, 1, 1, 1, 1, 1, 1],
        "line_num": [1, 1, 1, 2, 1, 1, 1],
    }

    text, confidence = _lines_from_data(data)

    assert text == "Hello world\nNext\n\nPara"
    assert confidence == 75.0


def test_lines_from_data_without_words() -> None:
    empty: dict[str, list[object]] = {
        "text": [""],
        "conf": ["-1"],
        "block_num": [1],
        "par_num": [1],
        "line_num": [1],
    }

    assert _lines_from_data(empty) == ("", None)


def test_render_scale_is_capped_for_huge_pages() -> None:
    a4 = _render_scale(595, 842, CONFIG)
    huge = _render_scale(14400, 14400, CONFIG)  # 200 x 200 inches

    assert a4 == pytest.approx(300 / 72)
    assert (14400 * huge) ** 2 <= CONFIG.max_render_pixels * 1.0001


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (pytesseract.TesseractNotFoundError(), "not installed"),
        (RuntimeError("Tesseract process timeout"), "timeout"),
    ],
)
def test_ocr_engine_errors_become_ocr_error(
    monkeypatch: pytest.MonkeyPatch, error: Exception, message: str
) -> None:
    def fail(*_: object, **__: object) -> None:
        raise error

    monkeypatch.setattr(ocr.pytesseract, "image_to_data", fail)

    with pytest.raises(OcrError, match=message):
        ocr_image(Image.new("RGB", (10, 10)), CONFIG)


def test_digital_pdf_does_not_invoke_ocr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_: object, **__: object) -> None:
        raise AssertionError("OCR must not run for digital pages")

    monkeypatch.setattr("app.processing.extract.ocr_pdf_pages", fail)
    path = write(tmp_path, make_text_pdf("Fully digital page with plenty of text"))

    pages = extract_content(path, "application/pdf", CONFIG).pages

    assert pages[0].method == ExtractionMethod.TEXT_LAYER


def test_unsupported_media_type_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="No extractor"):
        extract_content(write(tmp_path, b"PK"), "application/zip", CONFIG)
