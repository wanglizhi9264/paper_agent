from __future__ import annotations

import hashlib

import numpy as np

from app.embedding.base import (
    EmbeddingError,
    EmbeddingProvider,
    EmbeddingResult,
    ModelManifest,
    l2_normalize,
)


class FakeEmbeddingAdapter:
    """Deterministic fake embedding for CI and unit tests (spec §6).

    Produces a fixed-dimensional float32 vector from each text using a
    deterministic hash. No model download, no GPU. Two identical texts always
    produce identical vectors; semantically similar texts (sharing tokens)
    produce higher cosine similarity than dissimilar ones.
    """

    def __init__(self, dimension: int = 64) -> None:
        self.manifest = ModelManifest(
            model_id="fake-embedding-v1",
            revision="test",
            dimension=dimension,
            normalize=True,
            query_prefix="",
            passage_prefix="",
            pooling="none",
        )

    def _hash_to_vector(self, text: str) -> np.ndarray:
        dim = self.manifest.dimension
        vec = np.zeros(dim, dtype=np.float32)
        tokens = text.lower().split()
        for tok in tokens:
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            vec[h % dim] += 1.0
        return vec

    def embed_texts(self, texts: list[str], *, is_query: bool = False) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(
                vectors=np.empty((0, self.manifest.dimension), dtype=np.float32),
                dimension=self.manifest.dimension,
            )
        raw = np.stack([self._hash_to_vector(t) for t in texts])
        if self.manifest.normalize:
            raw = l2_normalize(raw)
        return EmbeddingResult(vectors=raw, dimension=self.manifest.dimension)

    def embed_query(self, query: str) -> EmbeddingResult:
        return self.embed_texts([query], is_query=True)


def assert_is_embedding_provider(obj: object) -> None:
    if not isinstance(obj, EmbeddingProvider):
        raise EmbeddingError(
            f"{type(obj).__name__} does not implement EmbeddingProvider",
            code="INVALID_ADAPTER",
        )
