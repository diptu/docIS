"""PostgreSQL + pgvector integration tests.

Run against a real, disposable database:

    export TEST_POSTGRES_URL=postgresql+asyncpg://docis:docis@localhost:5434/docis_test
    uv run pytest -m postgres

Without TEST_POSTGRES_URL these tests are skipped. The schema is built once
per session by the real Alembic migrations (not ``create_all``), so the
tests also prove the migrations produce what the code expects. After each
test every table is truncated, so tests are independent and can run in any
order.

Safety: the fixtures drop and recreate the schema, so they refuse any
database whose name does not end in ``_test``.
"""

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.db.base import Base

BACKEND = Path(__file__).resolve().parents[3]


def alembic(url: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the project's Alembic CLI against ``url``."""
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture(scope="session")
def pg_url() -> str:
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("set TEST_POSTGRES_URL to run PostgreSQL integration tests")
    database = make_url(url).database or ""
    if not database.endswith("_test"):
        pytest.exit(
            f"Refusing to use database {database!r}: the PostgreSQL tests drop "
            "its schema. Point TEST_POSTGRES_URL at a database named *_test.",
            returncode=4,
        )
    return url


@pytest.fixture(scope="session")
def migrated(pg_url: str) -> str:
    """A clean schema built by ``alembic upgrade head`` (once per session)."""

    async def reset() -> None:
        engine = create_async_engine(pg_url, poolclass=NullPool)
        async with engine.begin() as connection:
            await connection.execute(text("DROP SCHEMA public CASCADE"))
            await connection.execute(text("CREATE SCHEMA public"))
        await engine.dispose()

    asyncio.run(reset())
    result = alembic(pg_url, "upgrade", "head")
    assert result.returncode == 0, result.stderr
    return pg_url


@pytest.fixture
async def pg_engine(migrated: str) -> AsyncIterator[AsyncEngine]:
    """An engine on the migrated database; all tables emptied afterwards.

    NullPool: no connection outlives the test's event loop.
    """
    engine = create_async_engine(migrated, poolclass=NullPool)
    try:
        yield engine
    finally:
        tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
        async with engine.begin() as connection:
            await connection.execute(text(f"TRUNCATE {tables} CASCADE"))
        await engine.dispose()


@pytest.fixture
def pg_factory(pg_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(pg_engine, expire_on_commit=False, autoflush=False)
