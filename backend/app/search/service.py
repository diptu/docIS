"""
Searching documents by keywords, by meaning, or both.

On PostgreSQL keyword search uses the full-text index (stemming, ranking,
highlighted snippets) and semantic search the pgvector HNSW index. Other
databases (SQLite in tests) use simple scans with the same result shape.
"""

import math
import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from anyio import to_thread
from sqlalchemy import ColumnElement, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.document_chunk import TEXT_SEARCH_CONFIG, DocumentChunk
from app.search.embeddings import Embedder

# Snippet highlight markers. Control characters cannot clash with document
# text; clients turn them into highlighting without parsing HTML.
MARK_START, MARK_END = "\x02", "\x03"
CANDIDATES = 200  # chunks considered per search mode
SNIPPETS_PER_DOCUMENT = 3
SNIPPET_CHARS = 300
RRF_K = 60  # reciprocal rank fusion constant (standard value)


class SearchMode(StrEnum):
    KEYWORD = "keyword"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"


@dataclass
class Snippet:
    text: str
    page_number: int | None
    sheet_name: str | None


@dataclass
class Hit:
    document_id: uuid.UUID
    score: float
    matched: set[str] = field(default_factory=set)
    snippets: list[Snippet] = field(default_factory=list)


class SemanticSearchUnavailableError(Exception):
    """Semantic search was requested but is disabled."""


class SearchService:
    def __init__(
        self,
        session: AsyncSession,
        embedder: Embedder | None,
        min_semantic_score: float = 0.5,
    ) -> None:
        self.session = session
        self.embedder = embedder
        self.min_semantic_score = min_semantic_score

    @property
    def _postgres(self) -> bool:
        return self.session.get_bind().dialect.name == "postgresql"

    async def search(
        self, query: str, mode: SearchMode, limit: int
    ) -> list[tuple[Hit, Document]]:
        """Documents matching ``query``, best first, with their snippets."""
        if mode != SearchMode.KEYWORD and self.embedder is None:
            raise SemanticSearchUnavailableError("Semantic search is disabled.")
        if mode == SearchMode.KEYWORD:
            hits = await self._keyword(query)
        elif mode == SearchMode.SEMANTIC:
            hits = await self._semantic(query)
        else:
            hits = _fuse(await self._keyword(query), await self._semantic(query))
        hits = hits[:limit]
        documents = {
            document.id: document
            for document in await self.session.scalars(
                select(Document).where(
                    Document.id.in_([hit.document_id for hit in hits])
                )
            )
        }
        return [
            (hit, documents[hit.document_id])
            for hit in hits
            if hit.document_id in documents
        ]

    # ------------------------------------------------------------- keyword

    async def _keyword(self, query: str) -> list[Hit]:
        hits = await (
            self._keyword_postgres(query)
            if self._postgres
            else self._keyword_scan(query)
        )
        # File names count too: a search for "acme" should find "acme-q3.pdf".
        terms = _terms(query)
        if terms:
            names = await self.session.scalars(
                select(Document.id).where(
                    *[func.lower(Document.file_name).contains(term) for term in terms]
                )
            )
            by_id = {hit.document_id: hit for hit in hits}
            for document_id in names:
                hit = by_id.setdefault(document_id, Hit(document_id, score=0.0))
                hit.score += 1.0  # a file-name match ranks above body-only matches
                hit.matched.add("file_name")
            hits = list(by_id.values())
        # File-name matches first, then by text relevance.
        return sorted(
            hits, key=lambda hit: ("file_name" not in hit.matched, -hit.score)
        )

    async def _keyword_postgres(self, query: str) -> list[Hit]:
        config: ColumnElement[str] = literal_column(f"'{TEXT_SEARCH_CONFIG}'")
        tsquery = func.websearch_to_tsquery(config, query)
        vector = func.to_tsvector(config, DocumentChunk.content)
        rank = func.ts_rank_cd(vector, tsquery)
        headline = func.ts_headline(
            config,
            DocumentChunk.content,
            tsquery,
            f"StartSel={MARK_START},StopSel={MARK_END},MaxWords=35,MinWords=12,"
            "MaxFragments=2,FragmentDelimiter= … ",
        )
        rows = await self.session.execute(
            select(
                DocumentChunk.document_id,
                DocumentChunk.page_number,
                DocumentChunk.sheet_name,
                rank.label("rank"),
                headline.label("snippet"),
            )
            .where(vector.op("@@")(tsquery))
            .order_by(rank.desc())
            .limit(CANDIDATES)
        )
        return _group(
            (
                row.document_id,
                float(row.rank),
                row.snippet,
                row.page_number,
                row.sheet_name,
            )
            for row in rows.all()
        )

    async def _keyword_scan(self, query: str) -> list[Hit]:
        """Portable fallback: every term must occur (case-insensitive)."""
        terms = _terms(query)
        if not terms:
            return []
        rows = await self.session.execute(
            select(DocumentChunk).where(
                *[func.lower(DocumentChunk.content).contains(term) for term in terms]
            )
        )
        found = []
        for chunk in rows.scalars():
            lowered = chunk.content.lower()
            score = sum(lowered.count(term) for term in terms)
            found.append(
                (
                    chunk.document_id,
                    float(score),
                    _highlight(chunk.content, terms),
                    chunk.page_number,
                    chunk.sheet_name,
                )
            )
        return _group(sorted(found, key=lambda row: -row[1]))

    # ------------------------------------------------------------ semantic

    async def _semantic(self, query: str) -> list[Hit]:
        assert self.embedder is not None
        query_vector = await to_thread.run_sync(self.embedder.embed_query, query)
        if self._postgres:
            distance = DocumentChunk.embedding.cosine_distance(query_vector)
            rows = await self.session.execute(
                select(
                    DocumentChunk.document_id,
                    DocumentChunk.page_number,
                    DocumentChunk.sheet_name,
                    DocumentChunk.content,
                    (1 - distance).label("score"),
                )
                .where(DocumentChunk.embedding.is_not(None))
                .order_by(distance)
                .limit(CANDIDATES)
            )
            scored = [
                (r.document_id, float(r.score), r.content, r.page_number, r.sheet_name)
                for r in rows.all()
            ]
        else:
            chunks = await self.session.scalars(
                select(DocumentChunk).where(DocumentChunk.embedding.is_not(None))
            )
            scored = sorted(
                (
                    (
                        c.document_id,
                        _cosine(query_vector, c.embedding or []),
                        c.content,
                        c.page_number,
                        c.sheet_name,
                    )
                    for c in chunks
                ),
                key=lambda row: -row[1],
            )[:CANDIDATES]
        relevant = [
            (doc, score, _excerpt(content), page, sheet)
            for doc, score, content, page, sheet in scored
            if score >= self.min_semantic_score
        ]
        hits = _group(relevant)
        for hit in hits:
            hit.matched = {"semantic"}
        return hits


# ----------------------------------------------------------------- helpers


def _terms(query: str) -> list[str]:
    return [t for t in re.split(r"\W+", query.lower()) if t]


ChunkRow = tuple[uuid.UUID, float, str, int | None, str | None]


def _group(rows: Iterable[ChunkRow]) -> list[Hit]:
    """Per-document hits from chunk rows already sorted best-first; a
    document scores as its best chunk."""
    hits: dict[uuid.UUID, Hit] = {}
    for document_id, score, snippet, page, sheet in rows:
        hit = hits.get(document_id)
        if hit is None:
            hit = hits[document_id] = Hit(document_id, score=score, matched={"keyword"})
        if len(hit.snippets) < SNIPPETS_PER_DOCUMENT:
            hit.snippets.append(Snippet(snippet, page, sheet))
    return list(hits.values())


def _fuse(keyword: list[Hit], semantic: list[Hit]) -> list[Hit]:
    """Reciprocal rank fusion: robust to the two scores' different scales."""
    fused: dict[uuid.UUID, Hit] = {}
    for ranking in (keyword, semantic):
        for rank, hit in enumerate(ranking, start=1):
            merged = fused.setdefault(hit.document_id, Hit(hit.document_id, score=0.0))
            merged.score += 1 / (RRF_K + rank)
            merged.matched |= hit.matched
            for snippet in hit.snippets:
                if len(merged.snippets) < SNIPPETS_PER_DOCUMENT:
                    merged.snippets.append(snippet)
    return sorted(fused.values(), key=lambda hit: -hit.score)


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def _excerpt(text: str) -> str:
    text = " ".join(text.split())
    return (
        text if len(text) <= SNIPPET_CHARS else text[: SNIPPET_CHARS - 1].rstrip() + "…"
    )


def _highlight(text: str, terms: Sequence[str]) -> str:
    """An excerpt around the first match with every term marked."""
    flat = " ".join(text.split())
    lowered = flat.lower()
    first = min((i for t in terms if (i := lowered.find(t)) >= 0), default=0)
    start = max(0, first - 80)
    excerpt = flat[start : start + SNIPPET_CHARS]
    pattern = re.compile("|".join(re.escape(t) for t in terms), re.IGNORECASE)
    marked = pattern.sub(lambda m: f"{MARK_START}{m.group(0)}{MARK_END}", excerpt)
    return ("…" if start else "") + marked
