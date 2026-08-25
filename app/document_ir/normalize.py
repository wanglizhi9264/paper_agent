"""Unicode normalizer v2 (spec §8.2).

``normalize_for_retrieval`` produces the retrieval-facing text:
CRLF -> LF, NUL removal, NFKC, soft-hyphen removal, ligature folding,
line-end dehyphenation (letters join, numeric ranges keep the hyphen),
unicode dash unification to ASCII ``-``, whitespace collapse.
Replacement characters U+FFFD are counted and never silently deleted.

``formula_search_aliases`` maps Greek symbols to spoken names; aliases are
appended to chunk retrieval content only and never alter raw/normalized text
(spec §8.2).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

NORMALIZER_VERSION = "unicode-v2"

REPLACEMENT_CHAR = "\ufffd"

_LIGATURES = str.maketrans(
    {
        "\ufb00": "ff",
        "\ufb01": "fi",
        "\ufb02": "fl",
        "\ufb03": "ffi",
        "\ufb04": "ffl",
    }
)

_LETTER = "A-Za-z\u00c0-\u024f\u0370-\u03ff"
_LETTER_DEHYPH = re.compile(rf"(?<=[{_LETTER}])-\n(?=[{_LETTER}])")
_DIGIT_DEHYPH = re.compile(r"(?<=\d)-\n(?=\d)")

_UNICODE_DASHES = "\u2212\u2010\u2011\u2012\u2013\u2014\u2015"
_DASH_MAP = str.maketrans({c: "-" for c in _UNICODE_DASHES})

_WHITESPACE = re.compile(r"\s+")

_GREEK_ALIASES = {"ε": "epsilon", "θ": "theta", "Σ": "sigma", "μ": "mu"}


class NormalizationError(ValueError):
    """Raised when normalization would produce empty output."""


@dataclass(frozen=True, slots=True)
class NormalizeResult:
    """Normalized text plus replacement-character statistics (spec §8.2)."""

    text: str
    replacement_char_count: int


def normalize_for_retrieval(raw: str, *, allow_empty: bool = False) -> NormalizeResult:
    """Normalize *raw* for embedding/BM25/label resolution (spec §8.2).

    ``allow_empty=True`` is reserved for figure elements whose output may be
    empty; every other element must produce non-empty normalized text.
    """
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\x00", "")
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u00ad", "")
    text = text.translate(_LIGATURES)
    text = _LETTER_DEHYPH.sub("", text)
    text = _DIGIT_DEHYPH.sub("-", text)
    text = text.translate(_DASH_MAP)
    text = _WHITESPACE.sub(" ", text).strip()

    count = text.count(REPLACEMENT_CHAR)
    if not text and not allow_empty:
        raise NormalizationError("normalization produced empty output")
    return NormalizeResult(text=text, replacement_char_count=count)


def formula_search_aliases(text: str) -> list[str]:
    """Return sorted alias tokens for Greek symbols present in *text*.

    Aliases are appended to ``retrieval_content`` only (spec §8.2); they must
    not modify ``raw_text`` or ``normalized_text``.
    """
    found = {alias for symbol, alias in _GREEK_ALIASES.items() if symbol in text}
    return sorted(found)
