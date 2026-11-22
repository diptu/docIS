"""End-to-end: upload → worker indexing → GET /search and GET /compare
(SQLite database, deterministic fake embedder instead of the real model)."""

import uuid

import pytest
from httpx import AsyncClient

from app.api.routes.search import get_search_embedder
from app.core.config import Settings, get_settings
from app.main import app
from tests.support.api import upload
from tests.support.fakes import FakeInvoiceLLM, sample_invoice
from tests.support.helpers import make_text_pdf, make_xlsx
from tests.support.worker import Worker

MARK_START, MARK_END = "\u0002", "\u0003"


CONSULTING_CSV = (
    b"Service,Hours,Rate\r\nStrategy consulting workshop,10,125.00\r\n"
    b"Advisory retainer,5,90.00\r\n"
)
HARDWARE_PDF = make_text_pdf(
    "Delivery note\nHardware shipment: 12 laptops and 4 docking stations"
)


# --------------------------------------------------------------------- search


async def test_keyword_search_finds_documents_by_their_content(
    client: AsyncClient, run_worker: Worker
) -> None:
    consulting = await upload(client, "services.csv", CONSULTING_CSV)
    await upload(client, "delivery.pdf", HARDWARE_PDF)
    await run_worker()

    response = await client.get(
        "/search", params={"q": "consulting", "mode": "keyword"}
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["query"], body["mode"]) == ("consulting", "keyword")
    [hit] = body["hits"]
    assert hit["document"] == {
        "id": consulting,
        "file_name": "services.csv",
        "file_type": "text/csv",
        "status": "processed",
    }
    assert hit["matched"] == ["keyword"]
    [snippet] = hit["snippets"]
    assert f"{MARK_START}consulting{MARK_END}" in snippet["text"]
    assert snippet["sheet_name"] is not None
    assert snippet["page_number"] is None


async def test_keyword_search_locates_pdf_pages_and_matches_file_names(
    client: AsyncClient, run_worker: Worker
) -> None:
    delivery = await upload(client, "delivery.pdf", HARDWARE_PDF)
    await run_worker()

    by_content = (await client.get("/search", params={"q": "laptops"})).json()
    [hit] = by_content["hits"]
    assert by_content["mode"] == "keyword"  # the default
    assert hit["document"]["id"] == delivery
    assert hit["snippets"][0]["page_number"] == 1

    by_name = (await client.get("/search", params={"q": "delivery"})).json()
    assert by_name["hits"][0]["matched"] == ["file_name", "keyword"]


async def test_semantic_search_ranks_the_closest_document_first(
    client: AsyncClient, run_worker: Worker
) -> None:
    consulting = await upload(client, "services.csv", CONSULTING_CSV)
    delivery = await upload(client, "delivery.pdf", HARDWARE_PDF)
    await run_worker()

    hits = (
        await client.get(
            "/search", params={"q": "laptops docking hardware", "mode": "semantic"}
        )
    ).json()["hits"]

    assert hits[0]["document"]["id"] == delivery
    assert hits[0]["matched"] == ["semantic"]
    assert consulting not in [h["document"]["id"] for h in hits[:1]]
    assert hits == sorted(hits, key=lambda h: -h["score"])


async def test_hybrid_search_reports_both_kinds_of_match(
    client: AsyncClient, run_worker: Worker
) -> None:
    await upload(client, "delivery.pdf", HARDWARE_PDF)
    await run_worker()

    [hit] = (
        await client.get(
            "/search", params={"q": "laptops", "mode": "hybrid", "limit": 1}
        )
    ).json()["hits"]

    assert {"keyword", "semantic"} <= set(hit["matched"])


async def test_search_with_no_matches_returns_no_hits(
    client: AsyncClient, run_worker: Worker
) -> None:
    await upload(client, "services.csv", CONSULTING_CSV)
    await run_worker()

    response = await client.get("/search", params={"q": "zebra"})

    assert response.status_code == 200
    assert response.json()["hits"] == []


async def test_deleted_documents_are_no_longer_found(
    client: AsyncClient, run_worker: Worker
) -> None:
    consulting = await upload(client, "services.csv", CONSULTING_CSV)
    await run_worker()
    assert (await client.get("/search", params={"q": "consulting"})).json()["hits"]

    assert (await client.delete(f"/documents/{consulting}")).status_code == 204

    assert (await client.get("/search", params={"q": "consulting"})).json()[
        "hits"
    ] == []


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"q": ""},
        {"q": "x" * 501},
        {"q": "invoice", "mode": "fuzzy"},
        {"q": "invoice", "limit": 0},
        {"q": "invoice", "limit": 101},
    ],
)
async def test_search_validates_its_parameters(
    client: AsyncClient, params: dict[str, object]
) -> None:
    assert (await client.get("/search", params=params)).status_code == 422


async def test_semantic_search_is_503_when_disabled(
    client: AsyncClient, settings: Settings
) -> None:
    del app.dependency_overrides[get_search_embedder]
    app.dependency_overrides[get_settings] = lambda: settings.model_copy(
        update={"semantic_search_enabled": False}
    )

    semantic = await client.get("/search", params={"q": "invoice", "mode": "semantic"})
    keyword = await client.get("/search", params={"q": "invoice", "mode": "keyword"})

    assert semantic.status_code == 503
    assert "mode=keyword" in semantic.json()["detail"]
    assert keyword.status_code == 200


# -------------------------------------------------------------------- compare


async def test_compare_two_extracted_invoices(
    client: AsyncClient, run_worker: Worker
) -> None:
    original = await upload(
        client,
        "a.pdf",
        make_text_pdf("Invoice INV-2026-001\nACME GmbH\nTotal 1,487.50 EUR"),
    )
    await run_worker(FakeInvoiceLLM())
    revised = await upload(
        client,
        "b.pdf",
        make_text_pdf("Invoice INV-2026-001\nACME GmbH\nTotal 1,500.00 EUR"),
    )
    await run_worker(FakeInvoiceLLM(sample_invoice(total_amount=1500.0)))

    response = await client.get("/compare", params={"left": original, "right": revised})

    assert response.status_code == 200
    body = response.json()
    assert (body["left"]["id"], body["right"]["id"]) == (original, revised)
    invoice = body["invoice"]
    changed = {
        f["field"]
        for f in invoice["fields"]
        if f["status"] not in ("equal", "both_missing")
    }
    assert changed == {"total_amount"}
    assert invoice["fields_different"] == 1
    text = body["text"]
    assert 0 < text["similarity"] < 1
    assert text["added"] >= 1 and text["removed"] >= 1
    assert body["rows"] is None


async def test_compare_spreadsheet_rows_against_a_pdf(
    client: AsyncClient, run_worker: Worker
) -> None:
    sheet = await upload(
        client, "rows.csv", b"Item,Qty\r\nlaptops,12\r\nmonitors,3\r\n"
    )
    pdf = await upload(client, "delivery.pdf", HARDWARE_PDF)
    await run_worker()

    body = (await client.get("/compare", params={"left": sheet, "right": pdf})).json()

    rows = body["rows"]
    assert rows["spreadsheet"] == "left"
    assert rows["found"] + rows["partial"] + rows["missing"] == len(rows["rows"])
    assert rows["missing"] >= 1  # monitors are not on the delivery note
    assert body["invoice"] is None
    assert any("No invoice comparison" in note for note in body["notes"])


async def test_compare_rejects_the_same_document_and_unknown_ids(
    client: AsyncClient,
) -> None:
    document = await upload(client, "rows.csv", CONSULTING_CSV)

    same = await client.get("/compare", params={"left": document, "right": document})
    missing = await client.get(
        "/compare", params={"left": document, "right": str(uuid.uuid4())}
    )
    invalid = await client.get("/compare", params={"left": document, "right": "nope"})

    assert same.status_code == 422
    assert "two different documents" in same.json()["detail"]
    assert missing.status_code == 404
    assert invalid.status_code == 422


async def test_compare_explains_what_could_not_be_compared(
    client: AsyncClient, run_worker: Worker
) -> None:
    """Unprocessed documents get no sections, only notes saying why; two
    spreadsheets get no row check (it needs one document with page text)."""
    first = await upload(client, "a.csv", CONSULTING_CSV)
    second = await upload(client, "b.csv", b"Item,Qty\r\nlaptops,12\r\n")

    before = (
        await client.get("/compare", params={"left": first, "right": second})
    ).json()
    await run_worker()
    after = (
        await client.get("/compare", params={"left": first, "right": second})
    ).json()

    assert (before["invoice"], before["text"], before["rows"]) == (None, None, None)
    assert any("no extracted text" in note for note in before["notes"])
    assert after["rows"] is None
    assert any("No row check" in note for note in after["notes"])


# ------------------------------------------------- compare many documents


async def test_compare_many_documents_of_different_types(
    client: AsyncClient, run_worker: Worker
) -> None:
    """A PDF and a CSV invoice that disagree on a line and the total, plus
    a workbook with no extracted invoice (unavailable, not missing)."""
    pdf = await upload(client, "invoice.pdf", make_text_pdf("Invoice INV-2026-001"))
    await run_worker(FakeInvoiceLLM())
    revised = sample_invoice(total_amount=1500.0)
    revised.line_items[0] = revised.line_items[0].model_copy(
        update={"quantity": 10.1, "amount": 1262.5}
    )
    csv = await upload(client, "invoice.csv", CONSULTING_CSV)
    await run_worker(FakeInvoiceLLM(revised))
    xlsx = await upload(client, "export.xlsx", make_xlsx())
    await run_worker()  # content only: no invoice for the workbook

    response = await client.get("/compare/documents", params={"ids": [pdf, csv, xlsx]})

    assert response.status_code == 200
    body = response.json()
    assert [d["id"] for d in body["documents"]] == [pdf, csv, xlsx]
    assert [d["reference"] for d in body["documents"]] == [True, False, False]
    assert [d["invoice_available"] for d in body["documents"]] == [True, True, False]
    fields = {f["field"]: f for f in body["fields"]}
    total = fields["total_amount"]
    assert total["status"] == "mismatch"
    assert [c["value"] for c in total["cells"]] == ["1487.50", "1500.00", None]
    assert [c["difference"] for c in total["cells"]] == ["0.00", "+12.50", None]
    assert [c["presence"] for c in total["cells"]][2] == "unavailable"
    assert fields["invoice_number"]["status"] == "match"
    mismatched = {name for name, f in fields.items() if f["status"] == "mismatch"}
    assert mismatched == {"total_amount"}

    [group] = body["line_items"]
    assert group["status"] == "mismatch"
    assert [ref["matched_by"] for ref in group["lines"]] == [
        "reference",
        "product_code",
        None,
    ]
    line_fields = {f["field"]: f for f in group["fields"]}
    assert line_fields["quantity"]["cells"][1]["difference"] == "+0.1"
    assert line_fields["amount"]["cells"][1]["difference"] == "+12.50"
    assert line_fields["unit_price"]["status"] == "match"
    assert body["field_counts"]["mismatch"] == 1
    assert body["line_counts"] == {
        "match": 0,
        "mismatch": 1,
        "missing": 0,
        "unavailable": 0,
    }
    assert body["notes"] == ["export.xlsx: no invoice has been extracted from it."]


async def test_compare_many_requires_processed_distinct_documents(
    client: AsyncClient, run_worker: Worker
) -> None:
    first = await upload(client, "a.csv", CONSULTING_CSV)
    second = await upload(client, "b.csv", b"Item,Qty\r\nlaptops,12\r\n")

    pending = await client.get("/compare/documents", params={"ids": [first, second]})
    await run_worker()
    one = await client.get("/compare/documents", params={"ids": [first]})
    repeated = await client.get("/compare/documents", params={"ids": [first, first]})
    unknown = await client.get(
        "/compare/documents", params={"ids": [first, str(uuid.uuid4())]}
    )
    ready = await client.get("/compare/documents", params={"ids": [first, second]})

    assert pending.status_code == 409
    assert "not finished processing" in pending.json()["detail"]
    assert one.status_code == 422
    assert "between 2 and 10" in one.json()["detail"]
    assert repeated.status_code == 422
    assert unknown.status_code == 404
    assert ready.status_code == 200
    assert ready.json()["fields"] == []  # neither has an invoice
