"""
Shared ORM model mixins.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    """Provide creation and update timestamps.

    Values are set by the application with microsecond precision, so rows
    created in quick succession sort in creation order (SQLite's own clock
    is per-second). The server defaults remain for rows inserted with raw
    SQL.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        server_default=func.now(),
        onupdate=utcnow,
        nullable=False,
    )
