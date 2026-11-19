from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import app


def test_database_health_returns_healthy_status() -> None:
    """Verify the database health endpoint executes a query and returns 200."""
    session = AsyncMock()

    async def override_get_db() -> AsyncIterator[AsyncMock]:
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).get("/health/database")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"database": "healthy"}
    session.execute.assert_awaited_once()


def test_lifespan_closes_database_on_shutdown(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Verify the connection pool is disposed when the app shuts down."""
    close_db = AsyncMock()
    monkeypatch.setattr("app.main.close_db", close_db)

    with TestClient(app):
        close_db.assert_not_awaited()

    close_db.assert_awaited_once()
