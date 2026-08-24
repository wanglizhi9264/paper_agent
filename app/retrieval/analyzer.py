from __future__ import annotations

import re
from typing import Protocol

# User dictionary for domain terms that should be preserved as single tokens.
DOMAIN_TERMS: set[str] = {
    "bge-m3",
    "ddpm",
    "ddim",
    "fid",
    "r-precision",
    "infonce",
    "clip",
    "eeg",
}

# Pre-compiled patterns.
_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
_TOKEN_PATTERN = re.compile(r"[a-z0-9]+(?:[-/][a-z0-9]+)*")


class Analyzer(Protocol):
    def analyze(self, text: str) -> list[str]: ...


class SimpleAnalyzer:
    """Bilingual analyzer (spec §13.2).

    - Lowercase English.
    - Preserve numbers and hyphenated terms (BGE-M3, R-Precision).
    - Split Chinese into words using a simple approach (jieba when available).
    - Domain terms in the user dictionary are kept as single tokens.
    - Never filter numbers unconditionally.
    """

    def __init__(self, domain_terms: set[str] | None = None) -> None:
        self._domain_terms = {t.lower() for t in (domain_terms or DOMAIN_TERMS)}
        self._jieba = None
        try:
            import jieba

            jieba.setLogLevel("ERROR")
            for term in self._domain_terms:
                jieba.add_word(term)
            self._jieba = jieba
        except ImportError:
            pass

    def analyze(self, text: str) -> list[str]:
        text = text.lower()
        tokens: list[str] = []
        # Split into CJK and non-CJK segments.
        segments = re.findall(r"[\u4e00-\u9fff\u3400-\u4dbf]+|[a-z0-9][-/a-z0-9\s]*", text)
        for raw_seg in segments:
            seg = raw_seg.strip()
            if not seg:
                continue
            if _CJK_PATTERN.search(seg):
                tokens.extend(self._analyze_cjk(seg))
            else:
                tokens.extend(self._analyze_ascii(seg))
        return self._merge_domain_terms(tokens)

    def _analyze_ascii(self, text: str) -> list[str]:
        return _TOKEN_PATTERN.findall(text)

    def _analyze_cjk(self, text: str) -> list[str]:
        if self._jieba is not None:
            return [t for t in self._jieba.lcut(text) if t.strip()]
        # Fallback: character-level for CJK when jieba is unavailable.
        return [ch for ch in text if not ch.isspace()]

    def _merge_domain_terms(self, tokens: list[str]) -> list[str]:
        """Re-merge tokens that form a known domain term."""
        result: list[str] = []
        i = 0
        while i < len(tokens):
            matched = False
            for length in (3, 2):
                if i + length <= len(tokens):
                    candidate = "".join(tokens[i : i + length])
                    if candidate in self._domain_terms:
                        result.append(candidate)
                        i += length
                        matched = True
                        break
            if not matched:
                result.append(tokens[i])
                i += 1
        return result


def default_analyzer() -> Analyzer:
    return SimpleAnalyzer()
