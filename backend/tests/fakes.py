"""Test doubles shared across test modules."""

import uuid
from datetime import UTC, datetime
from typing import Any


class FakeSession:
    """Minimal stand-in for AsyncSession that records added objects."""

    def __init__(self, fail_on_commit: bool = False) -> None:
        self.added: list[Any] = []
        self.committed = False
        self.fail_on_commit = fail_on_commit

    def add_all(self, objects: Any) -> None:
        self.added.extend(objects)

    async def commit(self) -> None:
        if self.fail_on_commit:
            raise RuntimeError("database unavailable")
        self.committed = True

    async def refresh(self, obj: Any) -> None:
        now = datetime.now(UTC)
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        obj.created_at = now
        obj.updated_at = now
