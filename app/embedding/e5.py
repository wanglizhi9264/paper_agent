from __future__ import annotations

from typing import Any

import numpy as np

from app.core.config import Settings
from app.embedding.base import (
    EmbeddingError,
    EmbeddingResult,
    ModelManifest,
    l2_normalize,
)


class E5Adapter:
    """Real E5 embedding adapter using sentence-transformers (spec §13.1).

    Adds ``query: `` / ``passage: `` prefixes, mean-pools, and L2-normalizes.
    Requires model download — only used in ``model_smoke`` tests or production.
    """

    def __init__(
        self,
        model: Any,
        manifest: ModelManifest,
        batch_size: int = 16,
        device: str = "cpu",
    ) -> None:
        self._model = model
        self.manifest = manifest
        self._batch_size = batch_size
        self._device = device

    @classmethod
    def from_settings(cls, s: Settings) -> E5Adapter:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingError(
                f"sentence-transformers not installed: {exc}",
                code="EMBEDDING_UNAVAILABLE",
            ) from exc
        model = SentenceTransformer(
            s.embedding_model,
            device=s.embedding_device,
        )
        dim = model.get_sentence_embedding_dimension()
        if dim is None or dim <= 0:
            raise EmbeddingError(
                "embedding model did not report a positive dimension",
                code="EMBEDDING_UNAVAILABLE",
            )
        manifest = ModelManifest(
            model_id=s.embedding_model,
            revision=s.embedding_revision or "unknown",
            dimension=dim,
            normalize=True,
            query_prefix="query: ",
            passage_prefix="passage: ",
            pooling="mean",
        )
        return cls(
            model=model,
            manifest=manifest,
            batch_size=s.embedding_batch_size,
            device=s.embedding_device,
        )

    def _prefixed(self, texts: list[str], is_query: bool) -> list[str]:
        prefix = self.manifest.query_prefix if is_query else self.manifest.passage_prefix
        if not prefix:
            return texts
        return [f"{prefix}{t}" for t in texts]

    def embed_texts(self, texts: list[str], *, is_query: bool = False) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(
                vectors=np.empty((0, self.manifest.dimension), dtype=np.float32),
                dimension=self.manifest.dimension,
            )
        prefixed = self._prefixed(texts, is_query)
        raw = self._model.encode(
            prefixed,
            batch_size=self._batch_size,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        raw = raw.astype(np.float32, copy=False)
        if self.manifest.normalize:
            raw = l2_normalize(raw)
        return EmbeddingResult(vectors=raw, dimension=self.manifest.dimension)

    def embed_query(self, query: str) -> EmbeddingResult:
        return self.embed_texts([query], is_query=True)
