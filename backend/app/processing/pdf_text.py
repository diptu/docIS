"""
Text extraction from digital (text-layer) PDFs.
"""

import logging
import re
from pathlib import Path

from pypdf import PageObject, PdfReader

from app.models.document_page import ExtractionMethod
from app.processing.pages import ExtractedPage
from app.processing.progress import ProgressCallback, Stage, ignore_progress

logging.getLogger("pypdf").setLevel(logging.ERROR)

# A page with fewer visible characters than this is treated as having no
# usable text layer.
MIN_TEXT_CHARS = 20

_TRAILING_SPACE = re.compile(r"[ \t]+$", re.MULTILINE)
_EXCESS_BLANK_LINES = re.compile(r"\n{3,}")


def normalize_text(raw: str) -> str:
    """Clean extracted text for storage.

    Removes NUL characters (PostgreSQL text cannot store them), unifies line
    endings, strips trailing whitespace and collapses runs of blank lines.
    """
    text = raw.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = _TRAILING_SPACE.sub("", text)
    text = _EXCESS_BLANK_LINES.sub("\n\n", text)
    return text.strip()


def _has_images(page: PageObject) -> bool:
    """True if the page draws any image XObject (typical of scans)."""
    resources = page.get("/Resources")
    if resources is None:
        return False
    xobjects = resources.get_object().get("/XObject")
    if xobjects is None:
        return False
    return any(
        xobject.get_object().get("/Subtype") == "/Image"
        for xobject in xobjects.get_object().values()
    )


def _visible_chars(text: str) -> int:
    return sum(1 for ch in text if not ch.isspace())


def extract_pdf_pages(
    path: Path, progress: ProgressCallback = ignore_progress
) -> list[ExtractedPage]:
    """Extract the text layer of every page of a PDF.

    A page is flagged ``needs_ocr`` when it has almost no text but does
    contain images, i.e. it is most likely a scan. Nearly empty pages without
    images (blank or separator pages) are not flagged.

    This is CPU-bound; call it from a worker thread.
    """
    reader = PdfReader(path, strict=False)
    total = len(reader.pages)
    progress(Stage.EXTRACTING_TEXT, 0, total)
    pages = []
    for number, page in enumerate(reader.pages, start=1):
        text = normalize_text(page.extract_text() or "")
        sparse = _visible_chars(text) < MIN_TEXT_CHARS
        pages.append(
            ExtractedPage(
                page_number=number,
                text=text,
                method=ExtractionMethod.TEXT_LAYER,
                needs_ocr=sparse and _has_images(page),
            )
        )
        progress(Stage.EXTRACTING_TEXT, number, total)
    return pages
