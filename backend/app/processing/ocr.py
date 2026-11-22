"""
Optical character recognition with Tesseract.

Scanned PDF pages are rendered to bitmaps with PDFium; images are read with
Pillow (every frame of a multi-page TIFF is its own page). All functions are
blocking; call them from a worker thread.
"""

import math
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
import pytesseract
from PIL import Image, ImageOps, ImageSequence

from app.models.document_page import ExtractionMethod
from app.processing.image_prep import enhance, rotate_upright
from app.processing.pages import ExtractedPage
from app.processing.pdf_text import normalize_text
from app.processing.progress import ProgressCallback, Stage, ignore_progress

POINTS_PER_INCH = 72
# Upscale images whose longest side is below this; Tesseract misreads small
# glyphs, and low-resolution photos/screenshots are common.
MIN_OCR_LONG_SIDE = 1500
# Trust Tesseract's page-orientation guess only above this confidence;
# guesses on sparse or noisy pages are unreliable.
MIN_ORIENTATION_CONFIDENCE = 2.0
# Orientation is checked only when a first read scores below this mean word
# confidence (sideways or upside-down pages score ~35; upright ones, even
# handwriting, well above 70), so upright pages pay no extra Tesseract run.
ORIENTATION_CHECK_BELOW_CONFIDENCE = 60.0


class OcrError(Exception):
    """OCR could not be performed (engine missing, timeout, bad input)."""


@dataclass(frozen=True)
class OcrConfig:
    """OCR engine settings."""

    languages: str = "eng"
    dpi: int = 300
    timeout_seconds: int = 120
    # Clean up images (noise, lighting, contrast, skew) and turn sideways or
    # upside-down pages upright before recognising text.
    enhance_images: bool = True
    detect_orientation: bool = True
    # Cap on rendered bitmap size, so an oversized PDF page cannot exhaust
    # memory (50 MP is A0 at roughly 200 DPI).
    max_render_pixels: int = 50_000_000


@dataclass(frozen=True)
class OcrResult:
    text: str
    confidence: float | None


def prepare_image(image: Image.Image, enhance_image: bool = True) -> Image.Image:
    """Normalise an image for OCR: EXIF orientation, transparent background,
    grayscale, then (optionally) noise, lighting, contrast and skew
    correction (see ``image_prep``), and finally a minimum size."""
    image = ImageOps.exif_transpose(image)
    if image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info:
        # Flatten onto white; otherwise transparent areas turn black.
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, "white")
        image = Image.alpha_composite(background, rgba)
    image = image.convert("L")
    if enhance_image:
        image = enhance(image)

    long_side = max(image.size)
    if 0 < long_side < MIN_OCR_LONG_SIDE:
        factor = math.ceil(MIN_OCR_LONG_SIDE / long_side)
        image = image.resize(
            (image.width * factor, image.height * factor), Image.Resampling.LANCZOS
        )
    return image


def _lines_from_data(data: dict[str, list[object]]) -> tuple[str, float | None]:
    """Rebuild text (lines and paragraphs) and mean confidence from word data."""
    paragraphs: dict[tuple[int, int], dict[int, list[str]]] = {}
    confidences: list[float] = []

    for i, word in enumerate(data["text"]):
        word = str(word).strip()
        confidence = float(str(data["conf"][i]))
        if not word or confidence < 0:
            continue
        paragraph = (int(str(data["block_num"][i])), int(str(data["par_num"][i])))
        line = int(str(data["line_num"][i]))
        paragraphs.setdefault(paragraph, {}).setdefault(line, []).append(word)
        confidences.append(confidence)

    text = "\n\n".join(
        "\n".join(" ".join(words) for words in lines.values())
        for lines in paragraphs.values()
    )
    mean = round(sum(confidences) / len(confidences), 1) if confidences else None
    return normalize_text(text), mean


def page_rotation(image: Image.Image, config: OcrConfig) -> int:
    """Clockwise degrees (0, 90, 180, 270) that turn the page upright,
    according to Tesseract's orientation detection; 0 when unsure.

    Detection needs enough text and the ``osd`` model; any failure just
    means the page is read as it is.
    """
    try:
        osd = pytesseract.image_to_osd(
            image,
            config="--psm 0",
            output_type=pytesseract.Output.DICT,
            timeout=config.timeout_seconds,
        )
    except (
        pytesseract.TesseractError,
        pytesseract.TesseractNotFoundError,
        RuntimeError,
    ):
        return 0
    if float(osd.get("orientation_conf", 0)) < MIN_ORIENTATION_CONFIDENCE:
        return 0
    return int(osd.get("rotate", 0)) % 360


def ocr_image(
    image: Image.Image, config: OcrConfig, dpi: int | None = None
) -> OcrResult:
    """Run Tesseract on one image."""
    prepared = prepare_image(image, enhance_image=config.enhance_images)
    result = _recognise(prepared, config, dpi)
    if config.detect_orientation and (
        result.confidence is None
        or result.confidence < ORIENTATION_CHECK_BELOW_CONFIDENCE
    ):
        rotation = page_rotation(prepared, config)
        if rotation:
            upright = _recognise(rotate_upright(prepared, rotation), config, dpi)
            if (upright.confidence or 0) > (result.confidence or 0):
                result = upright
    return result


def _recognise(image: Image.Image, config: OcrConfig, dpi: int | None) -> OcrResult:
    options = f"--dpi {dpi}" if dpi else ""
    try:
        data = pytesseract.image_to_data(
            image,
            lang=config.languages,
            config=options,
            output_type=pytesseract.Output.DICT,
            timeout=config.timeout_seconds,
        )
    except pytesseract.TesseractNotFoundError as exc:
        raise OcrError("Tesseract is not installed or not on PATH.") from exc
    except RuntimeError as exc:  # pytesseract signals timeouts this way
        raise OcrError(f"OCR failed: {exc}") from exc
    text, confidence = _lines_from_data(data)
    return OcrResult(text=text, confidence=confidence)


def _render_scale(width_pt: float, height_pt: float, config: OcrConfig) -> float:
    scale = config.dpi / POINTS_PER_INCH
    pixels = width_pt * scale * height_pt * scale
    if pixels > config.max_render_pixels:
        scale *= math.sqrt(config.max_render_pixels / pixels)
    return scale


def ocr_pdf_pages(
    path: Path,
    page_numbers: list[int],
    config: OcrConfig,
    progress: ProgressCallback = ignore_progress,
) -> list[ExtractedPage]:
    """Render the given 1-based PDF pages and OCR them."""
    total = len(page_numbers)
    progress(Stage.OCR, 0, total)
    pdf = pdfium.PdfDocument(path)
    try:
        results = []
        for done, number in enumerate(page_numbers, start=1):
            page = pdf[number - 1]
            try:
                width, height = page.get_size()
                scale = _render_scale(width, height, config)
                bitmap = page.render(scale=scale).to_pil()
            finally:
                page.close()
            result = ocr_image(bitmap, config, dpi=round(scale * POINTS_PER_INCH))
            results.append(
                ExtractedPage(
                    page_number=number,
                    text=result.text,
                    method=ExtractionMethod.OCR,
                    confidence=result.confidence,
                )
            )
            progress(Stage.OCR, done, total)
        return results
    finally:
        pdf.close()


def _frames(image: Image.Image) -> Iterator[Image.Image]:
    for frame in ImageSequence.Iterator(image):
        yield frame.copy()


def ocr_image_file(
    path: Path, config: OcrConfig, progress: ProgressCallback = ignore_progress
) -> list[ExtractedPage]:
    """OCR an image file; each frame (e.g. TIFF page) becomes a page."""
    with Image.open(path) as image:
        total = getattr(image, "n_frames", 1)
        progress(Stage.OCR, 0, total)
        pages = []
        for number, frame in enumerate(_frames(image), start=1):
            result = ocr_image(frame, config)
            pages.append(
                ExtractedPage(
                    page_number=number,
                    text=result.text,
                    method=ExtractionMethod.OCR,
                    confidence=result.confidence,
                )
            )
            progress(Stage.OCR, number, total)
        return pages
