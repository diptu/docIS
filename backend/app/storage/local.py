"""
Local filesystem storage for original uploaded documents.

Guarantees for every stored file:

* Written atomically: data goes to a ``.part`` file that is optionally
  verified, flushed to disk (fsync), and only then renamed into place, so a
  file at its final key is always complete and validated.
* Never overwritten: saving to an existing key fails.
* Kept byte-for-byte as uploaded, with its size and SHA-256 recorded.
* Read-only once stored, in directories closed to other users.
* Addressed only by server-generated keys confined to the storage root.
"""

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import anyio
from anyio import to_thread
from fastapi import UploadFile

CHUNK_SIZE = 1024 * 1024
FILE_MODE = 0o440
DIR_MODE = 0o750


class StorageLimitExceededError(Exception):
    """Raised when a stream is larger than the allowed size."""


@dataclass(frozen=True)
class StoredFile:
    """Result of a successful save."""

    key: str
    size_bytes: int
    sha256: str


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _commit(partial: Path, target: Path) -> None:
    """Make the verified partial file read-only and move it into place."""
    if target.exists():
        raise FileExistsError(f"Refusing to overwrite stored file: {target.name}")
    partial.chmod(FILE_MODE)
    partial.rename(target)
    _fsync_dir(target.parent)


class LocalStorage:
    """Store files under a root directory, addressed by relative keys."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _resolve(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise ValueError(f"Invalid storage key: {key!r}")
        return path

    def path_for(self, key: str) -> Path:
        """Return the filesystem path of a stored file, for reading."""
        return self._resolve(key)

    def _make_dirs(self, directory: Path) -> None:
        """Create missing directories under the root, each with DIR_MODE.

        ``Path.mkdir(parents=True)`` applies the mode only to the last level,
        so intermediate levels are created one at a time.
        """
        self.root.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
        for level in reversed([directory, *directory.parents]):
            if level.is_relative_to(self.root) and level != self.root:
                level.mkdir(mode=DIR_MODE, exist_ok=True)

    async def save(
        self,
        source: UploadFile,
        key: str,
        max_bytes: int,
        verify: Callable[[Path], None] | None = None,
    ) -> StoredFile:
        """Stream an upload to disk and return its key, size and checksum.

        ``verify`` runs in a worker thread on the fully written file before it
        is committed; any exception it raises aborts the save.
        """
        target = self._resolve(key)
        partial = target.with_name(target.name + ".part")
        await to_thread.run_sync(self._make_dirs, target.parent)

        digest = hashlib.sha256()
        size = 0
        try:
            async with await anyio.open_file(partial, "xb") as out:
                while chunk := await source.read(CHUNK_SIZE):
                    size += len(chunk)
                    if size > max_bytes:
                        raise StorageLimitExceededError
                    digest.update(chunk)
                    await out.write(chunk)
                await out.flush()
                await to_thread.run_sync(os.fsync, out.wrapped.fileno())

            if verify is not None:
                await to_thread.run_sync(verify, partial)
            await to_thread.run_sync(_commit, partial, target)
        except BaseException:
            await anyio.Path(partial).unlink(missing_ok=True)
            raise

        return StoredFile(key=key, size_bytes=size, sha256=digest.hexdigest())

    async def delete(self, key: str) -> None:
        """Remove a stored file if it exists."""
        await anyio.Path(self._resolve(key)).unlink(missing_ok=True)
