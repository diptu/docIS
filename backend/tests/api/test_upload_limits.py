"""Upload limits: GET /documents/upload-limits and the request body cap on
POST /documents (UploadSizeLimitMiddleware).

The api ``settings`` fixture allows 64 KiB per file and 3 files, so the
request cap is 3 * (64 + 16) KiB = 240 KiB.
"""

from collections.abc import AsyncIterator
from typing import Any

from httpx import AsyncClient

from tests.support.helpers import PDF_BYTES

CAP = 3 * (64 + 16) * 1024


async def test_limits_endpoint_reports_the_configured_limits(
    client: AsyncClient,
) -> None:
    response = await client.get("/documents/upload-limits")

    assert response.status_code == 200
    assert response.json() == {
        "max_file_size_bytes": 64 * 1024,
        "max_file_size_mb": 0.0625,
        "max_files_per_upload": 3,
    }


async def test_declared_body_over_the_cap_is_refused_before_parsing(
    client: AsyncClient, service: Any
) -> None:
    body = b"x" * (CAP + 1)

    response = await client.post(
        "/documents",
        content=body,
        headers={"Content-Type": "multipart/form-data; boundary=b"},
    )

    assert response.status_code == 413
    assert response.json() == {
        "detail": "Upload too large: the limit is 0.0625 MB per file "
        "and 3 files per upload."
    }
    service.upload.assert_not_awaited()


async def test_streamed_body_is_cut_off_once_it_passes_the_cap(
    client: AsyncClient, service: Any
) -> None:
    """Chunked uploads have no Content-Length; the byte count decides, and
    the rest of the body is never read."""
    sent = 0

    async def chunks() -> AsyncIterator[bytes]:
        nonlocal sent
        yield (
            b"--b\r\n"
            b'Content-Disposition: form-data; name="files"; filename="a.pdf"\r\n'
            b"Content-Type: application/pdf\r\n\r\n"
        )
        for _ in range(100):  # 1.6 MiB, far past the cap
            sent += 1
            yield b"x" * (16 * 1024)

    response = await client.post(
        "/documents",
        content=chunks(),
        headers={"Content-Type": "multipart/form-data; boundary=b"},
    )

    assert response.status_code == 413
    assert "Upload too large" in response.json()["detail"]
    assert sent < 100
    service.upload.assert_not_awaited()


async def test_uploads_within_the_cap_reach_the_route(
    client: AsyncClient, service: Any
) -> None:
    service.upload.return_value = []
    files = [("files", (f"{i}.pdf", PDF_BYTES)) for i in range(3)]

    response = await client.post("/documents", files=files)

    assert response.status_code == 201
    service.upload.assert_awaited_once()


async def test_other_requests_are_not_capped(client: AsyncClient, service: Any) -> None:
    """Only the upload endpoint is limited; a large body elsewhere reaches
    normal validation."""
    response = await client.post(
        "/documents/delete",
        content=b"x" * (CAP + 1),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
