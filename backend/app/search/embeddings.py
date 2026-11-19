"""
Text embeddings for semantic search (local model, no API key needed).
"""

from collections.abc import Sequence
from functools import cached_property, lru_cache
from pathlib import Path
from typing import Any, Protocol

MODEL_NAME = "BAAI/bge-small-en-v1.5"


class Embedder(Protocol):
    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class FastEmbedEmbedder:
    """bge-small-en-v1.5 via fastembed (ONNX on CPU, 384 dimensions).

    The model is loaded on first use. Passages and queries are embedded
    differently (bge adds an instruction prefix to queries). Blocking: call
    from a worker thread.
    """

    def __init__(
        self, cache_dir: Path | None = None, model_name: str = MODEL_NAME
    ) -> None:
        self.cache_dir = cache_dir
        self.model_name = model_name

    @cached_property
    def _model(self) -> Any:
        from fastembed import TextEmbedding

        cache = str(self.cache_dir) if self.cache_dir else None
        return TextEmbedding(self.model_name, cache_dir=cache)

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        return [vector.tolist() for vector in self._model.passage_embed(list(texts))]

    def embed_query(self, text: str) -> list[float]:
        [vector] = list(self._model.query_embed([text]))
        return list(vector.tolist())


@lru_cache
def get_embedder(cache_dir: Path | None = None) -> FastEmbedEmbedder:
    """One embedder (and loaded model) per process."""
    return FastEmbedEmbedder(cache_dir)
