"""Edge cases for validators not reachable through the API tests."""

from pathlib import Path

import pytest
import xlrd

from app.services import validators
from app.services.validators import (
    ContentMismatchError,
    CorruptedContentError,
    verify_csv,
    verify_png,
    verify_xls,
    verify_xlsx,
)
from tests.support.helpers import make_image, make_pdf, make_xlsx, make_zip


def write(tmp_path: Path, data: bytes, name: str = "file") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


@pytest.mark.parametrize(
    "data",
    [
        b"name,amount\nalice,10\n",
        b"\xef\xbb\xbfname;amount\r\nj\xc3\xbcrgen;5\r\n",  # UTF-8 with BOM
        b"name\tamount\ncaf\xe9\t3\n",  # Windows-1252
        b"single column\n",
        b'quoted,"multi\nline",field\n',
        b'a,b"c,d\n',  # stray quote inside unquoted field is allowed
    ],
)
def test_csv_accepts_real_world_text(tmp_path: Path, data: bytes) -> None:
    verify_csv(write(tmp_path, data))


@pytest.mark.parametrize(
    ("data", "error"),
    [
        (b"a,b\n" * 5000 + b"c,\x00d\n", ContentMismatchError),  # NUL late in file
        (b"a,b\x01\x02,c\n", ContentMismatchError),  # binary control chars
        (b"\x81\x8d\x8f\x90", ContentMismatchError),  # undecodable
        (b'a,b\n"x"y,2\n', CorruptedContentError),  # bad quoting
    ],
)
def test_csv_rejects_binary_or_malformed(
    tmp_path: Path, data: bytes, error: type[Exception]
) -> None:
    with pytest.raises(error):
        verify_csv(write(tmp_path, data))


def test_xlsx_rejects_zip_bomb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(validators, "MAX_XLSX_UNCOMPRESSED_BYTES", 10)

    with pytest.raises(CorruptedContentError, match="unsafe size"):
        verify_xlsx(write(tmp_path, make_xlsx()))


def test_xlsx_rejects_bad_crc(tmp_path: Path) -> None:
    data = bytearray(
        make_zip({"[Content_Types].xml": "<T/>", "xl/workbook.xml": "<w/>" * 50})
    )
    # Flip a byte inside the stored (uncompressed) workbook part.
    index = data.index(b"<w/><w/>")
    data[index] ^= 0xFF

    with pytest.raises(CorruptedContentError, match="damaged"):
        verify_xlsx(write(tmp_path, bytes(data)))


def test_image_decompression_bomb_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Oversized images are rejected as decompression bombs."""
    from PIL import Image

    # The validator copies its limit into Pillow's process-wide setting;
    # restore that too, or every later image test inherits a 100-pixel cap.
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", Image.MAX_IMAGE_PIXELS)
    monkeypatch.setattr(validators, "MAX_IMAGE_PIXELS", 100)

    with pytest.raises(CorruptedContentError, match="too large"):
        verify_png(write(tmp_path, make_image("PNG", (64, 64))))


def test_pdf_without_pages_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class EmptyReader:
        is_encrypted = False
        pages: list[object] = []

        def __init__(self, *_: object, **__: object) -> None:
            pass

    monkeypatch.setattr(validators, "PdfReader", EmptyReader)

    with pytest.raises(CorruptedContentError, match="no pages"):
        validators.verify_pdf(write(tmp_path, make_pdf()))


@pytest.mark.parametrize(
    ("message", "error", "match"),
    [
        ("Can't find workbook in OLE2 compound document", ContentMismatchError, "XLS"),
        ("Workbook is encrypted", CorruptedContentError, "password-protected"),
        ("Unsupported format, or corrupt file", CorruptedContentError, "damaged"),
    ],
)
def test_xls_error_classification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    message: str,
    error: type[Exception],
    match: str,
) -> None:
    """A .doc renamed to .xls is a type mismatch; encryption is reported."""

    def fail(*_: object, **__: object) -> None:
        raise xlrd.XLRDError(message)

    monkeypatch.setattr(validators.xlrd, "open_workbook", fail)

    with pytest.raises(error, match=match):
        verify_xls(write(tmp_path, b"\xd0\xcf\x11\xe0"))


def test_image_of_another_format_is_a_mismatch(tmp_path: Path) -> None:
    """The decoder's detected format must match the expected one."""
    with pytest.raises(ContentMismatchError, match="not a valid PNG"):
        verify_png(write(tmp_path, make_image("JPEG")))
