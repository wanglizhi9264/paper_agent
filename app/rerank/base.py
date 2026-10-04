from __future__ import annotations

from threading import RLock
from typing import Any, Protocol, runtime_checkable

import numpy as np

from app.core.config import Settings

_model_lock = RLock()
_cached_key: tuple[str, str, str, str, int, int] | None = None
_cached_reranker: BGEReranker | None = None


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
    def from_settings(cls, settings: Settings) -> BGEReranker:
        import torch
        from sentence_transformers import CrossEncoder

        model = CrossEncoder(
            settings.rerank_model,
            device=settings.rerank_device,
            revision=settings.rerank_revision or None,
            max_length=settings.rerank_max_tokens,
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
        with _model_lock:
            try:
                scores = self._model.predict(pairs, batch_size=self._batch_size)
            except RuntimeError as exc:
                import torch

                if not isinstance(exc, torch.cuda.OutOfMemoryError) or self._batch_size == 1:
                    raise RerankError("Reranker inference failed") from exc
                # One smaller-batch retry only; never retry a non-OOM error.
                torch.cuda.empty_cache()
                try:
                    scores = self._model.predict(pairs, batch_size=max(1, self._batch_size // 2))
                except RuntimeError as retry_exc:
                    raise RerankError("Reranker OOM retry failed", code="RERANK_OOM") from retry_exc
        return [float(s) for s in np.asarray(scores).flatten()]


def get_reranker(settings: Settings | None = None) -> Reranker:
    """Cache one configuration, keyed by every model/inference setting."""
    global _cached_key, _cached_reranker
    if settings is None:
        from app.core.config import get_settings

        settings = get_settings()
    if settings.env == "test" or settings.rerank_model == "fake":
        return FakeReranker()
    key = (
        settings.rerank_model,
        settings.rerank_revision,
        settings.rerank_device,
        settings.rerank_dtype,
        settings.rerank_max_tokens,
        settings.rerank_batch_size,
    )
    with _model_lock:
        if _cached_key == key and _cached_reranker is not None:
            return _cached_reranker
        # Do not retain an unbounded registry of GPU models after config changes.
        _cached_key, _cached_reranker = None, None
        try:
            model = BGEReranker.from_settings(settings)
        except (ImportError, OSError, RuntimeError) as exc:
            raise RerankError("Reranker model unavailable", code="RERANK_UNAVAILABLE") from exc
        _cached_key, _cached_reranker = key, model
        return model


def reset_reranker_cache() -> None:
    global _cached_key, _cached_reranker
    with _model_lock:
        _cached_key, _cached_reranker = None, None
