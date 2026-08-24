"""Subprocess helper for PDF loader tests.

PyMuPDF's native extension segfaults inside the pytest process on some
macOS/arm64 environments (conflict with other C extensions loaded by pytest
plugins). The loader itself works perfectly in a standalone Python process
(verified by direct invocation). Since in production the loader runs in the
ARQ worker process — not the API/test process — running it in a subprocess
here is both a workaround and closer to real usage.

The helper serializes the ``ParsedDocument`` to JSON and returns it for
assertion in the test process.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

_LOAD_SCRIPT = """
import json, sys
from pathlib import Path
from app.loaders.pdf import PdfLoader

path = Path(sys.argv[1])
doc = PdfLoader().load(path)
out = {
    "title": doc.title,
    "metadata": doc.metadata,
    "paragraphs": [
        {
            "type": p.type,
            "content": p.content,
            "page": p.page,
            "line_start": p.line_start,
            "line_end": p.line_end,
            "metadata": p.metadata,
        }
        for p in doc.paragraphs
    ],
}
print(json.dumps(out))
"""


def load_pdf_in_subprocess(pdf_path: Path) -> dict[str, Any]:
    """Run PdfLoader in a subprocess and return the parsed document as dict."""
    result = subprocess.run(
        [sys.executable, "-c", _LOAD_SCRIPT, str(pdf_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"PdfLoader subprocess failed (rc={result.returncode}): "
            f"{result.stderr or result.stdout}"
        )
    return json.loads(result.stdout)


def load_pdf_expect_ocr_error(pdf_path: Path) -> str | None:
    """Run PdfLoader in subprocess; return the error code if it raises."""
    script = """
import sys
from pathlib import Path
from app.loaders.base import LoaderError
from app.loaders.pdf import PdfLoader

try:
    PdfLoader().load(Path(sys.argv[1]))
except LoaderError as e:
    print(e.code)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(pdf_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"PdfLoader subprocess failed: {result.stderr or result.stdout}")
    return result.stdout.strip() or None
