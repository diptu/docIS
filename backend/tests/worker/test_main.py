import os
import signal
from pathlib import Path

import anyio
import pytest

from app.core.config import Settings
from app.worker import __main__ as entrypoint


def test_build_worker_uses_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = Settings(
        database_url="sqlite+aiosqlite://",
        storage_dir=tmp_path,
        ocr_languages="eng+deu",
        job_lease_seconds=42,
        worker_poll_interval_seconds=0.5,
        worker_heartbeat_file=tmp_path / "hb",
    )
    monkeypatch.setattr(entrypoint, "get_settings", lambda: settings)

    worker = entrypoint.build_worker()

    assert worker.lease_seconds == 42
    assert worker.poll_interval == 0.5
    assert worker.heartbeat_file == tmp_path / "hb"
    assert worker.processor.ocr_config.languages == "eng+deu"
    assert worker.processor.storage.root == tmp_path.resolve()


def test_main_stops_on_sigterm(monkeypatch: pytest.MonkeyPatch) -> None:
    class IdleWorker:
        async def run_forever(self, stop: anyio.Event) -> None:
            os.kill(os.getpid(), signal.SIGTERM)
            with anyio.fail_after(5):
                await stop.wait()

    closed = []

    async def close_db() -> None:
        closed.append(True)

    monkeypatch.setattr(entrypoint, "build_worker", IdleWorker)
    monkeypatch.setattr(entrypoint, "close_db", close_db)

    anyio.run(entrypoint.main)

    assert closed == [True]
