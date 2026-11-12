"""Shared PDF-rendering helpers for the delivery_*_pdf.py renderers.

Every `delivery_*_pdf.py` module owns its own page CSS, footer template and
`render_*_pdf()` entrypoint (those stay artifact-specific), but all of them
previously carried their own byte-for-byte copy of these three pieces. Moved
here once a drift was found between copies: three of the five did not guard
the pandoc subprocess call against `OSError` (e.g. the binary vanishing
between the `shutil.which` check and the call) while two did -- every caller
now gets the guarded version.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import storage_locations


class PDFRenderError(RuntimeError):
    """A stable, user-readable PDF rendering failure."""

    def __init__(self, code: str, detail: str | None = None):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def _resolve_output_path(output_path: str | Path, data_root: str | Path | None = None) -> Path:
    path = Path(output_path).expanduser().resolve()
    if path.suffix.lower() != ".pdf":
        raise PDFRenderError("pdf_output_must_have_pdf_extension")
    protected_name = storage_locations.protected_canonical_data_kind(
        path, data_root if data_root is not None else Path(__file__).resolve().parents[2]
    )
    if protected_name:
        raise PDFRenderError("pdf_output_cannot_target_canonical_data", protected_name)
    if path.exists() and path.is_dir():
        raise PDFRenderError("pdf_output_path_is_directory")
    return path


def _markdown_to_html(markdown: str, page_css: str) -> str:
    """Convert `markdown` to a standalone HTML document with `page_css`
    injected just before `</head>`. `page_css` is the caller's own
    artifact-specific `_PAGE_CSS` (page size, fonts, per-heading rules) --
    kept as a parameter rather than a module-level default so this function
    has no opinion about any one artifact's layout."""
    pandoc = shutil.which("pandoc")
    if not pandoc:
        raise PDFRenderError("pdf_runtime_missing", "Pandoc is required for Markdown conversion")
    command = [pandoc, "--from=markdown", "--to=html5", "--standalone", "--wrap=none", "--metadata=lang:zh-TW"]
    try:
        result = subprocess.run(
            command,
            input=markdown,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise PDFRenderError("pdf_markdown_conversion_failed", type(exc).__name__) from exc
    if result.returncode != 0:
        detail = result.stderr.strip()[:500] or f"exit status {result.returncode}"
        raise PDFRenderError("pdf_markdown_conversion_failed", detail)
    if "</head>" not in result.stdout or "<body" not in result.stdout:
        raise PDFRenderError("pdf_markdown_conversion_invalid_output")
    return result.stdout.replace("</head>", page_css + "\n</head>", 1)
