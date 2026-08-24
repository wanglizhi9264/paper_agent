from __future__ import annotations

from app.chunking.sentence import split_sentences


def test_split_english_sentences() -> None:
    text = "Hello world. This is a test! Is it working? Yes."
    sents = split_sentences(text, max_chunk_chars=800)
    texts = [s.text for s in texts_strip(sents)]
    assert "Hello world." in texts
    assert "This is a test!" in texts
    assert "Is it working?" in texts


def test_split_chinese_sentences() -> None:
    text = "你好世界。这是一个测试！它在工作吗？是的。"
    sents = split_sentences(text, max_chunk_chars=800)
    texts = [s.text for s in texts_strip(sents)]
    assert "你好世界。" in texts
    assert "这是一个测试！" in texts
    assert "它在工作吗？" in texts
    assert "是的。" in texts


def test_split_newline_boundary() -> None:
    text = "First line\nSecond line"
    sents = split_sentences(text, max_chunk_chars=800)
    texts = [s.text for s in texts_strip(sents)]
    assert "First line" in texts
    assert "Second line" in texts


def test_split_empty() -> None:
    assert split_sentences("", max_chunk_chars=800) == []


def test_overlong_hard_cut() -> None:
    text = "a" * 1000  # no punctuation, no spaces
    sents = split_sentences(text, max_chunk_chars=100)
    assert len(sents) == 10
    assert all(s.hard_split for s in sents)
    assert all(len(s.text) == 100 for s in sents)


def test_overlong_split_by_semicolon() -> None:
    text = "x" * 300 + "; " + "y" * 300 + "; " + "z" * 300
    sents = split_sentences(text, max_chunk_chars=400)
    assert len(sents) >= 2
    assert all(not s.hard_split for s in sents)


def test_overlong_split_by_comma() -> None:
    text = "a" * 500 + ", " + "b" * 500
    sents = split_sentences(text, max_chunk_chars=600)
    assert len(sents) >= 2
    assert all(not s.hard_split for s in sents)


def test_overlong_split_by_whitespace() -> None:
    text = " ".join(["word" * 50] * 5)
    sents = split_sentences(text, max_chunk_chars=100)
    assert len(sents) > 1


def test_deterministic_repeated() -> None:
    text = "A. B. C. D. E. F. G. H."
    r1 = split_sentences(text, max_chunk_chars=800)
    r2 = split_sentences(text, max_chunk_chars=800)
    assert [s.text for s in r1] == [s.text for s in r2]


def texts_strip(sents):
    return sents
