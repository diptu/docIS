"""
Splitting extracted content into searchable chunks.

Chunks are sized for both search styles: small enough that a semantic
match points at a specific passage, large enough to keep context. Page text
is split on paragraph, then line, then word boundaries; spreadsheets are
chunked by rows, each chunk repeating the sheet's first (header) row.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

MAX_CHARS = 1200
OVERLAP_CHARS = 150
MAX_CHUNKS_PER_DOCUMENT = 1000


@dataclass(frozen=True)
class Chunk:
    content: str
    page_number: int | None = None
    sheet_number: int | None = None
    sheet_name: str | None = None


def _pieces(text: str) -> Iterable[str]:
    """Paragraphs, splitting any that are too long on lines, then words."""
    for paragraph in text.split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= MAX_CHARS:
            yield paragraph
            continue
        for line in paragraph.split("\n"):
            while len(line) > MAX_CHARS:
                cut = line.rfind(" ", 0, MAX_CHARS)
                cut = cut if cut > MAX_CHARS // 2 else MAX_CHARS
                yield line[:cut]
                line = line[cut:].lstrip()
            if line.strip():
                yield line


def split_text(text: str) -> list[str]:
    """Split text into chunks of at most MAX_CHARS, overlapping slightly so
    a phrase cut at a boundary is still found."""
    chunks: list[str] = []
    current = ""
    for piece in _pieces(text):
        if current and len(current) + 2 + len(piece) > MAX_CHARS:
            chunks.append(current)
            tail = current[-OVERLAP_CHARS:]
            space = tail.find(" ")
            tail = tail[space + 1 :] if space >= 0 else ""
            current = f"{tail}\n{piece}" if tail else piece
            if len(current) > MAX_CHARS:
                current = piece
        else:
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


def _row_text(row: Sequence[object]) -> str:
    return " | ".join("" if cell is None else str(cell) for cell in row).strip(" |")


def split_sheet(name: str, rows: Sequence[Sequence[object]]) -> list[str]:
    """Chunk spreadsheet rows; every chunk starts with the sheet name and the
    header row so it can be understood on its own."""
    lines = [line for line in (_row_text(row) for row in rows) if line]
    if not lines:
        return []
    header, body = lines[0], lines[1:]
    prefix = f"Sheet: {name}\n{header}"
    if not body:
        return [prefix]
    chunks: list[str] = []
    current: list[str] = []
    size = len(prefix)
    for line in body:
        if current and size + 1 + len(line) > MAX_CHARS:
            chunks.append("\n".join([prefix, *current]))
            current, size = [], len(prefix)
        current.append(line[:MAX_CHARS])
        size += 1 + len(current[-1])
    chunks.append("\n".join([prefix, *current]))
    return chunks


def build_chunks(
    pages: Sequence[tuple[int, str]],
    sheets: Sequence[tuple[int, str, Sequence[Sequence[object]]]],
) -> list[Chunk]:
    """Chunks for a document's pages ``(number, text)`` and sheets
    ``(number, name, rows)``, capped at MAX_CHUNKS_PER_DOCUMENT."""
    chunks: list[Chunk] = []
    for number, text in pages:
        chunks.extend(
            Chunk(content, page_number=number) for content in split_text(text)
        )
    for number, name, rows in sheets:
        chunks.extend(
            Chunk(content, sheet_number=number, sheet_name=name)
            for content in split_sheet(name, rows)
        )
    return chunks[:MAX_CHUNKS_PER_DOCUMENT]
