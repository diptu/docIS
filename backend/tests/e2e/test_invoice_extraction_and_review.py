"""E2E: Invoice extraction after content, validation, duplicates, the review queue,
listing, original files and manual corrections.
"""

import uuid
from pathlib import Path

from httpx import AsyncClient

from tests.support.api import upload_and_extract
from tests.support.helpers import (
    CSV_BYTES,
    PNG_BYTES,
    make_text_pdf,
)
from tests.support.worker import Worker


async def test_invoice_is_extracted_after_content(
    client: AsyncClient, run_worker: Worker
) -> None:
    from tests.support.fakes import FakeInvoiceLLM, sample_invoice

    pdf = make_text_pdf("ACME GmbH Invoice INV-2026-001\nTotal due: 1,487.50 EUR")
    document_id = (
        await client.post("/documents", files=[("files", ("inv.pdf", pdf))])
    ).json()[0]["id"]
    llm = FakeInvoiceLLM()

    assert (await client.get(f"/documents/{document_id}/invoice")).status_code == 404
    assert await run_worker(llm) == 2  # content job, then invoice job

    # The model saw the extracted text, wrapped with page markers.
    assert llm.texts == [
        "--- Page 1 ---\nACME GmbH Invoice INV-2026-001\nTotal due: 1,487.50 EUR"
    ]
    job = (await client.get(f"/documents/{document_id}/processing")).json()
    assert (job["kind"], job["status"], job["stage"]) == (
        "extract_invoice",
        "succeeded",
        "saving",
    )
    body = (await client.get(f"/documents/{document_id}/invoice")).json()
    assert body["invoice"] == sample_invoice().model_dump(mode="json")
    assert body["invoice"]["line_items"][0] == {
        "description": "Consulting",
        "product_code": "SVC-1",
        "quantity": 10.0,
        "unit": "h",
        "unit_price": 125.0,
        "tax_rate": 19.0,
        "amount": 1250.0,
    }
    assert body["invoice"]["amount_due"] is None  # missing -> null
    assert body["extraction"] == {
        "model": "claude-opus-5-5",
        "input_tokens": 1200,
        "output_tokens": 300,
        "cache_read_tokens": 800,
    }
    assert (await client.get(f"/documents/{document_id}")).json()[
        "status"
    ] == "processed"


async def test_failed_extraction_keeps_content_and_retries_only_extraction(
    client: AsyncClient, run_worker: Worker
) -> None:
    from app.extraction.llm import ExtractionRefusedError
    from tests.support.fakes import FakeInvoiceLLM

    pdf = make_text_pdf("Invoice INV-9 total 10.00 EUR")
    document_id = (
        await client.post("/documents", files=[("files", ("inv.pdf", pdf))])
    ).json()[0]["id"]

    await run_worker(FakeInvoiceLLM(error=ExtractionRefusedError("declined")))

    job = (await client.get(f"/documents/{document_id}/processing")).json()
    assert (job["kind"], job["status"]) == ("extract_invoice", "failed")
    assert "declined" in job["error"]
    # Content extraction is unaffected.
    assert (await client.get(f"/documents/{document_id}")).json()[
        "status"
    ] == "processed"
    assert (await client.get(f"/documents/{document_id}/pages")).json()[0]["text"]

    retry = (await client.post(f"/documents/{document_id}/processing/retry")).json()
    assert retry["kind"] == "extract_invoice"
    llm = FakeInvoiceLLM()
    assert await run_worker(llm) == 1  # only extraction re-ran, no OCR/parsing

    assert (await client.get(f"/documents/{document_id}/invoice")).status_code == 200
    assert (await client.get(f"/documents/{document_id}")).json()[
        "status"
    ] == "processed"


async def test_invoice_endpoint_404s(client: AsyncClient) -> None:
    assert (await client.get(f"/documents/{uuid.uuid4()}/invoice")).status_code == 404


async def test_invoice_response_has_validation_duplicates_and_review(
    client: AsyncClient, run_worker: Worker
) -> None:
    from tests.support.fakes import FakeInvoiceLLM, sample_invoice

    first = await upload_and_extract(
        client, run_worker, make_text_pdf("Invoice one"), FakeInvoiceLLM()
    )
    body = (await client.get(f"/documents/{first}/invoice")).json()
    assert body["validation"]["status"] == "passed"
    assert [c["code"] for c in body["validation"]["checks"]] == [
        "line_amounts",
        "lines_subtotal",
        "tax_amount",
        "total",
        "amount_due",
    ]
    assert body["duplicates"] == []
    assert body["review"] == {"needs_review": False, "errors": []}

    # The same invoice arrives again, with a wrong total typed in.
    wrong = sample_invoice(total_amount=1500.0)
    second = await upload_and_extract(
        client,
        run_worker,
        make_text_pdf("Invoice one again"),
        FakeInvoiceLLM(wrong),
    )
    body = (await client.get(f"/documents/{second}/invoice")).json()
    assert body["validation"]["status"] == "failed"
    total = next(c for c in body["validation"]["checks"] if c["code"] == "total")
    assert (total["status"], total["expected"], total["actual"]) == (
        "failed",
        "1487.5",
        "1500.0",
    )
    assert body["duplicates"] == [
        {"document_id": first, "file_name": "inv.pdf", "match": "same_invoice_number"}
    ]
    assert body["review"]["needs_review"] is True
    assert [
        (e["code"], e["severity"], e["resolved"]) for e in body["review"]["errors"]
    ] == [("total", "error", False), ("duplicate", "error", False)]

    # The first upload now lists the second as a duplicate, too.
    earlier = (await client.get(f"/documents/{first}/invoice")).json()
    assert [d["document_id"] for d in earlier["duplicates"]] == [second]
    assert earlier["review"]["needs_review"] is True

    queue = (await client.get("/invoices/review")).json()
    assert {item["document_id"] for item in queue} == {first, second}
    item = next(i for i in queue if i["document_id"] == second)
    assert (item["invoice_number"], item["vendor_name"], item["currency"]) == (
        "INV-2026-001",
        "ACME GmbH",
        "EUR",
    )
    assert float(item["total_amount"]) == 1500.0


async def test_review_queue_paging_and_validation(
    client: AsyncClient, run_worker: Worker
) -> None:
    from tests.support.fakes import FakeInvoiceLLM, sample_invoice

    for n in range(3):
        await upload_and_extract(
            client,
            run_worker,
            make_text_pdf(f"Document {n}"),
            FakeInvoiceLLM(sample_invoice(invoice_number=f"N-{n}", is_invoice=False)),
        )

    assert len((await client.get("/invoices/review")).json()) == 3
    assert len((await client.get("/invoices/review", params={"limit": 2})).json()) == 2
    assert len((await client.get("/invoices/review", params={"offset": 2})).json()) == 1
    assert (
        await client.get("/invoices/review", params={"limit": 0})
    ).status_code == 422


def test_validation_status_values() -> None:
    from app.api.routes.documents import validation_status
    from app.schema.document import ValidationCheckRead

    def check(status: str) -> ValidationCheckRead:
        return ValidationCheckRead(
            code="x", status=status, message="", expected=None, actual=None
        )

    assert validation_status([check("passed"), check("failed")]) == "failed"
    assert validation_status([check("passed"), check("skipped")]) == "passed"
    assert validation_status([check("skipped")]) == "not_checked"
    assert validation_status([]) == "not_checked"


async def test_validation_errors_can_be_listed_resolved_and_reopened(
    client: AsyncClient, run_worker: Worker
) -> None:
    from tests.support.fakes import FakeInvoiceLLM, sample_invoice

    bad = sample_invoice(total_amount=1500.0, uncertain_fields=["invoice_date"])
    document_id = await upload_and_extract(
        client, run_worker, make_text_pdf("Invoice"), FakeInvoiceLLM(bad)
    )

    errors = (await client.get(f"/documents/{document_id}/validation-errors")).json()
    assert [(e["code"], e["severity"]) for e in errors] == [
        ("total", "error"),  # errors before warnings
        ("model_uncertain", "warning"),
    ]
    assert errors[0]["details"] == {"expected": "1487.5", "actual": "1500.0"}
    assert {
        item["document_id"] for item in (await client.get("/invoices/review")).json()
    } == {document_id}

    # Resolve both: the document leaves the review queue.
    for error in errors:
        response = await client.patch(
            f"/documents/{document_id}/validation-errors/{error['id']}",
            json={"resolved": True},
        )
        assert response.status_code == 200
        assert response.json()["resolved"] is True
        assert response.json()["resolved_at"] is not None
    review = (await client.get(f"/documents/{document_id}/invoice")).json()["review"]
    assert review["needs_review"] is False
    assert all(e["resolved"] for e in review["errors"])
    assert (await client.get("/invoices/review")).json() == []

    # Reopen one: back in the queue, with only the unresolved error listed.
    await client.patch(
        f"/documents/{document_id}/validation-errors/{errors[0]['id']}",
        json={"resolved": False},
    )
    [item] = (await client.get("/invoices/review")).json()
    assert [e["code"] for e in item["errors"]] == ["total"]


async def test_validation_error_endpoints_404(
    client: AsyncClient, run_worker: Worker
) -> None:
    from tests.support.fakes import FakeInvoiceLLM

    missing = uuid.uuid4()
    assert (
        await client.get(f"/documents/{missing}/validation-errors")
    ).status_code == 404
    document_id = await upload_and_extract(
        client, run_worker, make_text_pdf("Invoice"), FakeInvoiceLLM()
    )
    assert (
        await client.get(f"/documents/{document_id}/validation-errors")
    ).json() == []
    response = await client.patch(
        f"/documents/{document_id}/validation-errors/{missing}", json={"resolved": True}
    )
    assert response.status_code == 404
    assert (
        await client.patch(
            f"/documents/{document_id}/validation-errors/{missing}", json={}
        )
    ).status_code == 422


async def test_list_documents_with_summaries_and_filters(
    client: AsyncClient, run_worker: Worker
) -> None:
    from tests.support.fakes import FakeInvoiceLLM, sample_invoice

    good = await upload_and_extract(
        client, run_worker, make_text_pdf("Invoice A"), FakeInvoiceLLM()
    )
    bad = await upload_and_extract(
        client,
        run_worker,
        make_text_pdf("Invoice B"),
        FakeInvoiceLLM(sample_invoice(invoice_number="B-1", total_amount=9.0)),
    )
    csv_doc = (
        await client.post("/documents", files=[("files", ("data.csv", CSV_BYTES))])
    ).json()[0]["id"]  # not processed yet

    page = (await client.get("/documents")).json()
    assert page["total"] == 3
    assert [item["id"] for item in page["items"]] == [
        csv_doc,
        bad,
        good,
    ]  # newest first
    by_id = {item["id"]: item for item in page["items"]}
    assert by_id[good]["processing"] == {
        "kind": "extract_invoice",
        "status": "succeeded",
        "stage": "saving",
        "percent": 100.0,
        "attempts": 1,
        "max_attempts": 3,
        "retry_scheduled": False,
        "error": None,
    }
    assert by_id[good]["invoice"]["invoice_number"] == "INV-2026-001"
    assert by_id[good]["invoice"]["corrected"] is False
    assert (by_id[good]["unresolved_errors"], by_id[bad]["unresolved_errors"]) == (0, 1)
    assert by_id[csv_doc]["invoice"] is None
    assert by_id[csv_doc]["processing"]["status"] == "queued"

    async def ids(**params: object) -> list[str]:
        return [
            i["id"]
            for i in (await client.get("/documents", params=params)).json()["items"]
        ]

    assert await ids(needs_review="true") == [bad]
    assert await ids(needs_review="false") == [csv_doc, good]
    assert await ids(file_type="text/csv") == [csv_doc]
    assert await ids(status="uploaded") == [csv_doc]
    assert await ids(limit=1, offset=1) == [bad]
    assert (
        await client.get("/documents", params={"status": "bogus"})
    ).status_code == 422


async def test_original_file_is_served_inline_and_sandboxed(
    client: AsyncClient,
) -> None:
    pdf = make_text_pdf("Original bytes")
    document_id = (
        await client.post("/documents", files=[("files", ("Q3 invoice.pdf", pdf))])
    ).json()[0]["id"]

    response = await client.get(f"/documents/{document_id}/file")

    assert response.status_code == 200
    assert response.content == pdf
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline;")
    assert "Q3%20invoice.pdf" in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"
    # Browsers' PDF viewers refuse sandboxed documents.
    assert "content-security-policy" not in response.headers

    image_id = (
        await client.post("/documents", files=[("files", ("scan.png", PNG_BYTES))])
    ).json()[0]["id"]
    image = await client.get(f"/documents/{image_id}/file")
    assert image.headers["content-type"] == "image/png"
    assert image.headers["content-security-policy"] == "sandbox"

    download = await client.get(
        f"/documents/{document_id}/file", params={"download": "true"}
    )
    assert download.headers["content-disposition"].startswith("attachment;")


async def test_file_endpoint_404s(client: AsyncClient, tmp_path: Path) -> None:
    assert (await client.get(f"/documents/{uuid.uuid4()}/file")).status_code == 404

    document = (
        await client.post(
            "/documents", files=[("files", ("a.pdf", make_text_pdf("x")))]
        )
    ).json()[0]
    stored = tmp_path / "storage" / document["storage_path"]
    stored.chmod(0o600)
    stored.unlink()

    response = await client.get(f"/documents/{document['id']}/file")
    assert response.status_code == 404
    assert "missing" in response.json()["detail"]


async def test_correct_invoice_via_api(client: AsyncClient, run_worker: Worker) -> None:
    from tests.support.fakes import FakeInvoiceLLM, sample_invoice

    document_id = await upload_and_extract(
        client,
        run_worker,
        make_text_pdf("Invoice"),
        FakeInvoiceLLM(sample_invoice(total_amount=1500.0)),
    )
    before = (await client.get(f"/documents/{document_id}/invoice")).json()
    assert before["review"]["needs_review"] is True
    assert before["corrected_at"] is None

    edited = before["invoice"] | {"total_amount": 1487.5}
    response = await client.put(f"/documents/{document_id}/invoice", json=edited)

    assert response.status_code == 200
    after = response.json()
    assert after["invoice"]["total_amount"] == 1487.5
    assert after["corrected_at"] is not None
    assert after["validation"]["status"] == "passed"
    assert after["review"] == {"needs_review": False, "errors": []}
    [history] = (
        await client.get(f"/documents/{document_id}/invoice/corrections")
    ).json()
    assert history["changes"] == {"total_amount": {"old": 1500.0, "new": 1487.5}}
    listed = (await client.get("/documents")).json()["items"][0]
    assert listed["invoice"]["corrected"] is True


async def test_correction_rejects_invalid_invoices(
    client: AsyncClient, run_worker: Worker
) -> None:
    from tests.support.fakes import FakeInvoiceLLM

    document_id = await upload_and_extract(
        client, run_worker, make_text_pdf("Invoice"), FakeInvoiceLLM()
    )
    invoice = (await client.get(f"/documents/{document_id}/invoice")).json()["invoice"]

    for bad in (
        invoice | {"invoice_date": "03/11/2026"},
        invoice | {"total_amount": "lots"},
        {k: v for k, v in invoice.items() if k != "currency"},
        invoice | {"surprise": True},
    ):
        assert (
            await client.put(f"/documents/{document_id}/invoice", json=bad)
        ).status_code == 422


async def test_correction_and_history_404s(client: AsyncClient) -> None:
    from tests.support.fakes import sample_invoice

    payload = sample_invoice().model_dump(mode="json")
    missing = uuid.uuid4()
    assert (
        await client.put(f"/documents/{missing}/invoice", json=payload)
    ).status_code == 404
    assert (
        await client.get(f"/documents/{missing}/invoice/corrections")
    ).status_code == 404

    document_id = (
        await client.post(
            "/documents", files=[("files", ("a.pdf", make_text_pdf("x")))]
        )
    ).json()[0]["id"]
    response = await client.put(f"/documents/{document_id}/invoice", json=payload)
    assert response.status_code == 404
    assert "no extracted invoice" in response.json()["detail"]


async def test_list_documents_when_there_are_none(client: AsyncClient) -> None:
    assert (await client.get("/documents")).json() == {
        "items": [],
        "total": 0,
        "offset": 0,
        "limit": 50,
    }
