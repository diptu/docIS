"""
Searching document content.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import ServiceUnavailableError
from app.db.session import get_db
from app.schema.document import (
    DocumentBrief,
    SearchHitRead,
    SearchResultsRead,
    SearchSnippetRead,
)
from app.search.embeddings import Embedder, get_embedder
from app.search.service import SearchMode, SearchService, SemanticSearchUnavailableError

router = APIRouter(prefix="/search", tags=["Search"])


def get_search_embedder(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Embedder | None:
    """The query embedder, or None when semantic search is disabled."""
    if not settings.semantic_search_enabled:
        return None
    return get_embedder(settings.embedding_cache_dir)


@router.get("", summary="Search documents")
async def search_documents(
    q: Annotated[str, Query(min_length=1, max_length=500, description="Search text")],
    session: Annotated[AsyncSession, Depends(get_db)],
    embedder: Annotated[Embedder | None, Depends(get_search_embedder)],
    settings: Annotated[Settings, Depends(get_settings)],
    mode: Annotated[SearchMode, Query(description="keyword, semantic or hybrid")] = (
        SearchMode.KEYWORD
    ),
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> SearchResultsRead:
    """Find documents by their extracted text (pages and spreadsheet cells)
    and file names.

    * `keyword`: full-text search with stemming; supports `"exact phrase"`,
      `or` and `-excluded` words. Snippets mark matches with U+0002/U+0003.
    * `semantic`: finds passages by meaning, e.g. "unpaid consulting work"
      finds an invoice for "advisory services".
    * `hybrid`: both, combined by reciprocal rank fusion.
    """
    service = SearchService(session, embedder, settings.semantic_min_score)
    try:
        results = await service.search(q.strip(), mode, limit)
    except SemanticSearchUnavailableError as exc:
        raise ServiceUnavailableError(
            "Semantic search is disabled (SEMANTIC_SEARCH_ENABLED=false); "
            "use mode=keyword."
        ) from exc
    return SearchResultsRead(
        query=q,
        mode=mode,
        hits=[
            SearchHitRead(
                document=DocumentBrief.model_validate(document),
                score=round(hit.score, 6),
                matched=sorted(hit.matched),
                snippets=[
                    SearchSnippetRead(
                        text=s.text, page_number=s.page_number, sheet_name=s.sheet_name
                    )
                    for s in hit.snippets
                ],
            )
            for hit, document in results
        ],
    )
