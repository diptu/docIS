"""SearchService on SQLite (portable fallback paths) with a fake embedder."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Document, DocumentChunk, DocumentStatus
from app.search.service import (
    MARK_END,
    MARK_START,
    SearchMode,
    SearchService,
    SemanticSearchUnavailableError,
)
from tests.fakes import FakeEmbedder

Factory = async_sessionmaker[AsyncSession]
EMBEDDER = FakeEmbedder()


async def add(factory: Factory, file_name: str, *texts: str) -> uuid.UUID:
    document_id = uuid.uuid4()
    async with factory() as session:
        session.add(
            Document(
                id=document_id,
                file_name=file_name,
                file_type="application/pdf",
                storage_path="x",
                status=DocumentStatus.PROCESSED,
            )
        )
        await session.flush()
        for index, text in enumerate(texts):
            session.add(
                DocumentChunk(
                    document_id=document_id,
                    chunk_index=index,
                    page_number=index + 1,
                    content=text,
                    embedding=EMBEDDER.embed_passages([text])[0],
                )
            )
        await session.commit()
    return document_id


async def search(
    factory: Factory,
    query: str,
    mode: SearchMode = SearchMode.KEYWORD,
    **kwargs: object,
) -> list[tuple[str, set[str]]]:
    async with factory() as session:
        service = SearchService(session, kwargs.get("embedder", EMBEDDER), 0.1)  # type: ignore[arg-type]
        results = await service.search(query, mode, 20)
    return [(document.file_name, hit.matched) for hit, document in results]


@pytest.mark.asyncio
async def test_keyword_search_requires_every_term(session_factory: Factory) -> None:
    await add(session_factory, "a.pdf", "Invoice from ACME GmbH for consulting")
    await add(session_factory, "b.pdf", "Invoice from Globex for hardware")

    assert await search(session_factory, "invoice acme") == [("a.pdf", {"keyword"})]
    assert [n for n, _ in await search(session_factory, "INVOICE")] == [
        "a.pdf",
        "b.pdf",
    ]
    assert await search(session_factory, "nothing-like-this") == []


@pytest.mark.asyncio
async def test_keyword_snippets_are_marked_and_located(
    session_factory: Factory,
) -> None:
    await add(
        session_factory, "a.pdf", "intro " * 40, "Payment terms: Net 30 days via SEPA"
    )

    async with session_factory() as session:
        [(hit, _)] = await SearchService(session, None).search(
            "sepa", SearchMode.KEYWORD, 5
        )

    [snippet] = hit.snippets
    assert f"{MARK_START}SEPA{MARK_END}" in snippet.text
    assert snippet.page_number == 2


@pytest.mark.asyncio
async def test_file_names_are_searched_too(session_factory: Factory) -> None:
    await add(session_factory, "acme-q3-statement.pdf", "Balance overview")
    await add(session_factory, "other.pdf", "Mentions acme once")

    results = await search(session_factory, "acme")

    # The file-name match ranks first.
    assert results == [
        ("acme-q3-statement.pdf", {"file_name"}),
        ("other.pdf", {"keyword"}),
    ]


@pytest.mark.asyncio
async def test_semantic_search_ranks_by_similarity(session_factory: Factory) -> None:
    await add(
        session_factory, "consulting.pdf", "consulting services hours invoice total"
    )
    await add(
        session_factory, "steel.pdf", "delivery note steel bolts warehouse pallets"
    )

    results = await search(
        session_factory, "steel warehouse delivery", SearchMode.SEMANTIC
    )

    assert results[0] == ("steel.pdf", {"semantic"})


@pytest.mark.asyncio
async def test_semantic_results_below_the_threshold_are_dropped(
    session_factory: Factory,
) -> None:
    await add(session_factory, "a.pdf", "completely unrelated words here")

    async with session_factory() as session:
        service = SearchService(session, EMBEDDER, min_semantic_score=0.9)
        assert await service.search("pallets", SearchMode.SEMANTIC, 10) == []


@pytest.mark.asyncio
async def test_hybrid_combines_both_rankings(session_factory: Factory) -> None:
    await add(session_factory, "both.pdf", "steel bolts delivery")
    await add(session_factory, "semantic-only.pdf", "bolts delivery pallets")

    results = await search(session_factory, "steel bolts", SearchMode.HYBRID)

    assert results[0] == ("both.pdf", {"keyword", "semantic"})
    assert ("semantic-only.pdf", {"semantic"}) in results


@pytest.mark.asyncio
async def test_semantic_needs_an_embedder(session_factory: Factory) -> None:
    async with session_factory() as session:
        service = SearchService(session, None)
        with pytest.raises(SemanticSearchUnavailableError):
            await service.search("x", SearchMode.SEMANTIC, 5)
        assert await service.search("!!!", SearchMode.KEYWORD, 5) == []


@pytest.mark.asyncio
async def test_limit_and_snippet_cap(session_factory: Factory) -> None:
    for i in range(5):
        await add(session_factory, f"{i}.pdf", *[f"invoice part {n}" for n in range(6)])

    async with session_factory() as session:
        results = await SearchService(session, None).search(
            "invoice", SearchMode.KEYWORD, 3
        )

    assert len(results) == 3
    assert all(len(hit.snippets) == 3 for hit, _ in results)
