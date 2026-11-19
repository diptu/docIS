import pytest

from app.services.document import MAX_FILE_NAME_LENGTH, clean_file_name


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("../../etc/report.pdf", "report.pdf"),
        ("C:\\Users\\me\\report.pdf", "report.pdf"),
        ("  spaced.pdf  ", "spaced.pdf"),
        (None, ""),
    ],
)
def test_clean_file_name(raw: str | None, expected: str) -> None:
    assert clean_file_name(raw) == expected


def test_long_file_name_keeps_extension() -> None:
    name = clean_file_name("a" * 300 + ".pdf")

    assert len(name) == MAX_FILE_NAME_LENGTH
    assert name.endswith(".pdf")


def test_long_file_name_without_extension() -> None:
    assert len(clean_file_name("a" * 300)) == MAX_FILE_NAME_LENGTH
