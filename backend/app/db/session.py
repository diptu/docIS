"""
Asynchronous database engine and session management.
"""

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Dialect
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.core.exceptions import DatabaseUnavailableError

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
)


@event.listens_for(engine.sync_engine, "do_connect")
def _connect(
    dialect: Dialect, _record: Any, cargs: tuple[Any, ...], cparams: dict[str, Any]
) -> Any:
    """Open a connection, turning "PostgreSQL is unreachable" (refused,
    timed out, still starting, login rejected) into DatabaseUnavailableError,
    which the API answers with 503 instead of a bare 500."""
    dbapi = dialect.loaded_dbapi
    try:
        return dbapi.connect(*cargs, **cparams)
    except (OSError, TimeoutError, dbapi.Error) as exc:
        raise DatabaseUnavailableError() from exc


AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Provide an asynchronous database session."""
    async with AsyncSessionLocal() as session:
        yield session


async def close_db() -> None:
    """Dispose of the database connection pool."""
    await engine.dispose()
