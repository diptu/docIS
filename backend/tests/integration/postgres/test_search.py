"""Search on PostgreSQL: full-text search (websearch syntax, English
stemming, ts_headline snippets) and pgvector cosine similarity.

SQLite runs a simpler portable fallback (tests/integration/search); these
tests cover what only the production database does.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.search.service import MARK_END, MARK_START, SearchMode, SearchService
from tests.support.factories import add_indexed_document
from tests.support.fakes import FakeEmbedder

Factory = async_sessionmaker[AsyncSession]


async def _search(
    factory: Factory, query: str, mode: SearchMode = SearchMode.KEYWORD
) -> list[tuple[str, set[str], list[str]]]:
    async with factory() as session:
        service = SearchService(session, FakeEmbedder(), min_semantic_score=0.1)
        results = await service.search(query, mode, 20)
    return [
        (document.file_name, hit.matched, [s.text for s in hit.snippets])
        for hit, document in results
    ]


async def _corpus(factory: Factory) -> None:
    await add_indexed_document(
        factory, "acme.pdf", "Invoices from ACME for consulting services rendered"
    )
    await add_indexed_document(
        factory, "globex.pdf", "Delivery note: twelve laptops, unpaid balance"
    )
    await add_indexed_document(
        factory, "mixed.pdf", "Consulting invoice; laptops included as hardware"
    )


async def test_keyword_search_stems_english_words(pg_factory: Factory) -> None:
    """'invoice' matches 'Invoices' (English stemming), unlike substring scans."""
    await _corpus(pg_factory)

    names = {name for name, _, _ in await _search(pg_factory, "invoice")}

    assert names == {"acme.pdf", "mixed.pdf"}


async def test_keyword_search_supports_websearch_syntax(pg_factory: Factory) -> None:
    """Quoted phrases, OR and -exclusions work as documented."""
    await _corpus(pg_factory)

    async def names(query: str) -> set[str]:
        return {name for name, _, _ in await _search(pg_factory, query)}

    assert await names('"consulting services"') == {"acme.pdf"}
    assert await names("consulting -laptops") == {"acme.pdf"}
    assert await names("acme or delivery") == {"acme.pdf", "globex.pdf"}


async def test_keyword_snippets_mark_matches(pg_factory: Factory) -> None:
    await _corpus(pg_factory)

    [(_, matched, [snippet])] = await _search(pg_factory, "unpaid")

    assert matched == {"keyword"}
    assert f"{MARK_START}unpaid{MARK_END}" in snippet


async def test_file_name_matches_rank_first(pg_factory: Factory) -> None:
    await _corpus(pg_factory)

    results = await _search(pg_factory, "acme")

    assert results[0][0] == "acme.pdf"
    assert results[0][1] == {"file_name", "keyword"}


async def test_semantic_search_uses_pgvector_similarity(pg_factory: Factory) -> None:
    """The closest embedding wins (cosine distance in the HNSW-indexed
    column); results are scored, not keyword-matched."""
    await _corpus(pg_factory)

    results = await _search(pg_factory, "laptops delivery balance", SearchMode.SEMANTIC)

    assert results[0][0] == "globex.pdf"
    assert all(matched == {"semantic"} for _, matched, _ in results)


async def test_hybrid_search_fuses_both_rankings(pg_factory: Factory) -> None:
    await _corpus(pg_factory)

    results = await _search(pg_factory, "laptops", SearchMode.HYBRID)

    top_name, top_matched, _ = results[0]
    assert top_name in {"globex.pdf", "mixed.pdf"}
    assert {"keyword", "semantic"} <= top_matched


async def test_a_query_with_no_matches_returns_nothing(pg_factory: Factory) -> None:
    await _corpus(pg_factory)

    assert await _search(pg_factory, "zebra") == []
