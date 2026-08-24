"""Heading tree for hierarchical section structure (spec §12.3).

Nodes store: title, level, section_path, content (list of paragraphs),
parent, children, character_count.

Rules:
- Heading level skipping: a heading whose level is more than 1 greater than
  the previous heading attaches to the nearest lower-level ancestor
  (spec §12.3).
- Content before any heading attaches to a virtual root that does NOT
  generate a title chunk.
- ``section_path`` is the list of titles from the top-level ancestor down to
  the current node (exclusive of the virtual root).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.loaders.base import Paragraph


@dataclass(slots=True)
class HeadingNode:
    """A node in the heading tree."""

    title: str
    level: int
    section_path: list[str]
    paragraphs: list[Paragraph] = field(default_factory=list)
    parent: HeadingNode | None = None
    children: list[HeadingNode] = field(default_factory=list)
    character_count: int = 0

    def add_paragraph(self, p: Paragraph) -> None:
        self.paragraphs.append(p)
        self.character_count += len(p.content)

    def add_child(self, child: HeadingNode) -> None:
        child.parent = self
        self.children.append(child)


@dataclass(slots=True)
class HeadingTree:
    """The full heading tree with a virtual root.

    The virtual root has ``level=0`` and ``title=""``. Content before the first
    heading belongs to the root. The root never generates a title chunk.
    """

    root: HeadingNode

    @property
    def all_nodes(self) -> list[HeadingNode]:
        """All nodes in DFS order, excluding the virtual root."""
        result: list[HeadingNode] = []

        def _walk(node: HeadingNode) -> None:
            for child in node.children:
                result.append(child)
                _walk(child)

        _walk(self.root)
        return result

    @property
    def leaf_nodes_with_content(self) -> list[HeadingNode]:
        """Nodes that have paragraphs (content-bearing), in DFS order."""
        return [n for n in self.all_nodes if n.paragraphs]


def build_heading_tree(paragraphs: list[Paragraph], *, max_level: int = 10) -> HeadingTree:
    """Build a heading tree from parsed paragraphs.

    Paragraphs with ``metadata.heading_level`` are treated as headings;
    all others are content attached to the current node.
    """
    root = HeadingNode(title="", level=0, section_path=[])
    current = root
    # Stack of (level, node) for ancestor tracking.
    stack: list[tuple[int, HeadingNode]] = [(0, root)]

    for p in paragraphs:
        heading_level = p.metadata.get("heading_level")
        if heading_level is not None and isinstance(heading_level, int) and heading_level > 0:
            level = min(heading_level, max_level)
            # Find the nearest ancestor with level < current.
            while stack and stack[-1][0] >= level:
                stack.pop()
            parent = stack[-1][1] if stack else root
            # If level skips (e.g., h1 -> h3), attach to nearest lower ancestor.
            section_path = [*parent.section_path, p.metadata.get("heading", p.content)]
            node = HeadingNode(
                title=p.metadata.get("heading", p.content),
                level=level,
                section_path=section_path,
            )
            parent.add_child(node)
            stack.append((level, node))
            current = node
        else:
            current.add_paragraph(p)

    # Root also may have paragraphs (content before first heading).
    return HeadingTree(root=root)


def collect_content_in_order(tree: HeadingTree) -> list[tuple[HeadingNode, Paragraph]]:
    """Return (node, paragraph) pairs in document order.

    Root paragraphs come first (untitled), then each heading node's paragraphs
    in DFS order.
    """
    result: list[tuple[HeadingNode, Paragraph]] = []
    for p in tree.root.paragraphs:
        result.append((tree.root, p))
    for node in tree.all_nodes:
        for p in node.paragraphs:
            result.append((node, p))
    return result
