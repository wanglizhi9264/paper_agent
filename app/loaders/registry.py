"""Loader registry — selects a loader by lowercase file extension (spec §11.1).

Adding a loader only requires a new implementation and registration here;
the ingestion pipeline never branches on format.
"""

from __future__ import annotations

from pathlib import Path

from app.loaders.base import BaseLoader, UnsupportedExtensionError
from app.loaders.docx import DocxLoader
from app.loaders.markdown import MarkdownLoader
from app.loaders.pdf import PdfLoader

_REGISTRY: dict[str, BaseLoader] = {}


def register(extension: str, loader: BaseLoader) -> None:
    ext = extension.lower().lstrip(".")
    _REGISTRY[ext] = loader


def get_loader(extension: str) -> BaseLoader:
    ext = extension.lower().lstrip(".")
    loader = _REGISTRY.get(ext)
    if loader is None:
        raise UnsupportedExtensionError(
            f"No loader registered for extension '{ext}'. "
            f"Supported: {', '.join(sorted(_REGISTRY))}",
            code="UNSUPPORTED_MEDIA_TYPE",
        )
    return loader


def get_loader_for_path(path: Path) -> BaseLoader:
    if not path.suffix:
        raise UnsupportedExtensionError(
            f"File '{path.name}' has no extension.",
            code="UNSUPPORTED_MEDIA_TYPE",
        )
    return get_loader(path.suffix)


def supported_extensions() -> frozenset[str]:
    return frozenset(_REGISTRY)


def reset_registry() -> None:
    """Clear all registrations (test helper)."""
    _REGISTRY.clear()


def register_defaults() -> None:
    """Register the three MVP loaders."""
    register("pdf", PdfLoader())
    register("docx", DocxLoader())
    register("md", MarkdownLoader())


# Auto-register defaults on import.
register_defaults()
