from __future__ import annotations

from app.core.security import redact_api_key, safe_join_within, sanitize_display_filename


def test_sanitize_display_filename() -> None:
    assert sanitize_display_filename("hello world.pdf") == "hello_world.pdf"
    assert sanitize_display_filename("") == "untitled"
    assert sanitize_display_filename("../../etc") == ".._.._etc"


def test_redact_api_key() -> None:
    assert redact_api_key("") == "<empty>"
    assert redact_api_key("short") == "<redacted>"
    assert redact_api_key("sk-abcdef12345") == "sk-***45"


def test_safe_join_within_rejects_traversal(tmp_path) -> None:
    import pytest

    root = tmp_path / "root"
    root.mkdir()
    assert safe_join_within(root, "a", "b.txt") == (root / "a" / "b.txt").resolve()

    with pytest.raises(ValueError):
        safe_join_within(root, "..", "x")
    with pytest.raises(ValueError):
        safe_join_within(root, "/etc/passwd")
    # symlink escape
    target = tmp_path / "outside.txt"
    target.write_text("x")
    link = root / "link"
    link.symlink_to(target)
    with pytest.raises(ValueError):
        safe_join_within(root, "link")
