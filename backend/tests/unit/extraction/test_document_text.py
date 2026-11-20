import pytest

from app.extraction.document_text import (
    DocumentTooLargeError,
    EmptyDocumentError,
    render_document,
)
from app.models import DocumentPage, DocumentSheet


def page(number: int, text: str) -> DocumentPage:
    return DocumentPage(page_number=number, text=text)


def sheet(number: int, name: str, rows: list[list[object]]) -> DocumentSheet:
    return DocumentSheet(sheet_number=number, name=name, rows=rows)


def test_pages_and_sheets_are_rendered_with_markers() -> None:
    text = render_document(
        [page(1, "Invoice INV-1"), page(2, "   "), page(3, "Total 10.00")],
        [sheet(1, "Lines", [["item", "qty"], ["bolts", 120], [None, 5]])],
        max_chars=10_000,
    )

    assert text == (
        "--- Page 1 ---\nInvoice INV-1\n\n"
        "--- Page 3 ---\nTotal 10.00\n\n"
        "--- Sheet 1: Lines ---\nitem | qty\nbolts | 120\n | 5"
    )


def test_empty_document_is_rejected() -> None:
    with pytest.raises(EmptyDocumentError):
        render_document([page(1, "")], [sheet(1, "Empty", [])], max_chars=100)


def test_oversized_document_is_rejected_not_truncated() -> None:
    with pytest.raises(DocumentTooLargeError, match="LLM_MAX_INPUT_CHARS"):
        render_document([page(1, "x" * 101)], [], max_chars=100)
