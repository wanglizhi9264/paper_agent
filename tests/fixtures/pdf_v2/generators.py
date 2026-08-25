"""Generate synthetic PDF fixtures for V2-0 baseline (spec §19 V2-0).

All fixtures are small, MIT-licensed, and contain no third-party content.
PDF generation runs in a **subprocess** because PyMuPDF's native extension
segfaults inside the pytest process on macOS/arm64.

Each fixture exercises a specific parsing challenge:
  - simple_table: normal table with headers and data rows
  - multi_header_table: multi-level column headers
  - merged_cells: merged cells (rowspan/colspan)
  - multicolumn: two-column layout
  - unicode_chars: Unicode characters (±, ε, θ, Σ, etc.)
  - hyphenation: hyphenated line breaks
  - cross_page: paragraphs crossing pages
  - model_metrics: model names with metric values
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_GEN_SCRIPT = r"""
import sys
from pathlib import Path

mode = sys.argv[1]
out = Path(sys.argv[2])

import pymupdf

def make_simple_table(doc):
    # Table with single-row header and data rows.
    page = doc.new_page()
    page.insert_text((72, 72), "Table 1: Model Performance", fontsize=12)
    # Draw table borders
    y0, y1 = 100, 120  # header row
    cols = [(72, 200), (200, 320), (320, 440)]
    for x0, x1 in cols:
        page.draw_rect(pymupdf.Rect(x0, y0, x1, y1), color=(0,0,0), width=0.5)
    # Header text
    page.insert_text((80, 115), "Model", fontsize=10)
    page.insert_text((208, 115), "IS", fontsize=10)
    page.insert_text((328, 115), "FID", fontsize=10)
    # Data rows
    models = [("DDPM", "9.46±0.11", "3.17"), ("GAN", "8.32±0.10", "4.51")]
    for i, (m, is_val, fid) in enumerate(models):
        ry0 = 120 + i * 20
        ry1 = ry0 + 20
        for x0, x1 in cols:
            page.draw_rect(pymupdf.Rect(x0, ry0, x1, ry1), color=(0,0,0), width=0.5)
        page.insert_text((80, ry0 + 15), m, fontsize=10)
        page.insert_text((208, ry0 + 15), is_val, fontsize=10)
        page.insert_text((328, ry0 + 15), fid, fontsize=10)
    doc.save(str(out))
    doc.close()

def make_multi_header_table(doc):
    # Table with two-level column headers.
    page = doc.new_page()
    page.insert_text((72, 72), "Table 2: Comparison", fontsize=12)
    # Level 1 header
    cols = [(72, 180), (180, 300), (300, 420), (420, 540)]
    y0, y1 = 100, 120
    for x0, x1 in cols:
        page.draw_rect(pymupdf.Rect(x0, y0, x1, y1), color=(0,0,0), width=0.5)
    page.insert_text((80, 115), "Method", fontsize=10)
    # Merged top header for two groups
    page.draw_rect(pymupdf.Rect(180, 100, 300, 110), color=(0,0,0), width=0.5)
    page.insert_text((210, 108), "HumanAct12", fontsize=8)
    page.draw_rect(pymupdf.Rect(300, 100, 420, 110), color=(0,0,0), width=0.5)
    page.insert_text((330, 108), "UESTC", fontsize=8)
    # Level 2 headers
    y2a, y2b = 110, 120
    page.draw_rect(pymupdf.Rect(180, y2a, 240, y2b), color=(0,0,0), width=0.5)
    page.insert_text((185, y2b - 3), "FIDtr", fontsize=7)
    page.draw_rect(pymupdf.Rect(240, y2a, 300, y2b), color=(0,0,0), width=0.5)
    page.insert_text((245, y2b - 3), "Acc", fontsize=7)
    page.draw_rect(pymupdf.Rect(300, y2a, 360, y2b), color=(0,0,0), width=0.5)
    page.insert_text((305, y2b - 3), "FID", fontsize=7)
    page.draw_rect(pymupdf.Rect(360, y2a, 420, y2b), color=(0,0,0), width=0.5)
    page.insert_text((365, y2b - 3), "Acc", fontsize=7)
    # Data rows
    rows = [("ACTOR", "0.12", "0.85", "0.15", "0.82"), ("MDM", "0.20", "0.80", "0.25", "0.77")]
    for i, vals in enumerate(rows):
        ry0 = 120 + i * 20
        ry1 = ry0 + 20
        for x0, x1 in cols:
            page.draw_rect(pymupdf.Rect(x0, ry0, x1, ry1), color=(0,0,0), width=0.5)
        for j, v in enumerate(vals):
            page.insert_text((80 + j * 120, ry0 + 15), v, fontsize=10)
    doc.save(str(out))
    doc.close()

def make_merged_cells(doc):
    # Table with merged cells (colspan).
    page = doc.new_page()
    page.insert_text((72, 72), "Table 3: Ablation", fontsize=12)
    cols = [(72, 200), (200, 350), (350, 500)]
    y0, y1 = 100, 120
    # Top header spans two columns
    page.draw_rect(pymupdf.Rect(72, y0, 200, y1), color=(0,0,0), width=0.5)
    page.insert_text((80, 115), "Model", fontsize=10)
    page.draw_rect(pymupdf.Rect(200, y0, 500, 110), color=(0,0,0), width=0.5)
    page.insert_text((280, 108), "ImageNet-4", fontsize=9)
    page.draw_rect(pymupdf.Rect(200, 110, 350, y1), color=(0,0,0), width=0.5)
    page.insert_text((210, 118), "H+L+FiLM", fontsize=7)
    page.draw_rect(pymupdf.Rect(350, 110, 500, y1), color=(0,0,0), width=0.5)
    page.insert_text((360, 118), "T+F+KD", fontsize=7)
    # Data rows
    data = [("H", "0.45", "0.52"), ("T", "0.38", "0.41")]
    for i, (m, v1, v2) in enumerate(data):
        ry0 = 120 + i * 20
        ry1 = ry0 + 20
        for x0, x1 in cols:
            page.draw_rect(pymupdf.Rect(x0, ry0, x1, ry1), color=(0,0,0), width=0.5)
        page.insert_text((80, ry0 + 15), m, fontsize=10)
        page.insert_text((210, ry0 + 15), v1, fontsize=10)
        page.insert_text((360, ry0 + 15), v2, fontsize=10)
    doc.save(str(out))
    doc.close()

def make_multicolumn(doc):
    # Two-column layout.
    page = doc.new_page()
    # Left column
    page.insert_text((72, 72), "Introduction", fontsize=12)
    left_text = ("In this paper we propose a novel approach to motion generation "
                 "that leverages transformer architectures. Our method achieves "
                 "state-of-the-art results on multiple benchmarks.")
    # Insert as wrapped text in left column (72-280)
    import textwrap
    wrapped = textwrap.wrap(left_text, width=35)
    for i, line in enumerate(wrapped):
        page.insert_text((72, 95 + i * 15), line, fontsize=10)
    # Right column
    page.insert_text((320, 72), "Related Work", fontsize=12)
    right_text = ("Prior work on motion generation has primarily used GANs and "
                  "diffusion models. Recent advances in transformers have opened "
                  "new possibilities for sequence modeling in this domain.")
    wrapped_r = textwrap.wrap(right_text, width=35)
    for i, line in enumerate(wrapped_r):
        page.insert_text((320, 95 + i * 15), line, fontsize=10)
    doc.save(str(out))
    doc.close()

def make_unicode_chars(doc):
    # Unicode and special characters.
    page = doc.new_page()
    page.insert_text((72, 72), "Results with Special Characters", fontsize=12)
    lines = [
        "The model achieves IS = 9.46±0.11 and FID = 3.17.",
        "We use ε as the noise variable in diffusion models.",
        "The angle θ determines the rotation matrix.",
        "Summation Σ is used over all timesteps.",
        "The loss is L = E[||ε - ε_θ||²] + λL_simple.",
        "Memory usage: 6.5 GB (peak), latency: 13.61 min.",
        "Comparison: 13.61 / 13.09 min for two methods.",
        "Accuracy: 85.2%, F1-score: 0.923.",
        "Greek letters: α β γ δ ε ζ η θ ι κ λ μ ν ξ π ρ σ τ φ χ ψ ω",
        "Math symbols: √ ∑ ∏ ∫ ∂ ∇ ∈ ∉ ⊆ ⊇ ∞ ∀ ∃ ¬ ∧ ∨ ⊕ ⊖",
    ]
    for i, line in enumerate(lines):
        page.insert_text((72, 100 + i * 18), line, fontsize=10)
    doc.save(str(out))
    doc.close()

def make_hyphenation(doc):
    # Hyphenated line breaks.
    page = doc.new_page()
    page.insert_text((72, 72), "Hyphenation Test", fontsize=12)
    # Text with explicit hyphenation at line ends
    lines = [
        "This is a paragraph with long-",
        "word hyphenation that may cause",
        "issues with text reconstruction.",
        "The model uses trans-",
        "former-based archi-",
        "tecture for motion gene-",
        "ration. This is a feature-",
        "rich approach.",
        "Numbers like 13.61-",
        "13.09 should not be merged.",
    ]
    for i, line in enumerate(lines):
        page.insert_text((72, 100 + i * 16), line, fontsize=10)
    doc.save(str(out))
    doc.close()

def make_cross_page(doc):
    # Paragraphs crossing pages.
    import textwrap
    p1 = doc.new_page()
    p1.insert_text((72, 72), "Cross-Page Paragraph", fontsize=12)
    para1 = ("This paragraph starts on page 1 and continues to the next page. "
             "The text should be reconstructed as a single coherent paragraph "
             "even though it spans across a page boundary. The key challenge "
             "is detecting that the continuation on page 2 belongs to the same "
             "paragraph and should not be treated as a new section or heading.")
    wrapped = textwrap.wrap(para1, width=50)
    for i, line in enumerate(wrapped[:8]):
        p1.insert_text((72, 95 + i * 15), line, fontsize=10)
    p2 = doc.new_page()
    for i, line in enumerate(wrapped[8:]):
        p2.insert_text((72, 95 + i * 15), line, fontsize=10)
    p2.insert_text((72, 230), "## Next Section", fontsize=12)
    p2.insert_text((72, 255), "This is a new section that starts after the cross-page paragraph.", fontsize=10)
    doc.save(str(out))
    doc.close()

def make_model_metrics(doc):
    # Model names with metric values, simulating paper table structure.
    page = doc.new_page()
    page.insert_text((72, 72), "Table 4: Results Comparison", fontsize=12)
    # Table with model rows and metric columns
    y = 100
    headers = ["Method", "Dataset", "IS", "FID", "Acc"]
    col_x = [80, 200, 310, 380, 450]
    # Header
    for i, h in enumerate(headers):
        page.insert_text((col_x[i], y + 15), h, fontsize=10)
    page.draw_rect(pymupdf.Rect(72, y, 530, y+20), color=(0,0,0), width=0.5)
    # Data rows
    data = [
        ("DDPM (Lsimple)", "CIFAR", "9.46±0.11", "3.17", "-"),
        ("EEG2IM", "ImageNet-4", "8.12", "4.51", "0.85"),
        ("ACTOR", "HumanAct12", "-", "0.12", "0.85"),
        ("LMM-Large", "UESTC", "-", "0.15", "0.82"),
    ]
    for j, row in enumerate(data):
        ry = y + 20 + j * 20
        for i, v in enumerate(row):
            page.insert_text((col_x[i], ry + 15), v, fontsize=10)
        page.draw_rect(pymupdf.Rect(72, ry, 530, ry+20), color=(0,0,0), width=0.5)
    # Add a paragraph mentioning the metrics
    page.insert_text((72, y + 120), "The DDPM model achieves IS of 9.46±0.11 and FID of 3.17 on CIFAR.", fontsize=10)
    page.insert_text((72, y + 140), "EEG2IM with H+L+FiLM achieves IS of 8.12 and FID of 4.51 on ImageNet-4.", fontsize=10)
    doc.save(str(out))
    doc.close()

mode = sys.argv[1]
out = Path(sys.argv[2])
doc = pymupdf.open()
if mode == "simple_table":
    make_simple_table(doc)
elif mode == "multi_header_table":
    make_multi_header_table(doc)
elif mode == "merged_cells":
    make_merged_cells(doc)
elif mode == "multicolumn":
    make_multicolumn(doc)
elif mode == "unicode_chars":
    make_unicode_chars(doc)
elif mode == "hyphenation":
    make_hyphenation(doc)
elif mode == "cross_page":
    make_cross_page(doc)
elif mode == "model_metrics":
    make_model_metrics(doc)
else:
    raise ValueError(f"Unknown mode: {mode}")
"""


def _run(mode: str, path: Path) -> Path:
    """Generate a fixture file by running the generator script in a subprocess."""
    path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-c", _GEN_SCRIPT, mode, str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"fixture generation '{mode}' failed: {result.stderr or result.stdout}")
    return path


FIXTURE_MODES = [
    "simple_table",
    "multi_header_table",
    "merged_cells",
    "multicolumn",
    "unicode_chars",
    "hyphenation",
    "cross_page",
    "model_metrics",
]


def generate_all(output_dir: Path) -> dict[str, Path]:
    """Generate all V2-0 fixtures and return a mapping name -> path."""
    paths: dict[str, Path] = {}
    for mode in FIXTURE_MODES:
        p = output_dir / f"{mode}.pdf"
        _run(mode, p)
        paths[mode] = p
    return paths
