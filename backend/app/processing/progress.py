"""
Progress reporting from (threaded) extractors to the job runner.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


class Stage(StrEnum):
    """Named steps of document processing."""

    EXTRACTING_TEXT = "extracting_text"
    OCR = "ocr"
    PARSING_SHEETS = "parsing_sheets"
    PARSING_CSV = "parsing_csv"
    SAVING = "saving"


# progress(stage, current, total): ``current`` of ``total`` steps done.
ProgressCallback = Callable[[str, int, int | None], None]


def ignore_progress(stage: str, current: int, total: int | None) -> None:
    """Default callback: discard progress."""


@dataclass(frozen=True)
class ProgressSnapshot:
    stage: str | None
    current: int
    total: int | None
    version: int


class ProgressTracker:
    """Thread-safe latest-progress holder.

    Extractors call ``update`` from a worker thread; the job runner reads
    ``snapshot`` from the event loop and persists changes periodically, so
    extraction never waits on the database.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshot = ProgressSnapshot(stage=None, current=0, total=None, version=0)

    def update(self, stage: str, current: int, total: int | None) -> None:
        with self._lock:
            self._snapshot = ProgressSnapshot(
                stage=stage,
                current=current,
                total=total,
                version=self._snapshot.version + 1,
            )

    def snapshot(self) -> ProgressSnapshot:
        with self._lock:
            return self._snapshot
