"""
Rendering a processed document's pages and sheets as text for the model,
and splitting long documents into parts that fit one extraction request.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.models.document_page import DocumentPage
from app.models.document_sheet import DocumentSheet


class DocumentTooLargeError(Exception):
    """The document needs more extraction requests than allowed."""


class EmptyDocumentError(Exception):
    """The document has no text or cell data to extract from."""


@dataclass(frozen=True)
class Section:
    """One page or sheet: a marker line and its text lines.

    A section too long for one part continues under ``continued``; a sheet
    also repeats its first row (usually the column headings) there, so every
    part can be read on its own.
    """

    header: str
    continued: str
    lines: tuple[str, ...]
    repeat_first_line: bool = False

    def text(self) -> str:
        return "\n".join((self.header, *self.lines))


def _cell(value: Any) -> str:
    return "" if value is None else str(value)


def document_sections(
    pages: Sequence[DocumentPage], sheets: Sequence[DocumentSheet]
) -> list[Section]:
    """Pages, then sheets, in order; empty ones are skipped."""
    sections: list[Section] = []
    for page in pages:
        if page.text.strip():
            sections.append(
                Section(
                    header=f"--- Page {page.page_number} ---",
                    continued=f"--- Page {page.page_number} (continued) ---",
                    lines=tuple(page.text.split("\n")),
                )
            )
    for sheet in sheets:
        if not sheet.rows:
            continue
        name = f"Sheet {sheet.sheet_number}: {sheet.name}"
        sections.append(
            Section(
                header=f"--- {name} ---",
                continued=f"--- {name} (continued; first row repeated) ---",
                lines=tuple(
                    " | ".join(_cell(value) for value in row) for row in sheet.rows
                ),
                repeat_first_line=True,
            )
        )
    if not sections:
        raise EmptyDocumentError("The document has no extracted text to read.")
    return sections


def render_document(
    pages: Sequence[DocumentPage], sheets: Sequence[DocumentSheet]
) -> str:
    """The whole document as one text, with page and sheet markers."""
    return "\n\n".join(section.text() for section in document_sections(pages, sheets))


def _pieces(section: Section, limit: int) -> list[str]:
    """The section as one piece if it fits, otherwise as consecutive pieces
    of at most ``limit`` characters, each starting with a marker line."""
    whole = section.text()
    if len(whole) <= limit:
        return [whole]

    pieces: list[str] = []
    head = [section.header]
    current: list[str] = []
    size = len(section.header)
    first = section.lines[0] if section.repeat_first_line and section.lines else None
    # Repeat the first row only if it leaves at least half of each part.
    continued_head = [section.continued]
    if first is not None and len(section.continued) + len(first) + 1 <= limit // 2:
        continued_head.append(first)
    continued_size = sum(len(line) + 1 for line in continued_head) - 1
    # A single line longer than a part is cut into slices that fit.
    room = max(1, limit - continued_size - 1)

    def flush() -> None:
        nonlocal head, current, size
        if current:
            pieces.append("\n".join((*head, *current)))
        head, current, size = continued_head, [], continued_size

    for line in section.lines:
        slices = [line[i : i + room] for i in range(0, len(line), room)] or [""]
        for piece in slices:
            if current and size + 1 + len(piece) > limit:
                flush()
            current.append(piece)
            size += 1 + len(piece)
    flush()
    return pieces


def split_document(sections: Sequence[Section], chunk_chars: int) -> list[str]:
    """Pack sections, in order, into parts of at most ``chunk_chars``
    characters. Parts break between pages and sheets where possible, and
    inside one only when it alone is too long. Nothing is dropped."""
    parts: list[str] = []
    current: list[str] = []
    size = 0
    for section in sections:
        for piece in _pieces(section, chunk_chars):
            added = len(piece) + (2 if current else 0)
            if current and size + added > chunk_chars:
                parts.append("\n\n".join(current))
                current, size = [], 0
                added = len(piece)
            current.append(piece)
            size += added
    if current:
        parts.append("\n\n".join(current))
    return parts
