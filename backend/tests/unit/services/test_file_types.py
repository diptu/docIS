import pytest

from app.services.file_types import (
    CSV,
    JPEG,
    PDF,
    PNG,
    SIGNATURE_LENGTH,
    TIFF,
    XLS,
    XLSX,
    accepted_extensions,
    type_for_file_name,
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("a.pdf", PDF),
        ("a.JPG", JPEG),
        ("a.jpeg", JPEG),
        ("a.png", PNG),
        ("a.tif", TIFF),
        ("a.TIFF", TIFF),
        ("a.xlsx", XLSX),
        ("a.XLS", XLS),
        ("a.csv", CSV),
        ("a.gif", None),
        ("pdf", None),
        ("", None),
    ],
)
def test_type_for_file_name(name: str, expected: object) -> None:
    assert type_for_file_name(name) is expected


def test_signature_length_covers_longest_signature() -> None:
    assert len(b"\x89PNG\r\n\x1a\n") == SIGNATURE_LENGTH


def test_accepted_extensions_lists_all() -> None:
    assert accepted_extensions() == (
        ".csv, .jpeg, .jpg, .pdf, .png, .tif, .tiff, .xls, .xlsx"
    )


def test_type_without_signatures_matches_any_header() -> None:
    assert CSV.matches(b"anything")
