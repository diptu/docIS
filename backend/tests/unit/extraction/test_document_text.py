import pytest

from app.extraction.document_text import (
    EmptyDocumentError,
    document_sections,
    render_document,
    split_document,
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
    )

    assert text == (
        "--- Page 1 ---\nInvoice INV-1\n\n"
        "--- Page 3 ---\nTotal 10.00\n\n"
        "--- Sheet 1: Lines ---\nitem | qty\nbolts | 120\n | 5"
    )


def test_empty_document_is_rejected() -> None:
    with pytest.raises(EmptyDocumentError):
        render_document([page(1, "")], [sheet(1, "Empty", [])])


# ---------------------------------------------------------------- splitting


def lines_of(parts: list[str]) -> list[str]:
    """Content lines, without page/sheet markers or blank separators."""
    return [
        line
        for part in parts
        for line in part.split("\n")
        if line and not line.startswith("--- ")
    ]


def test_a_short_document_is_one_part_identical_to_the_rendering() -> None:
    pages = [page(1, "Invoice INV-1"), page(2, "Total 10.00")]

    parts = split_document(document_sections(pages, []), 10_000)

    assert parts == [render_document(pages, [])]


def test_parts_break_between_pages_and_keep_their_order() -> None:
    pages = [page(n, f"Line item {n:03d} " + "x" * 30) for n in range(1, 11)]

    parts = split_document(document_sections(pages, []), 120)

    assert len(parts) > 1
    assert all(len(part) <= 120 for part in parts)
    # Each page is whole, in one part, in document order.
    assert lines_of(parts) == [p.text for p in pages]
    for part in parts:
        assert part.startswith("--- Page ")


def test_a_page_longer_than_a_part_continues_in_the_next() -> None:
    text = "\n".join(f"row {n:02d} " + "y" * 20 for n in range(30))

    parts = split_document(document_sections([page(7, text)], []), 200)

    assert len(parts) > 1
    assert all(len(part) <= 200 for part in parts)
    assert parts[0].startswith("--- Page 7 ---\n")
    assert all(part.startswith("--- Page 7 (continued) ---\n") for part in parts[1:])
    assert lines_of(parts) == text.split("\n")  # nothing lost or repeated


def test_a_long_sheet_repeats_its_heading_row_in_every_part() -> None:
    rows: list[list[object]] = [["description", "qty", "amount"]]
    rows += [[f"item {n}", n, n * 10] for n in range(1, 60)]

    parts = split_document(document_sections([], [sheet(2, "Lines", rows)]), 300)

    assert len(parts) > 1
    assert all(len(part) <= 300 for part in parts)
    for part in parts[1:]:
        marker, heading, *_ = part.split("\n")
        assert marker == "--- Sheet 2: Lines (continued; first row repeated) ---"
        assert heading == "description | qty | amount"
    data_rows = [
        line for line in lines_of(parts) if line != "description | qty | amount"
    ]
    assert data_rows == [f"item {n} | {n} | {n * 10}" for n in range(1, 60)]


def test_a_single_line_longer_than_a_part_is_sliced() -> None:
    line = "z" * 450

    parts = split_document(document_sections([page(1, line)], []), 100)

    assert all(len(part) <= 100 for part in parts)
    assert "".join(lines_of(parts)) == line


def test_a_heading_row_too_wide_to_repeat_is_not_repeated() -> None:
    rows: list[list[object]] = [["h" * 80], *[[f"r{n}"] for n in range(40)]]

    parts = split_document(document_sections([], [sheet(1, "S", rows)]), 120)

    assert all(len(part) <= 120 for part in parts)
    # Not repeated, and not lost (it may itself be sliced to fit).
    assert sum(line.count("h") for line in lines_of(parts)) == 80
    assert lines_of(parts)[-40:] == [f"r{n}" for n in range(40)]
