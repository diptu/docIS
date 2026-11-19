"""
Rendering a processed document's pages and sheets as text for the model.
"""

from collections.abc import Sequence
from typing import Any

from app.models.document_page import DocumentPage
from app.models.document_sheet import DocumentSheet


class DocumentTooLargeError(Exception):
    """The document text exceeds the configured extraction limit."""


class EmptyDocumentError(Exception):
    """The document has no text or cell data to extract from."""


def _cell(value: Any) -> str:
    return "" if value is None else str(value)


def render_document(
    pages: Sequence[DocumentPage],
    sheets: Sequence[DocumentSheet],
    max_chars: int,
) -> str:
    """Render extracted content with page and sheet markers.

    Never truncates: a document over ``max_chars`` raises instead, because
    silently dropping its end (often where the totals are) would produce a
    wrong extraction that looks right.
    """
    parts: list[str] = []
    for page in pages:
        if page.text.strip():
            parts.append(f"--- Page {page.page_number} ---\n{page.text}")
    for sheet in sheets:
        if not sheet.rows:
            continue
        lines = [" | ".join(_cell(value) for value in row) for row in sheet.rows]
        header = f"--- Sheet {sheet.sheet_number}: {sheet.name} ---"
        parts.append(header + "\n" + "\n".join(lines))

    if not parts:
        raise EmptyDocumentError("The document has no extracted text to read.")
    text = "\n\n".join(parts)
    if len(text) > max_chars:
        raise DocumentTooLargeError(
            f"Document text is {len(text):,} characters; the extraction limit is "
            f"{max_chars:,} (LLM_MAX_INPUT_CHARS)."
        )
    return text
