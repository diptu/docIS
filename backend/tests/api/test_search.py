"""GET /search: parameters, response mapping and the semantic-search switch.

SearchService is replaced by a recorder (it is built inside the route, so
it is patched where the route looks it up); its ranking logic is covered by
integration tests.
"""

from typing import Any

import pytest
from httpx import AsyncClient

from app.api.routes import search as search_route
from app.api.routes.search import get_search_embedder
from app.core.config import Settings
from app.search.embeddings import FastEmbedEmbedder
from app.search.service import (
    Hit,
    SearchMode,
    SemanticSearchUnavailableError,
    Snippet,
)
from tests.api.conftest import Overrides
from tests.support.builders import build_document


class RecordingSearch:
    """Stands in for SearchService; records how it was built and called."""

    calls: list[dict[str, Any]] = []
    results: list[Any] = []
    error: Exception | None = None

    def __init__(self, session: object, embedder: object, min_score: float) -> None:
        self.built = {"embedder": embedder, "min_score": min_score}

    async def search(self, query: str, mode: SearchMode, limit: int) -> list[Any]:
        RecordingSearch.calls.append(
            {**self.built, "query": query, "mode": mode, "limit": limit}
        )
        if RecordingSearch.error:
            raise RecordingSearch.error
        return RecordingSearch.results


@pytest.fixture
def search(monkeypatch: pytest.MonkeyPatch, overrides: Overrides) -> type:
    RecordingSearch.calls, RecordingSearch.results = [], []
    RecordingSearch.error = None
    monkeypatch.setattr(search_route, "SearchService", RecordingSearch)

    async def no_session() -> Any:
        yield None

    overrides[search_route.get_db] = no_session
    overrides[get_search_embedder] = lambda: "embedder"
    return RecordingSearch


async def test_defaults_to_keyword_search(client: AsyncClient, search: Any) -> None:
    """mode=keyword and limit=20 by default; the query is trimmed."""
    response = await client.get("/search", params={"q": "  invoice  "})

    assert response.status_code == 200
    assert response.json() == {"query": "  invoice  ", "mode": "keyword", "hits": []}
    [call] = search.calls
    assert (call["query"], call["mode"], call["limit"]) == (
        "invoice",
        SearchMode.KEYWORD,
        20,
    )


async def test_hits_are_mapped_with_rounded_scores_and_sorted_matches(
    client: AsyncClient, search: Any, settings: Settings
) -> None:
    document = build_document(file_name="inv.pdf")
    search.results = [
        (
            Hit(
                document_id=document.id,
                score=0.123456789,
                matched={"semantic", "keyword", "file_name"},
                snippets=[Snippet("Total \u0002due\u0003", 2, None)],
            ),
            document,
        )
    ]

    response = await client.get(
        "/search", params={"q": "due", "mode": "hybrid", "limit": 5}
    )

    [hit] = response.json()["hits"]
    assert hit["document"] == {
        "id": str(document.id),
        "file_name": "inv.pdf",
        "file_type": "application/pdf",
        "status": "processed",
    }
    assert hit["score"] == 0.123457
    assert hit["matched"] == ["file_name", "keyword", "semantic"]
    assert hit["snippets"] == [
        {"text": "Total \u0002due\u0003", "page_number": 2, "sheet_name": None}
    ]
    assert search.calls[0]["min_score"] == settings.semantic_min_score


async def test_semantic_search_unavailable_is_503(
    client: AsyncClient, search: Any
) -> None:
    search.error = SemanticSearchUnavailableError("disabled")

    response = await client.get("/search", params={"q": "x", "mode": "semantic"})

    assert response.status_code == 503
    assert "use mode=keyword" in response.json()["detail"]


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"q": ""},
        {"q": "x" * 501},
        {"q": "x", "mode": "fuzzy"},
        {"q": "x", "limit": 0},
        {"q": "x", "limit": 101},
    ],
)
async def test_invalid_parameters_are_422(
    client: AsyncClient, search: Any, params: dict[str, object]
) -> None:
    response = await client.get("/search", params=params)

    assert response.status_code == 422
    assert search.calls == []


def test_embedder_dependency_follows_the_setting(settings: Settings) -> None:
    """Disabled: no embedder (keyword only). Enabled: the shared local model
    (constructed lazily: nothing is downloaded until first use)."""
    disabled = settings.model_copy(update={"semantic_search_enabled": False})
    enabled = settings.model_copy(update={"semantic_search_enabled": True})

    assert get_search_embedder(disabled) is None
    embedder = get_search_embedder(enabled)
    assert isinstance(embedder, FastEmbedEmbedder)
    assert get_search_embedder(enabled) is embedder


def test_recording_double_matches_the_real_constructor() -> None:
    """Guard: the double must keep the real SearchService call shape."""
    import inspect

    from app.search.service import SearchService

    real = list(inspect.signature(SearchService.__init__).parameters)
    double = list(inspect.signature(RecordingSearch.__init__).parameters)
    assert len(real) == len(double)
