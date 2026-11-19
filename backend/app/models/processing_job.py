"""
Durable background processing jobs.
"""

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.base import TimestampMixin


class JobStatus(StrEnum):
    """Lifecycle of a processing job."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ProcessingJob(TimestampMixin, Base):
    """A unit of background work: extracting one document's content.

    Workers claim queued jobs whose ``next_run_at`` has passed, report
    progress (``stage`` plus ``progress_current`` of ``progress_total``) and
    refresh ``heartbeat_at`` while running. A failed attempt is requeued with
    backoff until ``attempts`` reaches ``max_attempts``; a running job whose
    heartbeat goes stale (dead worker) is treated the same way.
    """

    __tablename__ = "processing_jobs"
    __table_args__ = (
        Index(None, "status", "created_at"),
        # At most one active (queued or running) job per document, enforced
        # by the database so concurrent retry requests cannot double-queue.
        Index(
            "uq_processing_jobs_active_document",
            "document_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
            sqlite_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Set by the application with microsecond precision (the server default
    # remains for raw inserts): queue order and "latest job" depend on it,
    # and database clocks such as SQLite's CURRENT_TIMESTAMP are per-second.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=JobStatus.QUEUED
    )
    stage: Mapped[str | None] = mapped_column(String(50))
    progress_current: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_total: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3"
    )
    # Earliest time a queued job may be claimed; set when a retry is
    # scheduled with backoff. NULL means immediately.
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    worker_id: Mapped[str | None] = mapped_column(String(100))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
