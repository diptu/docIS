from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session as db_session


async def test_get_db_yields_async_session() -> None:
    """Verify get_db yields an AsyncSession."""
    generator = db_session.get_db()
    session = await anext(generator)

    assert isinstance(session, AsyncSession)

    await generator.aclose()


async def test_close_db_disposes_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify close_db disposes of the engine's connection pool."""
    engine = AsyncMock()
    monkeypatch.setattr(db_session, "engine", engine)

    await db_session.close_db()

    engine.dispose.assert_awaited_once()
