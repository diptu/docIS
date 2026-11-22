"""Health endpoints, application lifespan, and app-wide HTTP behaviour."""

from collections.abc import AsyncIterator, Callable
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import DBAPIError

from app.core.exceptions import (
    DATABASE_UNAVAILABLE,
    INTERNAL_ERROR,
    ConflictError,
    DatabaseUnavailableError,
    NotFoundError,
)
from app.db.session import _connect, get_db
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


def _db_failing_with(
    error: Exception,
) -> Callable[[], AsyncIterator[AsyncMock]]:
    session = AsyncMock()
    session.execute.side_effect = error

    async def mock_db() -> AsyncIterator[AsyncMock]:
        yield session

    return mock_db


async def test_an_unreachable_database_is_a_503_with_retry_after(
    client: AsyncClient, overrides: Overrides
) -> None:
    """PostgreSQL down (e.g. right after a restart): a clear, retryable 503."""
    overrides[get_db] = _db_failing_with(DatabaseUnavailableError())

    response = await client.get("/health/database")

    assert response.status_code == 503
    assert response.json() == {"detail": DATABASE_UNAVAILABLE}
    assert response.headers["retry-after"] == "5"


async def test_a_connection_lost_mid_request_is_a_503(
    client: AsyncClient, overrides: Overrides
) -> None:
    lost = DBAPIError("SELECT 1", {}, OSError("reset"), connection_invalidated=True)
    overrides[get_db] = _db_failing_with(lost)

    response = await client.get("/health/database")

    assert response.status_code == 503
    assert response.json() == {"detail": DATABASE_UNAVAILABLE}


@pytest.mark.parametrize(
    "error",
    [RuntimeError("bug"), DBAPIError("SELECT 1", {}, ValueError("bad sql"))],
)
async def test_unexpected_errors_are_a_json_500(
    overrides: Overrides, error: Exception
) -> None:
    """Not Starlette's plain-text page: clients get a detail to show, and
    internals stay in the server log."""
    overrides[get_db] = _db_failing_with(error)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.get("/health/database")

    assert response.status_code == 500
    assert response.json() == {"detail": INTERNAL_ERROR}


def _dialect(connect: MagicMock) -> MagicMock:
    dialect = MagicMock()
    dialect.loaded_dbapi.connect = connect
    dialect.loaded_dbapi.Error = type("DBAPIErrorBase", (Exception,), {})
    return dialect


@pytest.mark.parametrize(
    "failure",
    [
        OSError("Connect call failed ('127.0.0.1', 5434)"),
        ConnectionRefusedError("refused"),
        TimeoutError("timed out"),
    ],
)
def test_connect_failures_become_database_unavailable(failure: Exception) -> None:
    dialect = _dialect(MagicMock(side_effect=failure))

    with pytest.raises(DatabaseUnavailableError) as raised:
        _connect(dialect, None, (), {"host": "localhost"})

    assert raised.value.__cause__ is failure


def test_postgres_refusing_the_connection_is_database_unavailable() -> None:
    """E.g. "the database system is starting up" or a rejected password."""
    dialect = _dialect(MagicMock())
    dialect.loaded_dbapi.connect.side_effect = dialect.loaded_dbapi.Error("starting")

    with pytest.raises(DatabaseUnavailableError):
        _connect(dialect, None, (), {})


def test_successful_connections_pass_through() -> None:
    connection = object()
    dialect = _dialect(MagicMock(return_value=connection))

    assert _connect(dialect, None, ("a",), {"b": 1}) is connection
    dialect.loaded_dbapi.connect.assert_called_once_with("a", b=1)


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
