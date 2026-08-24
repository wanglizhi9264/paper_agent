"""Deterministic sentence splitter for Chinese + English text (spec §12.2).

Rules:
1. Split on Chinese sentence-end punctuation (。！？) and English (.!?).
2. Newlines are also sentence boundaries.
3. Keep the trailing punctuation with the sentence.
4. Empty results are discarded.
5. Single sentences exceeding ``max_chunk_chars`` are further split by
   semicolons, commas, then whitespace; if still too long, hard-cut and mark
   ``hard_split=True`` (spec §12.2 rule 3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Sentence-end punctuation: Chinese (full-width) + English (half-width).
_SENT_END = "。！？.!?。！？"
# Trailing closers that stay with the sentence: quotes, brackets, spaces.
_TRAILING = "）)」』\"'】\u3000 "
_SPLIT_RE = re.compile(rf"([{_SENT_END}][{_TRAILING}]*)")

# Secondary split delimiters for over-long sentences (spec §12.2 rule 3).
_SEMICOLON_RE = re.compile(r"[；;]")
_COMMA_RE = re.compile(r"[，,]")
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class Sentence:
    text: str
    hard_split: bool = False


def split_sentences(text: str, *, max_chunk_chars: int = 800) -> list[Sentence]:
    """Deterministically split ``text`` into sentences.

    Returns a list of ``Sentence`` objects. Each sentence's text is stripped
    but may contain internal newlines if the original had them (newlines act
    as boundaries, so this is rare). Over-long sentences are further split
    and marked ``hard_split=True`` when hard-cut was needed.
    """
    if not text:
        return []

    # Step 1: primary split on sentence-end punctuation.
    parts = _SPLIT_RE.split(text)
    raw_sentences: list[str] = []
    buf = ""
    for part in parts:
        if not part:
            continue
        if _SPLIT_RE.fullmatch(part):
            buf += part
            raw_sentences.append(buf)
            buf = ""
        else:
            buf += part
    if buf.strip():
        raw_sentences.append(buf)

    # Normalize: strip and collapse internal newlines to spaces for sentence text,
    # but keep the content intact for retrieval. Actually we keep newlines —
    # spec says newlines are boundaries. We split on newlines too.
    sentences: list[Sentence] = []
    for raw in raw_sentences:
        # Also split on newlines within a "sentence" (newline = boundary).
        for raw_line in raw.split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            if len(line) <= max_chunk_chars:
                sentences.append(Sentence(text=line))
            else:
                sentences.extend(_split_overlong(line, max_chunk_chars))
    return sentences


def _split_overlong(text: str, max_chars: int) -> list[Sentence]:
    """Split a sentence that exceeds ``max_chars`` (spec §12.2 rule 3).

    Priority: semicolons -> commas -> whitespace -> hard cut.
    """
    # Try semicolons.
    parts = _SEMICOLON_RE.split(text)
    if len(parts) > 1:
        return _merge_or_hardcut(parts, max_chars, sep_hint=";")

    # Try commas.
    parts = _COMMA_RE.split(text)
    if len(parts) > 1:
        return _merge_or_hardcut(parts, max_chars, sep_hint=",")

    # Try whitespace.
    parts = _WS_RE.split(text)
    if len(parts) > 1:
        return _merge_or_hardcut(parts, max_chars, sep_hint=" ")

    # Hard cut.
    return _hard_cut(text, max_chars)


def _merge_or_hardcut(parts: list[str], max_chars: int, *, sep_hint: str) -> list[Sentence]:
    """Greedily merge split parts up to ``max_chars``; hard-cut if a single part is still too long."""
    results: list[Sentence] = []
    buf = ""
    for src_part in parts:
        part = src_part.strip()
        if not part:
            continue
        if len(buf) + len(part) + (len(sep_hint) if buf else 0) <= max_chars:
            if buf:
                buf += sep_hint + part
            else:
                buf = part
        else:
            if buf:
                results.append(Sentence(text=buf))
                buf = ""
            if len(part) <= max_chars:
                buf = part
            else:
                results.extend(_hard_cut(part, max_chars))
                buf = ""
    if buf:
        results.append(Sentence(text=buf))
    return results


def _hard_cut(text: str, max_chars: int) -> list[Sentence]:
    """Hard-cut text into ``max_chars``-sized pieces, marking ``hard_split=True``."""
    results: list[Sentence] = []
    for i in range(0, len(text), max_chars):
        piece = text[i : i + max_chars]
        if piece:
            results.append(Sentence(text=piece, hard_split=True))
    return results
