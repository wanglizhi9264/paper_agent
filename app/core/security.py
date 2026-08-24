from __future__ import annotations

import re
from pathlib import Path

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_display_filename(name: str, *, max_len: int = 200) -> str:
    """Return a user-facing filename safe for display (not for storage paths)."""
    name = name.strip()
    if not name:
        return "untitled"
    return _SAFE_FILENAME_RE.sub("_", name)[:max_len]


def safe_join_within(root: Path, *parts: str) -> Path:
    """Join ``parts`` under ``root`` rejecting traversal, absolute paths and symlinks.

    Used whenever a user-influenced path component must be combined with a
    configured storage root. The resolved path must stay strictly inside ``root``.
    """
    if not parts:
        raise ValueError("safe_join_within requires at least one path part")
    raw = Path(*parts)
    if raw.is_absolute() or any(p in {"..", "."} for p in raw.parts):
        raise ValueError("absolute or traversal paths are not allowed")
    resolved = (root / raw).resolve()
    root_resolved = root.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError("path escapes storage root") from exc
    if resolved.is_symlink():
        raise ValueError("symlink targets are not allowed")
    return resolved


def redact_api_key(key: str) -> str:
    """Return a non-revealing representation for logs."""
    if not key:
        return "<empty>"
    if len(key) <= 8:
        return "<redacted>"
    return f"{key[:3]}***{key[-2:]}"
