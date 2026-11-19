"""
Content extraction entry point: picks the right extractor per file type.
"""

from dataclasses import dataclass, field
from pathlib import Path

from app.processing.ocr import OcrConfig, ocr_image_file, ocr_pdf_pages
from app.processing.pages import ExtractedPage
from app.processing.pdf_text import extract_pdf_pages
from app.processing.progress import ProgressCallback, ignore_progress
from app.processing.spreadsheets import (
    ExtractedSheet,
    parse_csv,
    parse_xls,
    parse_xlsx,
)

PDF_TYPE = "application/pdf"
IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/tiff"})
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
XLS_TYPE = "application/vnd.ms-excel"
CSV_TYPE = "text/csv"
PROCESSABLE_TYPES = frozenset({PDF_TYPE, XLSX_TYPE, XLS_TYPE, CSV_TYPE}) | IMAGE_TYPES


@dataclass(frozen=True)
class ExtractionResult:
    """Everything extracted from one document."""

    pages: list[ExtractedPage] = field(default_factory=list)
    sheets: list[ExtractedSheet] = field(default_factory=list)


def _extract_pdf(
    path: Path, config: OcrConfig, progress: ProgressCallback
) -> list[ExtractedPage]:
    pages = extract_pdf_pages(path, progress)
    scanned = [page.page_number for page in pages if page.needs_ocr]
    if not scanned:
        return pages
    ocred = {
        page.page_number: page
        for page in ocr_pdf_pages(path, scanned, config, progress)
    }
    return [ocred.get(page.page_number, page) for page in pages]


def extract_content(
    path: Path,
    media_type: str,
    config: OcrConfig,
    file_name: str = "",
    progress: ProgressCallback = ignore_progress,
) -> ExtractionResult:
    """Extract text and/or structured data from a stored document.

    PDFs use their text layer; only pages without one (scans) are OCRed.
    Images are OCRed frame by frame. Workbooks are parsed sheet by sheet; a
    CSV becomes one sheet named after ``file_name`` (without extension).
    Blocking; run in a worker thread.
    """
    if media_type == PDF_TYPE:
        return ExtractionResult(pages=_extract_pdf(path, config, progress))
    if media_type in IMAGE_TYPES:
        return ExtractionResult(pages=ocr_image_file(path, config, progress))
    if media_type == XLSX_TYPE:
        return ExtractionResult(sheets=parse_xlsx(path, progress=progress))
    if media_type == XLS_TYPE:
        return ExtractionResult(sheets=parse_xls(path, progress=progress))
    if media_type == CSV_TYPE:
        name = Path(file_name).stem or "Sheet1"
        return ExtractionResult(sheets=parse_csv(path, name=name, progress=progress))
    raise ValueError(f"No extractor for media type {media_type!r}")
