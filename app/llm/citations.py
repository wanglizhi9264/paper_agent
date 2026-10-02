from __future__ import annotations

import re
from dataclasses import dataclass

REFUSAL_PREFIX = "Insufficient evidence in the provided sources."


@dataclass
class Citation:
    index: int  # [1] → 1
    chunk_id: str


def parse_citations(text: str, citation_map: dict[int, str]) -> tuple[list[Citation], list[int]]:
    """Parse citation markers from text and validate against map (spec §15).

    Returns (valid_citations, invalid_indices).
    Invalid markers are removed from citations but recorded.
    """
    pattern = re.compile(r"\[(\d+)\]")
    seen: dict[int, list[int]] = {}  # index -> list of positions
    for match in pattern.finditer(text):
        idx = int(match.group(1))
        seen.setdefault(idx, []).append(match.start())

    valid: list[Citation] = []
    invalid: list[int] = []

    for idx in sorted(seen.keys()):
        if idx in citation_map:
            valid.append(Citation(index=idx, chunk_id=citation_map[idx]))
        else:
            invalid.append(idx)

    return valid, invalid


def strip_invalid_markers(text: str, invalid_indices: list[int]) -> str:
    """Remove invalid citation markers from text (spec §15)."""
    result = text
    for idx in invalid_indices:
        result = result.replace(f"[{idx}]", "")
    return result


def validate_citations(
    text: str,
    citation_map: dict[int, str],
) -> tuple[str, list[Citation], list[int]]:
    """Full citation validation: parse, validate, strip invalid (spec §15).

    Returns (cleaned_text, valid_citations, invalid_indices).
    """
    valid, invalid = parse_citations(text, citation_map)
    cleaned = strip_invalid_markers(text, invalid)
    return cleaned, valid, invalid


def finalize_answer(
    text: str,
    citation_map: dict[int, str],
) -> tuple[str, list[Citation], list[int]]:
    """Apply the externally visible refusal and citation contract."""
    cleaned, valid, invalid = validate_citations(text, citation_map)
    if cleaned.lstrip().casefold().startswith(REFUSAL_PREFIX.casefold()):
        return REFUSAL_PREFIX, [], invalid
    return cleaned, valid, invalid


class RefusalStreamGate:
    """Buffer only long enough to decide whether a stream begins with the refusal prefix."""

    def __init__(self) -> None:
        self._buffer = ""
        self._mode = "pending"

    def feed(self, value: str) -> str:
        if not value or self._mode == "refusal":
            return ""
        if self._mode == "normal":
            return value
        self._buffer += value
        candidate = self._buffer.lstrip()
        folded = candidate.casefold()
        prefix = REFUSAL_PREFIX.casefold()
        if prefix.startswith(folded):
            return ""
        if folded.startswith(prefix):
            self._mode = "refusal"
            self._buffer = ""
            return REFUSAL_PREFIX
        self._mode = "normal"
        result = self._buffer
        self._buffer = ""
        return result

    def finish(self) -> str:
        if self._mode != "pending":
            return ""
        self._mode = "normal"
        result = self._buffer
        self._buffer = ""
        return result
