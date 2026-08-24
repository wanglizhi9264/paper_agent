from __future__ import annotations

from pathlib import Path

import faiss
import numpy as np

from app.embedding.base import l2_normalize


class FaissIndexError(Exception):
    code = "FAISS_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class FaissIndex:
    """Wrapper around FAISS IndexIDMap2(IndexFlatIP(dim)) (spec §13.1).

    * Vectors are L2-normalized before insertion (caller may pre-normalize).
    * IDs are non-negative int64 assigned by the DB sequence.
    * save: write to temp path, fsync, atomic rename.
    * load: validate dimension, ntotal, then serve.
    """

    def __init__(self, index: faiss.IndexIDMap2, dimension: int) -> None:
        self._index = index
        self.dimension = dimension

    @classmethod
    def create(cls, dimension: int) -> FaissIndex:
        base = faiss.IndexFlatIP(dimension)
        index = faiss.IndexIDMap2(base)
        return cls(index=index, dimension=dimension)

    @property
    def ntotal(self) -> int:
        return self._index.ntotal

    def add(self, vectors: np.ndarray, ids: np.ndarray) -> None:
        """Add pre-normalized vectors with given int64 IDs.

        Caller must ensure vectors are already L2-normalized and ids are
        non-negative int64.
        """
        if vectors.ndim != 2:
            raise FaissIndexError(f"expected 2-D array, got {vectors.ndim}-D")
        if vectors.shape[1] != self.dimension:
            raise FaissIndexError(
                f"dimension mismatch: index={self.dimension}, got={vectors.shape[1]}",
                code="DIMENSION_MISMATCH",
            )
        if vectors.dtype != np.float32:
            vectors = vectors.astype(np.float32)
        if ids.dtype != np.int64:
            ids = ids.astype(np.int64)
        self._index.add_with_ids(vectors, ids)

    def add_texts(
        self,
        vectors: np.ndarray,
        ids: np.ndarray,
        *,
        normalize: bool = True,
    ) -> None:
        """Add vectors with automatic L2 normalization and zero-vector check."""
        if normalize:
            vectors = l2_normalize(vectors.astype(np.float32))
        self.add(vectors, ids)

    def search(self, query: np.ndarray, top_k: int = 10) -> tuple[np.ndarray, np.ndarray]:
        """Search for top_k nearest passages. Returns (scores, ids).

        Query must be a single L2-normalized float32 vector of shape (dim,).
        """
        if query.ndim == 1:
            query = query.reshape(1, -1)
        if query.shape[1] != self.dimension:
            raise FaissIndexError(
                f"query dimension mismatch: index={self.dimension}, got={query.shape[1]}",
                code="DIMENSION_MISMATCH",
            )
        if query.dtype != np.float32:
            query = query.astype(np.float32)
        scores, ids = self._index.search(query, top_k)
        return scores[0], ids[0]

    def search_batch(
        self, queries: np.ndarray, top_k: int = 10
    ) -> list[tuple[np.ndarray, np.ndarray]]:
        if queries.ndim == 2:
            if queries.dtype != np.float32:
                queries = queries.astype(np.float32)
            scores, ids = self._index.search(queries, top_k)
            return [(scores[i], ids[i]) for i in range(queries.shape[0])]
        return [self.search(queries, top_k)]

    def save(self, path: Path) -> None:
        """Write FAISS index to *path* via temp file + atomic rename (spec §13.1)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        faiss.write_index(self._index, str(tmp))
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path, expected_dimension: int | None = None) -> FaissIndex:
        index = faiss.read_index(str(path))
        if not isinstance(index, faiss.IndexIDMap2):
            raise FaissIndexError(
                f"expected IndexIDMap2, got {type(index).__name__}",
                code="INVALID_INDEX_TYPE",
            )
        inner = index.index
        dim = inner.d
        if expected_dimension is not None and dim != expected_dimension:
            raise FaissIndexError(
                f"dimension mismatch: expected={expected_dimension}, got={dim}",
                code="DIMENSION_MISMATCH",
            )
        return cls(index=index, dimension=dim)

    def contains(self, faiss_id: int) -> bool:
        """Check if a faiss_id is present in the index."""
        id_list = faiss.vector_to_array(self._index.id_map)
        return int(faiss_id) in id_list.tolist()
