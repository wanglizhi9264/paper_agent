"""Explicit Docling model setup (spec pdf-ingestion-v2 §7.2, §15.1).

Model downloads must never happen as a test or worker side effect; this
command is the single explicit entry point::

    uv run python -m app.cli.docling_setup [--skip-download] [--check <pdf>]

Steps:
1. optionally runs ``docling-tools models download`` (argv list, no shell);
2. resolves the Hugging Face revision SHA for the configured layout/table
   model repositories and prints ready-to-paste ``.env`` lines;
3. with ``--check``, parses one PDF end to end to verify the installation.

The command prints no secrets and never reads OpenCode credentials.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from typing import Any


def _resolve_revision(repo_id: str) -> str | None:
    try:
        from huggingface_hub import HfApi
    except ImportError:
        print("huggingface_hub is not installed; install the 'pdf-layout' extra", file=sys.stderr)
        return None
    try:
        info: Any = HfApi().model_info(repo_id=repo_id, revision="main")
    except Exception as exc:
        print(f"could not resolve revision for {repo_id}: {type(exc).__name__}", file=sys.stderr)
        return None
    sha = getattr(info, "sha", None)
    return str(sha) if sha else None


def _download_models() -> bool:
    import shutil

    tool = shutil.which("docling-tools")
    if not tool:
        print(
            "docling-tools executable not found; activate the project venv "
            "with the 'pdf-layout' extra installed",
            file=sys.stderr,
        )
        return False
    try:
        result = subprocess.run(
            [tool, "models", "download"],
            check=False,
        )
    except OSError as exc:
        print(f"could not start docling-tools: {type(exc).__name__}", file=sys.stderr)
        return False
    return result.returncode == 0


def _check_parse(pdf: str) -> bool:
    from pathlib import Path
    from uuid import uuid4

    from app.core.config import get_settings
    from app.loaders.docling_adapter import DoclingParser

    settings = get_settings()
    parser = DoclingParser.from_settings(settings)
    try:
        ir = parser.parse(Path(pdf), document_id=uuid4())
    except Exception as exc:
        code = getattr(exc, "code", type(exc).__name__)
        print(f"check parse failed [{code}]: {exc}", file=sys.stderr)
        return False
    print(
        f"check parse ok: pages={len(ir.pages)} elements={len(ir.elements)} "
        f"tables={ir.quality.table_count}"
    )
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Explicit Docling model setup")
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="only resolve revisions; skip `docling-tools models download`",
    )
    parser.add_argument("--check", default=None, help="verify with a real PDF after setup")
    args = parser.parse_args(argv)

    from app.core.config import get_settings

    settings = get_settings()
    layout_repo = settings.docling_layout_model
    table_repo = settings.docling_table_model

    if not args.skip_download:
        print("running: docling-tools models download ...")
        if not _download_models():
            print("model download failed", file=sys.stderr)
            return 1

    layout_sha = _resolve_revision(layout_repo)
    table_sha = _resolve_revision(table_repo)

    print("\n# Paste into .env to pin the parser signature (spec §6.2):")
    print(f"PAPER_RAG_DOCLING_LAYOUT_MODEL={layout_repo}")
    print(f"PAPER_RAG_DOCLING_TABLE_MODEL={table_repo}")
    print(f"PAPER_RAG_DOCLING_LAYOUT_REVISION={layout_sha or ''}")
    print(f"PAPER_RAG_DOCLING_TABLE_REVISION={table_sha or ''}")
    if not layout_sha or not table_sha:
        print(
            "\nwarning: revision resolution incomplete; A/B runs stay usable, "
            "but production activation requires pinned revisions (spec §14)",
            file=sys.stderr,
        )

        return 1

    if args.check and not _check_parse(args.check):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
