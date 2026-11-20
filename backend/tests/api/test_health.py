"""Health endpoints, application lifespan, and app-wide HTTP behaviour."""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from app.core.exceptions import ConflictError, NotFoundError
from app.db.session import get_db
from app.main import app
from tests.api.conftest import Overrides


async def test_health_reports_healthy(client: AsyncClient) -> None:
    """GET /health answers without touching any dependency."""
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


async def test_database_health_runs_a_query(
    client: AsyncClient, overrides: Overrides
) -> None:
    """GET /health/database executes a query on the session."""
    session = AsyncMock()

    async def mock_db() -> AsyncIterator[AsyncMock]:
        yield session

    overrides[get_db] = mock_db

    response = await client.get("/health/database")

    assert response.status_code == 200
    assert response.json() == {"database": "healthy"}
    session.execute.assert_awaited_once()


async def test_database_health_propagates_a_database_outage(
    client: AsyncClient, overrides: Overrides
) -> None:
    """A failing database surfaces as a server error, not a healthy reply."""
    session = AsyncMock()
    session.execute.side_effect = ConnectionRefusedError("db down")

    async def mock_db() -> AsyncIterator[AsyncMock]:
        yield session

    overrides[get_db] = mock_db

    with pytest.raises(ConnectionRefusedError):
        await client.get("/health/database")


async def test_lifespan_disposes_the_pool_on_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The connection pool is closed when the app shuts down, not before."""
    close_db = AsyncMock()
    monkeypatch.setattr("app.main.close_db", close_db)

    async with app.router.lifespan_context(app):
        close_db.assert_not_awaited()

    close_db.assert_awaited_once()


@pytest.mark.parametrize(
    ("error", "status"), [(NotFoundError("gone"), 404), (ConflictError("busy"), 409)]
)
async def test_app_errors_become_json_detail_responses(
    client: AsyncClient, service: AsyncMock, error: Exception, status: int
) -> None:
    """AppError subclasses map to their status with a client-safe detail."""
    service.retry_processing.side_effect = error

    response = await client.post(
        "/documents/00000000-0000-0000-0000-000000000001/processing/retry"
    )

    assert response.status_code == status
    assert response.json() == {"detail": str(error)}


async def test_unknown_routes_and_methods(client: AsyncClient) -> None:
    """Unknown paths are 404 and unsupported methods 405."""
    assert (await client.get("/nope")).status_code == 404
    assert (await client.put("/health")).status_code == 405


async def test_openapi_schema_lists_every_route_group(client: AsyncClient) -> None:
    """The OpenAPI document builds (catches schema/annotation regressions)."""
    response = await client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    for path in (
        "/documents",
        "/documents/stats",
        "/documents/delete",
        "/documents/{document_id}",
        "/invoices/review",
        "/search",
        "/compare",
    ):
        assert path in paths
