from __future__ import annotations

import re
from dataclasses import dataclass


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
