from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import numpy as np


class RerankError(Exception):
    code = "RERANK_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


@runtime_checkable
class Reranker(Protocol):
    """Cross-encoder reranker protocol (spec §14.3).

    Input: query + list of (doc_id, text). Output: scores aligned with input.
    """

    def rerank(self, query: str, passages: list[str]) -> list[float]: ...


class FakeReranker:
    """Deterministic fake reranker for CI (spec §6).

    Scores based on token overlap between query and passage — no model
    download, deterministic, reproducible.
    """

    def rerank(self, query: str, passages: list[str]) -> list[float]:
        q_tokens = set(query.lower().split())
        scores: list[float] = []
        for p in passages:
            p_tokens = set(p.lower().split())
            if not q_tokens or not p_tokens:
                scores.append(0.0)
                continue
            overlap = len(q_tokens & p_tokens)
            score = overlap / len(q_tokens)
            scores.append(float(score))
        return scores


class BGEReranker:
    """Real BGE reranker using sentence-transformers (spec §14.3).

    Requires model download — only used in model_smoke tests or production.
    """

    def __init__(self, model: Any, batch_size: int = 4, device: str = "cpu") -> None:
        self._model = model
        self._batch_size = batch_size
        self._device = device

    @classmethod
    def from_settings(cls, settings: Any) -> BGEReranker:
        import torch
        from sentence_transformers import CrossEncoder

        model = CrossEncoder(
            settings.rerank_model,
            device=settings.rerank_device,
            revision=settings.rerank_revision or None,
            automodel_args={
                "dtype": torch.float16 if settings.rerank_dtype == "float16" else torch.float32
            },
        )
        return cls(
            model=model,
            batch_size=settings.rerank_batch_size,
            device=settings.rerank_device,
        )

    def rerank(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        pairs = [(query, p) for p in passages]
        scores = self._model.predict(pairs, batch_size=self._batch_size)
        return [float(s) for s in np.asarray(scores).flatten()]


def get_reranker(settings: Any | None = None) -> Reranker:
    """Return a cached reranker. Fake in test env, BGE in production."""
    if settings is None:
        from app.core.config import get_settings

        settings = get_settings()
    if settings.env == "test" or settings.rerank_model == "fake":
        return FakeReranker()
    return BGEReranker.from_settings(settings)
