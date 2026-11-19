import hashlib
import stat
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.main import app
from tests.fakes import FakeSession
from tests.helpers import (
    CSV_BYTES,
    JPEG_BYTES,
    PDF_BYTES,
    PNG_BYTES,
    TIFF_BYTES,
    XLS_BYTES,
    XLSX_BYTES,
    make_image,
    make_pdf,
    make_zip,
    truncate,
)

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def storage_dir(tmp_path: Path) -> Path:
    return tmp_path / "storage"


@pytest.fixture
def client(session: FakeSession, storage_dir: Path) -> Iterator[TestClient]:
    async def override_get_db() -> AsyncIterator[FakeSession]:
        yield session

    settings = Settings(
        database_url="postgresql+asyncpg://test@localhost/test",
        storage_dir=storage_dir,
        max_upload_size_bytes=64 * 1024,
        max_files_per_upload=3,
    )
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def stored_files(storage_dir: Path) -> list[Path]:
    if not storage_dir.exists():
        return []
    return sorted(p for p in storage_dir.rglob("*") if p.is_file())


def post(client: TestClient, *files: tuple[str, bytes]) -> Response:
    return client.post("/documents", files=[("files", f) for f in files])


# ------------------------------------------------------------ accepted


@pytest.mark.parametrize(
    ("name", "content", "media_type", "stored_ext"),
    [
        ("report.pdf", PDF_BYTES, "application/pdf", ".pdf"),
        ("photo.jpg", JPEG_BYTES, "image/jpeg", ".jpg"),
        ("photo.JPEG", JPEG_BYTES, "image/jpeg", ".jpg"),
        ("scan.png", PNG_BYTES, "image/png", ".png"),
        ("scan.tif", TIFF_BYTES, "image/tiff", ".tiff"),
        ("book.xlsx", XLSX_BYTES, XLSX_TYPE, ".xlsx"),
        ("legacy.XLS", XLS_BYTES, "application/vnd.ms-excel", ".xls"),
        ("data.csv", CSV_BYTES, "text/csv", ".csv"),
    ],
)
def test_upload_each_supported_type(
    client: TestClient,
    session: FakeSession,
    storage_dir: Path,
    name: str,
    content: bytes,
    media_type: str,
    stored_ext: str,
) -> None:
    """Every supported type is accepted, stored unmodified and recorded."""
    response = client.post("/documents", files=[("files", (name, content))])

    assert response.status_code == 201, response.text
    document = response.json()[0]
    assert document["file_name"] == name
    assert document["file_type"] == media_type
    assert document["status"] == "uploaded"
    assert document["size_bytes"] == len(content)
    assert document["sha256"] == hashlib.sha256(content).hexdigest()

    hex_id = document["id"].replace("-", "")
    assert document["storage_path"] == (
        f"originals/{hex_id[:2]}/{document['id']}{stored_ext}"
    )
    assert (storage_dir / document["storage_path"]).read_bytes() == content
    assert session.committed


def test_upload_multiple_files_in_one_request(client: TestClient) -> None:
    """Several files of different types can be uploaded together."""
    response = client.post(
        "/documents",
        files=[
            ("files", ("a.pdf", PDF_BYTES)),
            ("files", ("b.png", PNG_BYTES)),
            ("files", ("c.csv", CSV_BYTES)),
        ],
    )

    assert response.status_code == 201
    assert [d["file_name"] for d in response.json()] == ["a.pdf", "b.png", "c.csv"]


def test_file_name_is_stripped_of_directories(client: TestClient) -> None:
    """Client-supplied paths cannot influence where files are written."""
    response = client.post(
        "/documents", files=[("files", ("../../etc/evil.pdf", PDF_BYTES))]
    )

    assert response.status_code == 201
    assert response.json()[0]["file_name"] == "evil.pdf"
    assert response.json()[0]["storage_path"].startswith("originals/")


# ------------------------------------------------------------ T1.5 storage


def test_stored_originals_are_read_only_and_complete(
    client: TestClient, storage_dir: Path
) -> None:
    """Stored files are read-only, private, and no partial files remain."""
    response = client.post("/documents", files=[("files", ("a.pdf", PDF_BYTES))])
    stored = storage_dir / response.json()[0]["storage_path"]

    assert stat.S_IMODE(stored.stat().st_mode) == 0o440
    assert stat.S_IMODE(stored.parent.stat().st_mode) & 0o007 == 0
    assert not list(storage_dir.rglob("*.part"))


# ------------------------------------------------------------ rejected


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("notes.txt", b"hello", "unsupported file type"),
        ("anim.gif", b"GIF89a....", "unsupported file type"),
        ("noextension", PNG_BYTES, "unsupported file type"),
        # Extension does not match the real content.
        ("photo.png", JPEG_BYTES, "not a valid PNG file"),
        ("photo.jpg", PNG_BYTES, "not a valid JPEG file"),
        ("scan.tiff", PDF_BYTES, "not a valid TIFF file"),
        ("doc.pdf", PNG_BYTES, "not a valid PDF file"),
        ("plain.xlsx", make_zip({"readme.txt": "x"}), "not a valid XLSX file"),
        ("book.xls", XLSX_BYTES, "not a valid XLS file"),
        ("image.csv", PNG_BYTES, "not a valid CSV file"),
    ],
)
def test_unsupported_types_are_rejected_with_415(
    client: TestClient, storage_dir: Path, name: str, content: bytes, message: str
) -> None:
    response = post(client, (name, content))

    assert response.status_code == 415
    detail = response.json()["detail"]
    assert name in detail
    assert message in detail
    assert stored_files(storage_dir) == []


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("cut.pdf", truncate(make_pdf(pages=3)), "PDF file is damaged"),
        ("locked.pdf", make_pdf(password="secret"), "password-protected"),
        ("cut.jpg", truncate(make_image("JPEG", (64, 64))), "JPEG file is damaged"),
        ("cut.png", truncate(make_image("PNG", (64, 64))), "PNG file is damaged"),
        ("cut.tiff", truncate(make_image("TIFF", (64, 64))), "TIFF file is damaged"),
        ("cut.xlsx", truncate(XLSX_BYTES), "XLSX file is damaged"),
        ("cut.xls", truncate(XLS_BYTES), "XLS file is damaged"),
        ("bad.csv", b'a,b\n1,"unterminated\n', "CSV file is malformed"),
        ("blank.csv", b"  \n\n", "CSV file contains no data"),
    ],
)
def test_corrupted_files_are_rejected_with_422(
    client: TestClient, storage_dir: Path, name: str, content: bytes, message: str
) -> None:
    response = post(client, (name, content))

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert name in detail
    assert message in detail
    assert stored_files(storage_dir) == []


@pytest.mark.parametrize(
    ("content", "status_code", "message"),
    [
        (b"", 422, "file is empty"),
        (PDF_BYTES + b"\n" * (70 * 1024), 413, "exceeds the"),
    ],
)
def test_empty_and_oversized_files(
    client: TestClient,
    storage_dir: Path,
    content: bytes,
    status_code: int,
    message: str,
) -> None:
    response = post(client, ("file.pdf", content))

    assert response.status_code == status_code
    assert message in response.json()["detail"]
    assert stored_files(storage_dir) == []


def test_one_bad_file_rejects_whole_batch(
    client: TestClient, session: FakeSession, storage_dir: Path
) -> None:
    """A corrupted file undoes earlier, valid files in the same batch."""
    response = client.post(
        "/documents",
        files=[
            ("files", ("ok.pdf", PDF_BYTES)),
            ("files", ("ok.csv", CSV_BYTES)),
            ("files", ("cut.png", truncate(make_image("PNG", (64, 64))))),
        ],
    )

    assert response.status_code == 422
    assert not session.committed
    assert stored_files(storage_dir) == []


def test_too_many_files(client: TestClient) -> None:
    files = [("files", (f"{i}.pdf", PDF_BYTES)) for i in range(4)]

    response = client.post("/documents", files=files)

    assert response.status_code == 422
    assert "maximum is 3" in response.json()["detail"]


def test_no_files(client: TestClient) -> None:
    assert client.post("/documents").status_code == 422


def test_database_failure_removes_stored_files(
    client: TestClient, storage_dir: Path
) -> None:
    """If the database write fails, uploaded files are cleaned up."""
    failing = FakeSession(fail_on_commit=True)

    async def override_get_db() -> AsyncIterator[FakeSession]:
        yield failing

    app.dependency_overrides[get_db] = override_get_db
    with pytest.raises(RuntimeError, match="database unavailable"):
        client.post("/documents", files=[("files", ("a.pdf", PDF_BYTES))])

    assert stored_files(storage_dir) == []
