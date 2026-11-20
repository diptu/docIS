"""DELETE /documents/{id}, POST /documents/delete and GET /documents/stats."""

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.exceptions import ConflictError, NotFoundError
from app.repositories.document import DocumentStats
from app.services.document import DeleteResult


async def test_delete_one_returns_204_with_no_body(
    client: AsyncClient, service: Any
) -> None:
    document_id = uuid.uuid4()

    response = await client.delete(f"/documents/{document_id}")

    assert response.status_code == 204
    assert response.content == b""
    service.delete_one.assert_awaited_once_with(document_id)


@pytest.mark.parametrize(
    ("error", "status"),
    [(NotFoundError("Document x not found."), 404), (ConflictError("busy"), 409)],
)
async def test_delete_one_maps_service_errors(
    client: AsyncClient, service: Any, error: Exception, status: int
) -> None:
    """Unknown documents are 404; documents being processed are 409."""
    service.delete_one.side_effect = error

    response = await client.delete(f"/documents/{uuid.uuid4()}")

    assert response.status_code == status
    assert response.json() == {"detail": str(error)}


async def test_bulk_delete_reports_every_id(client: AsyncClient, service: Any) -> None:
    """The outcome of each requested ID is returned, in request order."""
    deleted, busy, missing = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    service.delete.return_value = DeleteResult(
        deleted=[deleted], in_progress=[busy], not_found=[missing]
    )

    response = await client.post(
        "/documents/delete", json={"ids": [str(deleted), str(busy), str(missing)]}
    )

    assert response.status_code == 200
    assert response.json() == {
        "deleted": [str(deleted)],
        "in_progress": [str(busy)],
        "not_found": [str(missing)],
    }
    service.delete.assert_awaited_once_with([deleted, busy, missing])


@pytest.mark.parametrize(
    "body",
    [
        None,
        {},
        {"ids": []},
        {"ids": ["not-a-uuid"]},
        {"ids": "0b0c4d3e-0000-0000-0000-000000000000"},
        {"ids": [str(uuid.UUID(int=i)) for i in range(201)]},
    ],
)
async def test_bulk_delete_validates_the_request(
    client: AsyncClient, service: Any, body: object
) -> None:
    """1-200 UUIDs are required; nothing is deleted on a bad request."""
    response = await client.post("/documents/delete", json=body)

    assert response.status_code == 422
    service.delete.assert_not_awaited()


async def test_stats_are_returned_as_counted(
    client: AsyncClient, repository: Any
) -> None:
    repository.stats.return_value = DocumentStats(
        total=10, completed=7, failed=2, in_progress=1, avg_processing_seconds=4.25
    )

    response = await client.get("/documents/stats")

    assert response.status_code == 200
    assert response.json() == {
        "total": 10,
        "completed": 7,
        "failed": 2,
        "in_progress": 1,
        "avg_processing_seconds": 4.25,
    }


async def test_stats_route_is_not_mistaken_for_a_document_id(
    client: AsyncClient, repository: Any
) -> None:
    """/documents/stats must not be captured by /documents/{document_id}."""
    repository.stats.return_value = DocumentStats(0, 0, 0, 0, None)

    response = await client.get("/documents/stats")

    assert response.status_code == 200
    assert response.json()["avg_processing_seconds"] is None
    repository.get.assert_not_awaited()
