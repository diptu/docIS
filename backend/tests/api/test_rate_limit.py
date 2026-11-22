"""Rate limiting through the HTTP stack (RateLimitMiddleware)."""

from typing import Any

import pytest
from httpx import AsyncClient

from app.core.config import Settings, get_settings
from tests.api.conftest import Overrides


@pytest.fixture
def limited(settings: Settings, overrides: Overrides) -> Settings:
    tight = settings.model_copy(
        update={"rate_limit_per_minute": 3, "rate_limit_processing_per_minute": 2}
    )
    overrides[get_settings] = lambda: tight
    return tight


async def test_requests_over_the_limit_get_429_with_retry_after(
    client: AsyncClient, limited: Settings
) -> None:
    statuses = [
        (await client.get("/documents/upload-limits")).status_code for _ in range(3)
    ]
    refused = await client.get("/documents/upload-limits")

    assert statuses == [200, 200, 200]
    assert refused.status_code == 429
    assert refused.headers["Retry-After"] == "20"
    assert refused.json() == {
        "detail": "Too many requests. Please wait 20 seconds and try again."
    }


async def test_health_checks_are_never_limited(
    client: AsyncClient, limited: Settings
) -> None:
    for _ in range(10):
        assert (await client.get("/health")).status_code == 200


async def test_uploads_have_their_own_smaller_limit(
    client: AsyncClient, limited: Settings, service: Any
) -> None:
    """The third upload in a burst is refused before its body is read;
    the first two reach the upload middleware (413: declared body too big)."""

    async def upload() -> int:
        response = await client.post(
            "/documents",
            content=b"",
            headers={
                "Content-Type": "multipart/form-data; boundary=b",
                "Content-Length": str(10**9),
            },
        )
        return response.status_code

    assert [await upload(), await upload()] == [413, 413]
    refused = await client.post("/documents/0b3d/processing/retry")

    assert refused.status_code == 429
    assert refused.headers["Retry-After"] == "30"
    assert "uploads or retries" in refused.json()["detail"]
    service.upload.assert_not_awaited()


async def test_clients_are_limited_separately_behind_a_trusted_proxy(
    client: AsyncClient, settings: Settings, overrides: Overrides
) -> None:
    proxied = settings.model_copy(
        update={"rate_limit_per_minute": 1, "rate_limit_trusted_proxy_hops": 1}
    )
    overrides[get_settings] = lambda: proxied

    def get(ip: str) -> Any:
        return client.get("/documents/upload-limits", headers={"X-Forwarded-For": ip})

    assert (await get("203.0.113.1")).status_code == 200
    assert (await get("203.0.113.1")).status_code == 429
    assert (await get("203.0.113.2")).status_code == 200


async def test_can_be_disabled(
    client: AsyncClient, settings: Settings, overrides: Overrides
) -> None:
    off = settings.model_copy(
        update={"rate_limit_enabled": False, "rate_limit_per_minute": 1}
    )
    overrides[get_settings] = lambda: off

    for _ in range(5):
        assert (await client.get("/documents/upload-limits")).status_code == 200
