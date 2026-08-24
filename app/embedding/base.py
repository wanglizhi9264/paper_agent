from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import numpy as np


@dataclass(frozen=True)
class ModelManifest:
    """Immutable description of an embedding model (spec §13.1, §9.5).

    Any change to these fields produces a different ``signature``, which
    forces a new DocumentVersion + IndexSnapshot for all documents.
    """

    model_id: str
    revision: str
    dimension: int
    normalize: bool = True
    query_prefix: str = ""
    passage_prefix: str = ""
    pooling: str = "mean"

    @property
    def signature(self) -> str:
        raw = "|".join(
            [
                self.model_id,
                self.revision,
                str(self.dimension),
                str(self.normalize).lower(),
                self.query_prefix,
                self.passage_prefix,
                self.pooling,
            ]
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "revision": self.revision,
            "dimension": self.dimension,
            "normalize": self.normalize,
            "query_prefix": self.query_prefix,
            "passage_prefix": self.passage_prefix,
            "pooling": self.pooling,
            "signature": self.signature,
        }


@dataclass
class EmbeddingResult:
    """Output of a single embed_texts call."""

    vectors: np.ndarray  # shape (n, dim), dtype float32
    dimension: int


class EmbeddingError(Exception):
    """Recoverable embedding failure carrying a stable code."""

    code = "EMBEDDING_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class ZeroVectorError(EmbeddingError):
    """A zero-norm vector was produced and cannot be normalized (spec §13.1)."""

    code = "ZERO_VECTOR"


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Adapter protocol for embedding models (spec §13.1).

    Input: ``list[str]``. Output: 2-D float32 numpy array + dimension.
    The adapter is responsible for adding query/passage prefixes.
    """

    manifest: ModelManifest

    def embed_texts(self, texts: list[str], *, is_query: bool = False) -> EmbeddingResult:
        """Embed *texts*. When ``is_query`` is True, use the query prefix."""
        ...

    def embed_query(self, query: str) -> EmbeddingResult:
        """Convenience: embed a single query string."""
        ...


def l2_normalize(vectors: np.ndarray) -> np.ndarray:
    """L2-normalize rows. Raises ``ZeroVectorError`` if any row has zero norm.

    (spec §13.1: all vectors must be L2 normalized before FAISS; zero vectors
    must be rejected.)
    """
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    bad = np.where(norms == 0.0)[0]
    if len(bad) > 0:
        raise ZeroVectorError(f"zero-norm vector at index {bad[0]}")
    return (vectors / norms).astype(np.float32, copy=False)  # type: ignore[no-any-return]
