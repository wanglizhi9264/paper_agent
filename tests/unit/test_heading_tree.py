from __future__ import annotations

from app.chunking.heading_tree import (
    build_heading_tree,
    collect_content_in_order,
)
from app.loaders.base import Paragraph


def _heading(text: str, level: int) -> Paragraph:
    return Paragraph(
        type="markdown", content=text, metadata={"heading_level": level, "heading": text}
    )


def _text(text: str) -> Paragraph:
    return Paragraph(type="text", content=text)


def test_flat_headings() -> None:
    paras = [_heading("Intro", 1), _text("Body 1"), _heading("Method", 1), _text("Body 2")]
    tree = build_heading_tree(paras)
    nodes = tree.all_nodes
    assert len(nodes) == 2
    assert nodes[0].title == "Intro"
    assert nodes[0].section_path == ["Intro"]
    assert nodes[1].title == "Method"
    assert nodes[1].section_path == ["Method"]
    assert len(nodes[0].paragraphs) == 1
    assert len(nodes[1].paragraphs) == 1


def test_nested_headings() -> None:
    paras = [
        _heading("A", 1),
        _text("a body"),
        _heading("A1", 2),
        _text("a1 body"),
        _heading("B", 1),
        _text("b body"),
    ]
    tree = build_heading_tree(paras)
    nodes = tree.all_nodes
    assert len(nodes) == 3
    assert nodes[0].title == "A"
    assert nodes[1].title == "A1"
    assert nodes[1].section_path == ["A", "A1"]
    assert nodes[1].parent is nodes[0]
    assert nodes[2].title == "B"
    assert nodes[2].section_path == ["B"]


def test_heading_skip_attaches_to_nearest_ancestor() -> None:
    """h1 -> h3 (skipping h2) should attach h3 under h1 (spec §12.3)."""
    paras = [
        _heading("Top", 1),
        _text("top body"),
        _heading("Deep", 3),
        _text("deep body"),
    ]
    tree = build_heading_tree(paras)
    nodes = tree.all_nodes
    assert len(nodes) == 2
    assert nodes[1].title == "Deep"
    assert nodes[1].level == 3
    assert nodes[1].parent is nodes[0]  # under h1, not a new root
    assert nodes[1].section_path == ["Top", "Deep"]


def test_content_before_first_heading_goes_to_root() -> None:
    paras = [_text("untitled intro"), _heading("S1", 1), _text("s1 body")]
    tree = build_heading_tree(paras)
    assert len(tree.root.paragraphs) == 1
    assert tree.root.paragraphs[0].content == "untitled intro"
    nodes = tree.all_nodes
    assert len(nodes) == 1
    assert nodes[0].title == "S1"


def test_virtual_root_not_in_all_nodes() -> None:
    paras = [_text("no headings at all")]
    tree = build_heading_tree(paras)
    assert tree.all_nodes == []
    assert len(tree.root.paragraphs) == 1


def test_collect_content_in_order() -> None:
    paras = [
        _text("before"),
        _heading("A", 1),
        _text("a1"),
        _text("a2"),
        _heading("B", 1),
        _text("b1"),
    ]
    tree = build_heading_tree(paras)
    ordered = collect_content_in_order(tree)
    assert len(ordered) == 4  # "before" + a1 + a2 + b1
    assert ordered[0][1].content == "before"
    assert ordered[1][1].content == "a1"
    assert ordered[3][1].content == "b1"
