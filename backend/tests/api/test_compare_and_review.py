"""GET /compare request validation and GET /invoices/review mapping.

Comparison logic itself is unit-tested (tests/unit/compare) and exercised
end to end (tests/e2e/test_search_and_compare.py).
"""

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from tests.support.builders import (
    build_document,
    build_invoice,
    build_validation_error,
)


async def test_compare_rejects_the_same_document_twice(
    client: AsyncClient, repository: Any
) -> None:
    same = str(uuid.uuid4())

    response = await client.get("/compare", params={"left": same, "right": same})

    assert response.status_code == 422
    assert response.json() == {"detail": "Choose two different documents to compare."}
    repository.get.assert_not_awaited()


@pytest.mark.parametrize("missing_side", ["left", "right"])
async def test_compare_unknown_document_is_404(
    client: AsyncClient, repository: Any, missing_side: str
) -> None:
    known = build_document()
    unknown = uuid.uuid4()
    repository.get.side_effect = lambda i: known if i == known.id else None
    params = {"left": str(known.id), "right": str(known.id)}
    params[missing_side] = str(unknown)

    response = await client.get("/compare", params=params)

    assert response.status_code == 404
    assert response.json() == {"detail": f"Document {unknown} not found."}


@pytest.mark.parametrize(
    "params", [{}, {"left": str(uuid.uuid4())}, {"left": "x", "right": "y"}]
)
async def test_compare_requires_two_uuids(
    client: AsyncClient, repository: Any, params: dict[str, str]
) -> None:
    response = await client.get("/compare", params=params)

    assert response.status_code == 422


async def test_review_queue_lists_invoices_with_open_errors(
    client: AsyncClient, repository: Any
) -> None:
    document = build_document(file_name="dup.pdf")
    invoice = build_invoice(document.id)
    error = build_validation_error(document.id, code="duplicate")
    repository.list_for_review.return_value = [(invoice, document, [error])]

    response = await client.get("/invoices/review", params={"offset": 10, "limit": 5})

    assert response.status_code == 200
    [item] = response.json()
    assert item["document_id"] == str(document.id)
    assert (item["file_name"], item["invoice_number"], item["vendor_name"]) == (
        "dup.pdf",
        "INV-2026-001",
        "ACME GmbH",
    )
    assert (item["total_amount"], item["currency"]) == ("1487.50", "EUR")
    assert [e["code"] for e in item["errors"]] == ["duplicate"]
    repository.list_for_review.assert_awaited_once_with(10, 5)


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"offset": -1}])
async def test_review_queue_paging_is_validated(
    client: AsyncClient, repository: Any, params: dict[str, int]
) -> None:
    response = await client.get("/invoices/review", params=params)

    assert response.status_code == 422
    repository.list_for_review.assert_not_awaited()


@pytest.mark.parametrize("count", [1, 11])
async def test_compare_many_limits_the_number_of_documents(
    client: AsyncClient, repository: Any, count: int
) -> None:
    ids = [str(uuid.uuid4()) for _ in range(count)]

    response = await client.get("/compare/documents", params={"ids": ids})

    assert response.status_code == 422
    assert response.json() == {
        "detail": "Choose between 2 and 10 documents to compare."
    }
    repository.get.assert_not_awaited()


async def test_compare_many_maps_a_non_invoice_to_unavailable(
    client: AsyncClient, repository: Any
) -> None:
    invoice_doc = build_document(file_name="a.pdf")
    letter_doc = build_document(file_name="letter.pdf")
    documents = {d.id: d for d in (invoice_doc, letter_doc)}
    invoices = {
        invoice_doc.id: build_invoice(invoice_doc.id),
        letter_doc.id: build_invoice(letter_doc.id, is_invoice=False),
    }
    repository.get.side_effect = documents.get
    repository.get_invoice.side_effect = invoices.get

    response = await client.get(
        "/compare/documents",
        params={"ids": [str(invoice_doc.id), str(letter_doc.id)]},
    )

    assert response.status_code == 200
    body = response.json()
    assert [d["invoice_available"] for d in body["documents"]] == [True, False]
    assert body["notes"][0] == "letter.pdf: not recognised as an invoice."
    assert {f["status"] for f in body["fields"]} == {"unavailable"}
    total = next(f for f in body["fields"] if f["field"] == "total_amount")
    assert [c["presence"] for c in total["cells"]] == ["present", "unavailable"]
