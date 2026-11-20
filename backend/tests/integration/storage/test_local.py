import io
import stat
from pathlib import Path

import pytest
from fastapi import UploadFile

from app.storage.local import LocalStorage, StorageLimitExceededError


def upload(data: bytes) -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename="x.pdf")


async def test_save_records_size_and_checksum(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)

    stored = await storage.save(upload(b"data"), "nested/file.pdf", max_bytes=10)

    assert stored.key == "nested/file.pdf"
    assert stored.size_bytes == 4
    assert stored.sha256 == (
        "3a6eb0790f39ac87c94f3856b2dd2c5d110e6811602261a9a923d3bb23adc8b7"
    )
    path = tmp_path / "nested" / "file.pdf"
    assert path.read_bytes() == b"data"
    assert stat.S_IMODE(path.stat().st_mode) == 0o440


async def test_delete_is_idempotent(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    stored = await storage.save(upload(b"data"), "file.pdf", max_bytes=10)

    await storage.delete(stored.key)
    await storage.delete(stored.key)

    assert not (tmp_path / "file.pdf").exists()


async def test_save_never_overwrites(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    await storage.save(upload(b"original"), "file.pdf", max_bytes=100)

    with pytest.raises(FileExistsError):
        await storage.save(upload(b"replacement"), "file.pdf", max_bytes=100)

    assert (tmp_path / "file.pdf").read_bytes() == b"original"
    assert not list(tmp_path.glob("*.part"))


async def test_save_over_limit_leaves_nothing(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)

    with pytest.raises(StorageLimitExceededError):
        await storage.save(upload(b"x" * 20), "file.pdf", max_bytes=10)

    assert list(tmp_path.iterdir()) == []


async def test_failed_verification_leaves_nothing(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    seen: list[bytes] = []

    def reject(path: Path) -> None:
        seen.append(path.read_bytes())
        raise ValueError("bad content")

    with pytest.raises(ValueError, match="bad content"):
        await storage.save(upload(b"data"), "file.pdf", max_bytes=10, verify=reject)

    assert seen == [b"data"]  # verifier saw the complete file
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("key", ["../escape.pdf", "/etc/passwd", "", "."])
def test_keys_outside_root_are_rejected(tmp_path: Path, key: str) -> None:
    with pytest.raises(ValueError, match="Invalid storage key"):
        LocalStorage(tmp_path / "root")._resolve(key)


async def test_every_created_directory_is_private(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)

    await storage.save(upload(b"data"), "a/b/c/file.pdf", max_bytes=10)

    for level in ("a", "a/b", "a/b/c"):
        assert stat.S_IMODE((tmp_path / level).stat().st_mode) == 0o750
