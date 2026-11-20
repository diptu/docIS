"""Alembic migrations on a real PostgreSQL + pgvector database."""

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.base import Base
from tests.integration.postgres.conftest import alembic


async def _scalars(engine: AsyncEngine, sql: str) -> set[str]:
    async with engine.connect() as connection:
        return set((await connection.execute(text(sql))).scalars())


async def test_head_creates_every_table_extension_and_search_index(
    pg_engine: AsyncEngine,
) -> None:
    """The migrated schema matches the models, with pgvector and the
    Postgres-only indexes that search and the job queue rely on."""
    tables = await _scalars(
        pg_engine, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
    )
    extensions = await _scalars(pg_engine, "SELECT extname FROM pg_extension")
    indexes = await _scalars(
        pg_engine,
        "SELECT indexname || ' ' || indexdef FROM pg_indexes "
        "WHERE schemaname = 'public'",
    )

    assert tables == set(Base.metadata.tables) | {"alembic_version"}
    assert "vector" in extensions
    by_name = {line.split(" ", 1)[0]: line for line in indexes}
    assert (
        "USING gin (to_tsvector('english'" in by_name["ix_document_chunks_content_fts"]
    )
    assert (
        "USING hnsw (embedding vector_cosine_ops)"
        in by_name["ix_document_chunks_embedding_hnsw"]
    )
    active_job = by_name["uq_processing_jobs_active_document"]
    assert active_job.split(" ", 1)[1].startswith("CREATE UNIQUE INDEX")
    assert "WHERE" in active_job and "queued" in active_job


def test_models_and_migrations_do_not_drift(migrated: str) -> None:
    """``alembic check``: every model change has a migration."""
    result = alembic(migrated, "check")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "No new upgrade operations detected" in result.stdout + result.stderr


def test_full_downgrade_and_upgrade_round_trip(migrated: str) -> None:
    """Every migration can be reverted and re-applied."""
    try:
        down = alembic(migrated, "downgrade", "base")
        assert down.returncode == 0, down.stderr

        async def remaining() -> set[str]:
            engine = create_async_engine(migrated, poolclass=NullPool)
            try:
                return await _scalars(
                    engine,
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'",
                )
            finally:
                await engine.dispose()

        assert asyncio.run(remaining()) == {"alembic_version"}
    finally:
        up = alembic(migrated, "upgrade", "head")
    assert up.returncode == 0, up.stderr
