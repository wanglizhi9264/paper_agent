"""Dependency direction guard for app.document_ir (spec §4).

document_ir must never import loaders, ORM, FastAPI, ARQ, FAISS, LLM,
services, workers, or API code. This test parses the package sources and
fails on any forbidden import.
"""

from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN_PREFIXES = (
    "app.loaders",
    "app.models",
    "app.db",
    "app.services",
    "app.workers",
    "app.api",
    "app.embedding",
    "app.llm",
    "app.index",
    "app.retrieval",
    "app.rerank",
    "app.chunking",
    "app.context",
    "fastapi",
    "starlette",
    "sqlalchemy",
    "arq",
    "faiss",
    "sentence_transformers",
    "pymupdf",
    "fitz",
    "docling",
    "docx",
)

DOCUMENT_IR_DIR = Path(__file__).resolve().parents[3] / "app" / "document_ir"


def _imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_document_ir_package_exists() -> None:
    assert DOCUMENT_IR_DIR.is_dir()
    assert (DOCUMENT_IR_DIR / "__init__.py").is_file()


def test_no_forbidden_imports() -> None:
    offenders: list[str] = []
    for source in sorted(DOCUMENT_IR_DIR.glob("*.py")):
        for module in _imports_of(source):
            if module.startswith(FORBIDDEN_PREFIXES):
                offenders.append(f"{source.name}: {module}")
    assert not offenders, f"forbidden imports in document_ir: {offenders}"


def test_only_expected_public_modules() -> None:
    expected = {
        "__init__.py",
        "errors.py",
        "markdown.py",
        "models.py",
        "normalize.py",
        "protocol.py",
        "serialize.py",
        "validate.py",
    }
    actual = {path.name for path in DOCUMENT_IR_DIR.glob("*.py")}
    assert actual == expected
