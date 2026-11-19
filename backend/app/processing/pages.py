"""
Common result type for all extractors.
"""

from dataclasses import dataclass

from app.models.document_page import ExtractionMethod


@dataclass(frozen=True)
class ExtractedPage:
    """Text extracted from one page (PDF page or image frame)."""

    page_number: int
    text: str
    method: ExtractionMethod
    needs_ocr: bool = False
    # Mean OCR word confidence, 0-100; None for text-layer pages.
    confidence: float | None = None

    @property
    def char_count(self) -> int:
        return len(self.text)
