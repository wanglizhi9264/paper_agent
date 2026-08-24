from __future__ import annotations

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.embedding.base import EmbeddingError, EmbeddingProvider
from app.embedding.fake import FakeEmbeddingAdapter

logger = get_logger(__name__)

_registry: dict[str, EmbeddingProvider] = {}


def get_embedding_provider(settings: Settings | None = None) -> EmbeddingProvider:
    """Return a cached embedding provider based on settings.

    In test/CI environments (``embedding_model`` == ``fake``), returns the
    deterministic FakeEmbeddingAdapter. In production, lazily constructs the
    real E5 adapter (requires sentence-transformers + model download).
    """
    s = settings or get_settings()
    key = s.embedding_model
    if key in _registry:
        return _registry[key]
    if key == "fake" or s.env == "test":
        provider: EmbeddingProvider = FakeEmbeddingAdapter(dimension=64)
    else:
        provider = _build_e5_adapter(s)
    _registry[key] = provider
    return provider


def _build_e5_adapter(s: Settings) -> EmbeddingProvider:
    try:
        from app.embedding.e5 import E5Adapter
    except ImportError as exc:
        raise EmbeddingError(
            f"sentence-transformers not available: {exc}",
            code="EMBEDDING_UNAVAILABLE",
        ) from exc
    return E5Adapter.from_settings(s)


def reset_registry() -> None:
    _registry.clear()


def register_provider(name: str, provider: EmbeddingProvider) -> None:
    _registry[name] = provider
