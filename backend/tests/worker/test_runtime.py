from pathlib import Path

import pytest

from app.api.dependencies import get_job_dispatcher
from app.core.config import Settings
from app.worker import runtime as runtime_module
from app.worker.dispatch import celery_dispatcher


def test_get_runtime_is_built_from_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = Settings(
        database_url="sqlite+aiosqlite://",
        storage_dir=tmp_path,
        ocr_languages="eng+deu",
        job_lease_seconds=42,
        job_retry_base_seconds=5,
        job_retry_max_seconds=50,
        job_redispatch_after_seconds=120,
    )
    monkeypatch.setattr(runtime_module, "get_settings", lambda: settings)
    runtime_module.get_runtime.cache_clear()
    try:
        runtime = runtime_module.get_runtime()

        assert runtime is runtime_module.get_runtime()  # one per process
        assert (runtime.lease_seconds, runtime.redispatch_after_seconds) == (42, 120)
        assert (runtime.retry_base_seconds, runtime.retry_max_seconds) == (5, 50)
        assert runtime.processor.ocr_config.languages == "eng+deu"
        assert runtime.processor.storage.root == tmp_path.resolve()
        assert runtime.queue.session_factory is runtime.processor.session_factory
        assert runtime.invoice_extractor is not None
        assert runtime.invoice_extractor.llm.model == "claude-opus-5-5"  # type: ignore[attr-defined]
    finally:
        runtime_module.get_runtime.cache_clear()


def test_get_runtime_without_invoice_extraction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = Settings(
        database_url="sqlite+aiosqlite://",
        storage_dir=tmp_path,
        invoice_extraction_enabled=False,
    )
    monkeypatch.setattr(runtime_module, "get_settings", lambda: settings)
    runtime_module.get_runtime.cache_clear()
    try:
        assert runtime_module.get_runtime().invoice_extractor is None
    finally:
        runtime_module.get_runtime.cache_clear()


def test_api_dispatches_through_celery() -> None:
    assert get_job_dispatcher() is celery_dispatcher
