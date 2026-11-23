from pathlib import Path

import pytesseract
import pytest
from PIL import Image, ImageChops

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
    page_rotation,
    prepare_image,
)
from evaluation.ocr_benchmark import (
    CASES,
    KEY_VALUES,
    LAYOUTS,
    degrade,
    make_page,
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


def lines(text: str) -> list[str]:
    """Text lines, ignoring blank lines between Tesseract's paragraphs."""
    return [line for line in text.splitlines() if line.strip()]


LONG_PAGE = [
    "ACME GmbH Invoice 2026-001",
    "Total due: 1,250.00 EUR",
    "Payment within 30 days",
    "Bank transfer to account 12345678",
]


@requires_tesseract
@pytest.mark.parametrize("angle", [10.0, -14.0])
def test_tilted_photos_are_deskewed_before_reading(angle: float) -> None:
    image = make_text_image(LONG_PAGE).rotate(
        angle, expand=True, fillcolor="white", resample=Image.Resampling.BICUBIC
    )

    assert lines(ocr_image(image, CONFIG).text) == LONG_PAGE
    # Without clean-up, Tesseract loses the text at this tilt.
    plain = OcrConfig(enhance_images=False, detect_orientation=False)
    assert lines(ocr_image(image, plain).text) != LONG_PAGE


@requires_tesseract
@pytest.mark.parametrize(
    "turn",
    [Image.Transpose.ROTATE_90, Image.Transpose.ROTATE_180, Image.Transpose.ROTATE_270],
)
def test_sideways_and_upside_down_pages_are_turned_upright(
    turn: Image.Transpose,
) -> None:
    result = ocr_image(make_text_image(LONG_PAGE).transpose(turn), CONFIG)

    assert lines(result.text) == LONG_PAGE
    assert result.confidence is not None and result.confidence > 80


@requires_tesseract
def test_shadowed_photo_is_read(tmp_path: Path) -> None:
    image = make_text_image(INVOICE).convert("L")
    shadow = Image.new("L", image.size, 255)
    shadow.paste(70, (image.width // 2, 0, image.width, image.height))
    darkened = ImageChops.multiply(image, shadow)

    assert ocr_image(darkened, CONFIG).text == "\n".join(INVOICE)


@requires_tesseract
def test_upright_pages_skip_orientation_detection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A confident first read is kept; no second Tesseract run."""

    def fail(*_: object, **__: object) -> None:
        raise AssertionError("orientation detection must not run")

    monkeypatch.setattr(ocr.pytesseract, "image_to_osd", fail)

    assert ocr_image(make_text_image(INVOICE), CONFIG).text == "\n".join(INVOICE)


@requires_tesseract
def test_failed_orientation_detection_keeps_the_first_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_: object, **__: object) -> None:
        raise pytesseract.TesseractError(1, "Too few characters")

    monkeypatch.setattr(ocr.pytesseract, "image_to_osd", fail)
    upside_down = make_text_image(LONG_PAGE).transpose(Image.Transpose.ROTATE_180)

    result = ocr_image(upside_down, CONFIG)  # does not raise

    assert lines(result.text) != LONG_PAGE


# ---------------------------------------------------- no Tesseract needed


@pytest.mark.parametrize(
    ("osd", "expected"),
    [
        ({"rotate": 90, "orientation_conf": 5.0}, 90),
        ({"rotate": 180, "orientation_conf": 0.5}, 0),  # too unsure
        ({"rotate": 0, "orientation_conf": 9.0}, 0),
    ],
)
def test_page_rotation_trusts_only_confident_guesses(
    monkeypatch: pytest.MonkeyPatch, osd: dict[str, float], expected: int
) -> None:
    monkeypatch.setattr(ocr.pytesseract, "image_to_osd", lambda *_, **__: osd)

    assert page_rotation(Image.new("L", (10, 10)), CONFIG) == expected


def test_page_rotation_is_zero_without_tesseract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(*_: object, **__: object) -> None:
        raise pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(ocr.pytesseract, "image_to_osd", missing)

    assert page_rotation(Image.new("L", (10, 10)), CONFIG) == 0


def test_prepare_image_can_skip_enhancement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ocr, "enhance", lambda _: pytest.fail("enhancement must not run")
    )

    assert prepare_image(Image.new("RGB", (2000, 100)), enhance_image=False).mode == "L"


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


# ------------------------------------------- poor-quality photos (regression)

# Every case of the OCR benchmark (python -m evaluation ocr) that the full
# clean-up reads completely, on both a sparse page (a few lines: receipts,
# short invoices) and a dense one. Before the fix, dark or grainy sparse
# pages came back empty.
POOR_QUALITY = [
    "low resolution",
    "tilted 4°",
    "blurred",
    "shadow",
    "dark",
    "dark, grainy",
    "dark, very grainy",
    "very dark",
    "grainy, white paper",
    "shadow, grainy, tilted",
    "phone photo",
    "dark, grainy, low-res, tilted",
    "harsh: tiny, dark, grainy",
]


@requires_tesseract
@pytest.mark.parametrize("layout", ["sparse", "dense"])
@pytest.mark.parametrize("case", POOR_QUALITY)
def test_poor_quality_photos_are_read(layout: str, case: str) -> None:
    photo = degrade(make_page(LAYOUTS[layout]), CASES[case])

    result = ocr_image(photo, CONFIG)

    missing = [value for value in KEY_VALUES if value not in result.text]
    assert missing == []
    assert result.confidence is not None and result.confidence > 80


@requires_tesseract
def test_clean_up_rescues_dark_grainy_sparse_photos() -> None:
    """Without clean-up Tesseract reads nothing from this photo."""
    photo = degrade(make_page(LAYOUTS["sparse"]), CASES["dark, very grainy"])
    plain = OcrConfig(enhance_images=False, detect_orientation=False)

    assert ocr_image(photo, plain).text == ""
    assert all(value in ocr_image(photo, CONFIG).text for value in KEY_VALUES)


def test_small_images_are_enlarged_before_clean_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clean-up on 15-pixel glyphs erodes them; it runs on the enlarged copy."""
    sizes: list[tuple[int, int]] = []

    def record(image: Image.Image) -> Image.Image:
        sizes.append(image.size)
        return image

    monkeypatch.setattr(ocr, "enhance", record)

    prepare_image(Image.new("RGB", (500, 200), "white"))

    assert sizes == [(1500, 600)]
