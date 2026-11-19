from sqlalchemy import Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.base import TimestampMixin


class _Sample(TimestampMixin, Base):
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
