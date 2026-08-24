from __future__ import annotations

from app.retrieval.analyzer import SimpleAnalyzer


def test_ascii_lowercased() -> None:
    a = SimpleAnalyzer()
    assert a.analyze("Hello WORLD") == ["hello", "world"]


def test_preserve_numbers() -> None:
    a = SimpleAnalyzer()
    tokens = a.analyze("model has 3 layers and 128 dimensions")
    assert "3" in tokens
    assert "128" in tokens


def test_preserve_hyphenated() -> None:
    a = SimpleAnalyzer()
    tokens = a.analyze("fine-tuning state-of-the-art")
    assert "fine-tuning" in tokens
    assert "state-of-the-art" in tokens


def test_domain_terms_preserved() -> None:
    a = SimpleAnalyzer()
    tokens = a.analyze("BGE-M3 and DDPM and FID")
    assert "bge-m3" in tokens
    assert "ddpm" in tokens
    assert "fid" in tokens


def test_chinese_split() -> None:
    a = SimpleAnalyzer()
    tokens = a.analyze("深度学习模型")
    # jieba available or char-level fallback — at least some tokens
    assert len(tokens) > 0
    assert all(t.strip() for t in tokens)


def test_mixed_cn_en() -> None:
    a = SimpleAnalyzer()
    tokens = a.analyze("CLIP model for 图像检索")
    assert "clip" in tokens
    # Chinese part should produce tokens
    assert any(len(t) > 1 or ord(t[0]) > 0x4E00 for t in tokens)


def test_empty_text() -> None:
    a = SimpleAnalyzer()
    assert a.analyze("") == []


def test_no_number_filtering() -> None:
    a = SimpleAnalyzer()
    tokens = a.analyze("score 0.95 with p-value 0.01")
    assert "0" in tokens or "0.95" in tokens or "95" in tokens
