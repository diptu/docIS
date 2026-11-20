"""The real local embedding model (bge-small-en-v1.5 via fastembed).

Opt-in: the model is ~64 MB and downloaded on first use, so these run only
with RUN_EMBEDDING_TESTS=1 (and are marked ``embeddings``). Everything else
uses the deterministic FakeEmbedder.
"""

import math
import os

import pytest

from app.search.embeddings import FastEmbedEmbedder

pytestmark = [
    pytest.mark.embeddings,
    pytest.mark.skipif(
        os.environ.get("RUN_EMBEDDING_TESTS") != "1",
        reason="set RUN_EMBEDDING_TESTS=1 to load the real embedding model",
    ),
]


@pytest.fixture(scope="module")
def embedder() -> FastEmbedEmbedder:
    return FastEmbedEmbedder(os.environ.get("EMBEDDING_CACHE_DIR") or None)


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_vectors_are_384_dimensional_and_unit_length(
    embedder: FastEmbedEmbedder,
) -> None:
    [passage] = embedder.embed_passages(["Invoice for consulting services"])
    query = embedder.embed_query("consulting invoice")

    for vector in (passage, query):
        assert len(vector) == 384
        assert math.isclose(math.sqrt(sum(x * x for x in vector)), 1.0, abs_tol=1e-3)


def test_meaning_outranks_unrelated_text(embedder: FastEmbedEmbedder) -> None:
    """A paraphrase scores higher than an unrelated passage."""
    advisory, laptops = embedder.embed_passages(
        [
            "Advisory services rendered in October, payment outstanding",
            "Delivery of twelve laptops and four docking stations",
        ]
    )
    query = embedder.embed_query("unpaid consulting work")

    assert _cosine(query, advisory) > _cosine(query, laptops)


def test_no_passages_need_no_model_call(embedder: FastEmbedEmbedder) -> None:
    assert embedder.embed_passages([]) == []
