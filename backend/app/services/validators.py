"""
Deep content validation for uploaded files.

Each validator opens the fully written file with a real parser for its
format and raises:

* ``ContentMismatchError`` if the content is a different kind of file than
  its extension claims (rejected as an unsupported type), or
* ``CorruptedContentError`` if it is the right kind of file but damaged,
  truncated, encrypted, or otherwise unreadable.

Validators are synchronous and CPU/IO bound; run them in a worker thread.
"""

import csv
import io
import logging
import warnings
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TextIO

import openpyxl
import xlrd
from PIL import Image, ImageSequence
from pypdf import PdfReader

# Images above this many pixels are rejected to block decompression bombs.
MAX_IMAGE_PIXELS = 100_000_000
# XLSX packages may not expand beyond this, to block zip bombs.
MAX_XLSX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024

_XLSX_REQUIRED_PARTS = {"[Content_Types].xml", "xl/workbook.xml"}
_CSV_ALLOWED_CONTROL = {"\t", "\n", "\r", "\f"}
_CSV_ENCODINGS = ("utf-8-sig", "cp1252")

logging.getLogger("pypdf").setLevel(logging.ERROR)


class ContentError(Exception):
    """Base class for validation failures; ``reason`` is client-safe."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ContentMismatchError(ContentError):
    """The content is not the type its extension claims."""


class CorruptedContentError(ContentError):
    """The content is the right type but cannot be read."""


Validator = Callable[[Path], None]


def _guard(label: str, check: Validator) -> Validator:
    """Turn unexpected parser exceptions into CorruptedContentError."""

    def run(path: Path) -> None:
        try:
            check(path)
        except ContentError:
            raise
        except Exception as exc:
            raise CorruptedContentError(
                f"{label} file is damaged or unreadable."
            ) from exc

    return run


# ----------------------------------------------------------------- PDF


def _verify_pdf(path: Path) -> None:
    reader = PdfReader(path, strict=False)
    if reader.is_encrypted:
        raise CorruptedContentError("PDF is password-protected.")
    if len(reader.pages) == 0:
        raise CorruptedContentError("PDF has no pages.")
    for page in reader.pages:
        _ = page.mediabox  # forces each page object to be parsed


verify_pdf = _guard("PDF", _verify_pdf)


# -------------------------------------------------------------- images


def _image_verifier(label: str, formats: frozenset[str]) -> Validator:
    def verify(path: Path) -> None:
        Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            try:
                # Structural check: chunk CRCs, headers.
                with Image.open(path) as image:
                    if image.format not in formats:
                        raise ContentMismatchError(
                            f"content is not a valid {label} file."
                        )
                    image.verify()
                # Full decode of every frame catches truncated pixel data.
                with Image.open(path) as image:
                    for frame in ImageSequence.Iterator(image):
                        frame.load()
            except (Image.DecompressionBombError, Image.DecompressionBombWarning):
                raise CorruptedContentError(
                    f"{label} image dimensions are too large."
                ) from None

    return _guard(label, verify)


verify_jpeg = _image_verifier("JPEG", frozenset({"JPEG", "MPO"}))
verify_png = _image_verifier("PNG", frozenset({"PNG"}))
verify_tiff = _image_verifier("TIFF", frozenset({"TIFF"}))


# -------------------------------------------------------- spreadsheets


def _verify_xlsx(path: Path) -> None:
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise CorruptedContentError("XLSX file is damaged or unreadable.") from None

    with archive:
        if not set(archive.namelist()) >= _XLSX_REQUIRED_PARTS:
            raise ContentMismatchError("content is not a valid XLSX file.")
        expanded = sum(info.file_size for info in archive.infolist())
        if expanded > MAX_XLSX_UNCOMPRESSED_BYTES:
            raise CorruptedContentError("XLSX file expands to an unsafe size.")
        if archive.testzip() is not None:
            raise CorruptedContentError("XLSX file is damaged or unreadable.")

    # Pass a file object: openpyxl rejects paths without an .xlsx suffix,
    # and validation runs on the temporary ``.part`` file.
    with path.open("rb") as file:
        workbook = openpyxl.load_workbook(file, read_only=True, data_only=True)
        try:
            for sheet in workbook.worksheets:
                for _ in sheet.iter_rows(values_only=True):
                    pass
        finally:
            workbook.close()


verify_xlsx = _guard("XLSX", _verify_xlsx)


def _verify_xls(path: Path) -> None:
    try:
        book = xlrd.open_workbook(str(path), logfile=io.StringIO())
    except xlrd.XLRDError as exc:
        message = str(exc).lower()
        if "encrypted" in message:
            raise CorruptedContentError("XLS file is password-protected.") from None
        if "workbook" in message:
            # An OLE2 file without a workbook stream, e.g. a .doc or .ppt.
            raise ContentMismatchError("content is not a valid XLS file.") from None
        raise
    book.release_resources()


verify_xls = _guard("XLS", _verify_xls)


def _checked_lines(text: TextIO) -> Iterator[str]:
    for line in text:
        if any(ord(ch) < 32 and ch not in _CSV_ALLOWED_CONTROL for ch in line):
            raise ContentMismatchError("content is not a valid CSV file.")
        yield line


def _scan_csv(path: Path, encoding: str) -> None:
    has_data = False
    with path.open(encoding=encoding, newline="") as text:
        try:
            for row in csv.reader(_checked_lines(text), strict=True):
                has_data = has_data or any(cell.strip() for cell in row)
        except csv.Error as exc:
            raise CorruptedContentError(f"CSV file is malformed: {exc}.") from None
    if not has_data:
        raise CorruptedContentError("CSV file contains no data.")


def _verify_csv(path: Path) -> None:
    for encoding in _CSV_ENCODINGS:
        try:
            _scan_csv(path, encoding)
        except UnicodeDecodeError:
            continue
        return
    raise ContentMismatchError(
        "content is not valid CSV text (expected UTF-8 or Windows-1252)."
    )


verify_csv = _guard("CSV", _verify_csv)
