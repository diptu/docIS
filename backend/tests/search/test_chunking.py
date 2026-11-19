from app.search.chunking import (
    MAX_CHARS,
    MAX_CHUNKS_PER_DOCUMENT,
    build_chunks,
    split_sheet,
    split_text,
)


def test_short_text_is_one_chunk() -> None:
    assert split_text("Invoice INV-1\n\nTotal 10 EUR") == [
        "Invoice INV-1\n\nTotal 10 EUR"
    ]


def test_empty_text_has_no_chunks() -> None:
    assert split_text("  \n\n  ") == []


def test_long_text_is_split_within_the_limit_with_overlap() -> None:
    paragraphs = [f"Paragraph {i} " + "word " * 60 for i in range(20)]

    chunks = split_text("\n\n".join(paragraphs))

    assert len(chunks) > 1
    assert all(len(chunk) <= MAX_CHARS for chunk in chunks)
    # Every paragraph is somewhere, and consecutive chunks share some text.
    assert all(any(f"Paragraph {i} " in c for c in chunks) for i in range(20))
    assert chunks[1].split()[0] in chunks[0]


def test_a_single_huge_line_is_split_on_spaces() -> None:
    chunks = split_text("token " * 1000)

    assert all(len(chunk) <= MAX_CHARS for chunk in chunks)
    assert sum(chunk.count("token") for chunk in chunks) >= 1000


def test_sheet_chunks_repeat_name_and_header() -> None:
    rows: list[list[object]] = [["sku", "qty"], *[[f"SKU-{i}", i] for i in range(400)]]

    chunks = split_sheet("Orders", rows)

    assert len(chunks) > 1
    assert all(c.startswith("Sheet: Orders\nsku | qty\n") for c in chunks)
    assert "SKU-399 | 399" in chunks[-1]


def test_sheet_with_only_a_header_or_nothing() -> None:
    assert split_sheet("Empty", [[None, None]]) == []
    assert split_sheet("Head", [["a", "b"]]) == ["Sheet: Head\na | b"]


def test_build_chunks_keeps_sources_and_caps_the_total() -> None:
    chunks = build_chunks([(1, "Page one"), (2, "")], [(1, "S", [["h"], ["x"]])])

    assert [(c.page_number, c.sheet_number, c.sheet_name) for c in chunks] == [
        (1, None, None),
        (None, 1, "S"),
    ]
    many = build_chunks([(i, f"page {i}") for i in range(2000)], [])
    assert len(many) == MAX_CHUNKS_PER_DOCUMENT
