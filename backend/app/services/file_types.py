"""
Accepted upload file types and content detection.
"""

from dataclasses import dataclass

from app.services.validators import (
    Validator,
    verify_csv,
    verify_jpeg,
    verify_pdf,
    verify_png,
    verify_tiff,
    verify_xls,
    verify_xlsx,
)


@dataclass(frozen=True)
class FileType:
    """An accepted file type.

    ``signatures`` are magic bytes the file must start with (none means any
    start is allowed); they give a cheap early rejection. ``verify`` then
    parses the whole written file to prove it is genuine and undamaged.
    """

    label: str
    media_type: str
    extensions: tuple[str, ...]
    signatures: tuple[bytes, ...] = ()
    verify: Validator | None = None

    @property
    def canonical_extension(self) -> str:
        """Extension used when storing files of this type."""
        return self.extensions[0]

    def matches(self, header: bytes) -> bool:
        """Return True if the header carries one of this type's signatures."""
        if not self.signatures:
            return True
        return any(header.startswith(signature) for signature in self.signatures)


PDF = FileType(
    label="PDF",
    media_type="application/pdf",
    extensions=(".pdf",),
    signatures=(b"%PDF-",),
    verify=verify_pdf,
)
JPEG = FileType(
    label="JPEG",
    media_type="image/jpeg",
    extensions=(".jpg", ".jpeg"),
    signatures=(b"\xff\xd8\xff",),
    verify=verify_jpeg,
)
PNG = FileType(
    label="PNG",
    media_type="image/png",
    extensions=(".png",),
    signatures=(b"\x89PNG\r\n\x1a\n",),
    verify=verify_png,
)
TIFF = FileType(
    label="TIFF",
    media_type="image/tiff",
    extensions=(".tiff", ".tif"),
    signatures=(
        b"II*\x00",  # little-endian TIFF
        b"MM\x00*",  # big-endian TIFF
        b"II+\x00",  # little-endian BigTIFF
        b"MM\x00+",  # big-endian BigTIFF
    ),
    verify=verify_tiff,
)
XLSX = FileType(
    label="XLSX",
    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    extensions=(".xlsx",),
    signatures=(b"PK\x03\x04",),
    verify=verify_xlsx,
)
XLS = FileType(
    label="XLS",
    media_type="application/vnd.ms-excel",
    extensions=(".xls",),
    signatures=(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),  # OLE2 compound document
    verify=verify_xls,
)
CSV = FileType(
    label="CSV",
    media_type="text/csv",
    extensions=(".csv",),
    verify=verify_csv,
)

SUPPORTED_TYPES: tuple[FileType, ...] = (PDF, JPEG, PNG, TIFF, XLSX, XLS, CSV)

SIGNATURE_LENGTH = max(len(s) for t in SUPPORTED_TYPES for s in t.signatures)

_BY_EXTENSION = {ext: t for t in SUPPORTED_TYPES for ext in t.extensions}


def type_for_file_name(file_name: str) -> FileType | None:
    """Return the accepted file type for a file name's extension, if any."""
    _, dot, ext = file_name.lower().rpartition(".")
    return _BY_EXTENSION.get(f".{ext}") if dot else None


def accepted_extensions() -> str:
    """Human-readable list of accepted extensions."""
    return ", ".join(sorted(_BY_EXTENSION))
