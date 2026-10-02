from __future__ import annotations

import pytest

from app.rerank.base import FakeReranker


def test_fake_reranker_basic() -> None:
    r = FakeReranker()
    query = "deep learning model"
    passages = [
        "deep learning architecture",
        "natural language processing",
        "machine learning model",
    ]
    scores = r.rerank(query, passages)
    assert len(scores) == 3
    # First passage has highest overlap
    assert scores[0] >= scores[1]
    assert scores[2] >= scores[1]


def test_fake_reranker_perfect_match() -> None:
    r = FakeReranker()
    scores = r.rerank("hello world", ["hello world"])
    assert scores[0] == 1.0


def test_fake_reranker_no_overlap() -> None:
    r = FakeReranker()
    scores = r.rerank("alpha beta", ["gamma delta"])
    assert scores[0] == 0.0


def test_fake_reranker_empty_passages() -> None:
    r = FakeReranker()
    scores = r.rerank("test", [])
    assert scores == []


def test_fake_reranker_deterministic() -> None:
    r = FakeReranker()
    s1 = r.rerank("deep learning", ["learning deep", "machine learning"])
    s2 = r.rerank("deep learning", ["learning deep", "machine learning"])
    assert s1 == s2


def test_fake_reranker_empty_query() -> None:
    r = FakeReranker()
    scores = r.rerank("", ["some text"])
    assert scores[0] == 0.0


def test_cache_uses_revision_device_dtype_batch_and_token_limit(monkeypatch) -> None:
    from app.core.config import get_settings
    from app.rerank.base import BGEReranker, get_reranker, reset_reranker_cache

    reset_reranker_cache()
    built = []

    def build(_cls, settings):
        model = BGEReranker(object())
        built.append(model)
        return model

    monkeypatch.setattr(BGEReranker, "from_settings", classmethod(build))
    settings = get_settings().model_copy(update={"env": "production"})
    try:
        first = get_reranker(settings)
        assert get_reranker(settings) is first
        for field, value in {
            "rerank_revision": "new",
            "rerank_device": "cpu",
            "rerank_dtype": "float32",
            "rerank_batch_size": 2,
            "rerank_max_tokens": 256,
            "rerank_model": "other",
        }.items():
            settings = settings.model_copy(update={field: value})
            next_model = get_reranker(settings)
            assert next_model is not first
            assert get_reranker(settings) is next_model
            first = next_model
        assert len(built) == 7
    finally:
        reset_reranker_cache()


def test_model_load_failure_is_not_cached(monkeypatch) -> None:
    from app.core.config import get_settings
    from app.rerank.base import BGEReranker, RerankError, get_reranker, reset_reranker_cache

    reset_reranker_cache()
    calls = []

    def fail(_cls, _settings):
        calls.append(1)
        raise OSError("missing local weights")

    monkeypatch.setattr(BGEReranker, "from_settings", classmethod(fail))
    settings = get_settings().model_copy(update={"env": "production"})
    for _ in range(2):
        with pytest.raises(RerankError, match="unavailable"):
            get_reranker(settings)
    assert len(calls) == 2


@pytest.mark.parametrize("always_fail", [False, True])
def test_oom_retries_once_with_smaller_batch(monkeypatch, always_fail) -> None:
    import sys
    from types import SimpleNamespace

    from app.rerank.base import BGEReranker, RerankError

    class OOM(RuntimeError):
        pass

    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            cuda=SimpleNamespace(OutOfMemoryError=OOM, empty_cache=lambda: None),
        ),
    )
    batches = []

    class Model:
        def predict(self, pairs, batch_size):
            batches.append(batch_size)
            if len(batches) == 1 or always_fail:
                raise OOM("oom")
            return [0.5] * len(pairs)

    reranker = BGEReranker(Model(), batch_size=4)
    if always_fail:
        with pytest.raises(RerankError):
            reranker.rerank("q", ["p"])
    else:
        assert reranker.rerank("q", ["p"]) == [0.5]
    assert batches == [4, 2]
