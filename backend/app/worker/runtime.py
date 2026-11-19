"""
Per-process dependencies for Celery tasks.
"""

from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.extraction.llm import ClaudeInvoiceLLM
from app.extraction.service import InvoiceExtractor
from app.processing.ocr import OcrConfig
from app.processing.processor import DocumentProcessor
from app.search.embeddings import get_embedder
from app.storage.local import LocalStorage
from app.worker.queue import JobQueue


@dataclass(frozen=True)
class Runtime:
    """Everything a task needs to run a job."""

    queue: JobQueue
    processor: DocumentProcessor
    invoice_extractor: InvoiceExtractor | None = None
    lease_seconds: float = 300.0
    progress_interval: float = 1.0
    retry_base_seconds: float = 30.0
    retry_max_seconds: float = 900.0
    retry_jitter: float = 0.2
    redispatch_after_seconds: float = 600.0


@lru_cache
def get_runtime() -> Runtime:
    """Build the runtime once per worker process.

    Each task runs in its own event loop (``asyncio.run``), so the engine
    uses NullPool: pooled asyncpg connections cannot be shared across loops.
    """
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, expire_on_commit=False, autoflush=False
    )
    invoice_extractor = None
    if settings.invoice_extraction_enabled:
        invoice_extractor = InvoiceExtractor(
            session_factory,
            ClaudeInvoiceLLM(
                model=settings.llm_model,
                effort=settings.llm_effort,
                max_tokens=settings.llm_max_tokens,
                timeout_seconds=settings.llm_timeout_seconds,
            ),
            max_input_chars=settings.llm_max_input_chars,
            tolerance=Decimal(str(settings.validation_tolerance)),
            min_ocr_confidence=settings.review_min_ocr_confidence,
        )
    return Runtime(
        queue=JobQueue(session_factory),
        invoice_extractor=invoice_extractor,
        processor=DocumentProcessor(
            session_factory=session_factory,
            storage=LocalStorage(settings.storage_dir),
            ocr_config=OcrConfig(
                languages=settings.ocr_languages,
                dpi=settings.ocr_dpi,
                timeout_seconds=settings.ocr_timeout_seconds,
            ),
            embedder=(
                get_embedder(settings.embedding_cache_dir)
                if settings.semantic_search_enabled
                else None
            ),
        ),
        lease_seconds=settings.job_lease_seconds,
        progress_interval=settings.progress_interval_seconds,
        retry_base_seconds=settings.job_retry_base_seconds,
        retry_max_seconds=settings.job_retry_max_seconds,
        redispatch_after_seconds=settings.job_redispatch_after_seconds,
    )
