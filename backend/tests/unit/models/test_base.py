from sqlalchemy import Integer
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.base import Base
from app.models.base import TimestampMixin


class _IsolatedBase(DeclarativeBase):
    """Own metadata: the sample table must not leak into the app's schema
    (other tests compare Base.metadata with the migrated database)."""


class _Sample(TimestampMixin, _IsolatedBase):
    __tablename__ = "_sample_timestamp"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)


def test_timestamp_mixin_adds_columns() -> None:
    """Verify TimestampMixin adds non-null, timezone-aware timestamp columns."""
    columns = _Sample.__table__.columns

    for name in ("created_at", "updated_at"):
        assert name in columns
        assert columns[name].nullable is False
        assert columns[name].type.timezone is True  # type: ignore[attr-defined]
        assert columns[name].server_default is not None

    assert columns["updated_at"].onupdate is not None


def test_sample_model_does_not_touch_the_application_metadata() -> None:
    assert "_sample_timestamp" not in Base.metadata.tables
