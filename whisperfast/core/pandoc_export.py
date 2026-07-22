"""Export Markdown to Office formats via Pandoc (DOCX now; PDF-ready API)."""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Callable, List, Optional, Sequence

from whisperfast.config import PANDOC_REFERENCE_DOCX
from whisperfast.platform_util import win_no_window_kwargs

# Formats implemented today. Add "pdf" here when PDF export is wired up.
SUPPORTED_EXPORT_FORMATS = ("docx",)
DEFAULT_EXPORT_FORMATS = ("docx",)

LogFunc = Callable[..., None]


def find_pandoc() -> Optional[str]:
    """Absolute path to pandoc executable, or None if not on PATH."""
    return shutil.which("pandoc")


def is_pandoc_available() -> bool:
    return find_pandoc() is not None


def pandoc_version() -> Optional[str]:
    """First line of `pandoc -v`, or None if Pandoc is missing / fails."""
    exe = find_pandoc()
    if not exe:
        return None
    try:
        result = subprocess.run(
            [exe, "-v"],
            capture_output=True,
            text=True,
            timeout=15,
            **win_no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    first = (result.stdout or result.stderr or "").strip().splitlines()
    return first[0].strip() if first else "pandoc"


def office_output_path(md_path: str, fmt: str) -> str:
    """name.md → name.docx / name.pdf (same directory)."""
    fmt = (fmt or "").lower().lstrip(".")
    base, _ = os.path.splitext(os.path.abspath(md_path))
    return base + "." + fmt


def default_reference_doc() -> Optional[str]:
    """Optional Word style template: resources/templates/reference.docx."""
    path = PANDOC_REFERENCE_DOCX
    return path if path and os.path.isfile(path) else None


def _build_pandoc_args(
    md_path: str,
    output_path: str,
    fmt: str,
    *,
    reference_doc: Optional[str] = None,
    extra_args: Optional[Sequence[str]] = None,
) -> List[str]:
    exe = find_pandoc()
    if not exe:
        raise RuntimeError(
            "Pandoc is not installed or not in PATH. "
            "Install from https://pandoc.org/installing.html"
        )
    fmt = fmt.lower().lstrip(".")
    args: List[str] = [exe, md_path, "-o", output_path, f"--to={fmt}"]

    if fmt == "docx":
        ref = reference_doc if reference_doc is not None else default_reference_doc()
        if ref and os.path.isfile(ref):
            args.append(f"--reference-doc={ref}")
    # PDF (future): --pdf-engine=..., --css=..., --template=...

    if extra_args:
        args.extend(extra_args)
    return args


def convert_markdown_with_pandoc(
    md_path: str,
    output_path: Optional[str] = None,
    *,
    fmt: str = "docx",
    reference_doc: Optional[str] = None,
    extra_args: Optional[Sequence[str]] = None,
) -> str:
    """
    Convert one Markdown file with Pandoc.

    fmt: "docx" now; "pdf" can be added later without changing call sites much.
    Returns absolute path of the created file.
    """
    md_path = os.path.abspath(md_path)
    if not os.path.isfile(md_path):
        raise FileNotFoundError(md_path)

    fmt = (fmt or "docx").lower().lstrip(".")
    if fmt not in SUPPORTED_EXPORT_FORMATS:
        raise ValueError(
            f"Export format '{fmt}' is not supported yet. "
            f"Supported: {', '.join(SUPPORTED_EXPORT_FORMATS)}"
        )

    output_path = os.path.abspath(output_path or office_output_path(md_path, fmt))
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    args = _build_pandoc_args(
        md_path,
        output_path,
        fmt,
        reference_doc=reference_doc,
        extra_args=extra_args,
    )
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=300,
            **win_no_window_kwargs(),
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("Pandoc timed out while converting Markdown") from e

    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip() or f"exit {result.returncode}"
        raise RuntimeError(f"Pandoc failed: {err}")

    if not os.path.isfile(output_path):
        raise RuntimeError(f"Pandoc did not create output file: {output_path}")
    return output_path


def export_markdown(
    md_path: str,
    formats: Sequence[str] = DEFAULT_EXPORT_FORMATS,
    *,
    reference_doc: Optional[str] = None,
    log_func: Optional[LogFunc] = None,
) -> List[str]:
    """
    Export Markdown to one or more Office formats.

    Currently only docx is implemented; pass formats=("docx", "pdf") later
    once PDF is added to SUPPORTED_EXPORT_FORMATS.
    Returns list of created file paths (skips failed formats if log_func set;
    re-raises if log_func is None).
    """
    created: List[str] = []
    for fmt in formats:
        fmt_norm = (fmt or "").lower().lstrip(".")
        try:
            path = convert_markdown_with_pandoc(
                md_path,
                fmt=fmt_norm,
                reference_doc=reference_doc,
            )
            created.append(path)
        except Exception as e:
            if log_func is None:
                raise
            try:
                from whisperfast.i18n import t
                log_func(t("pandoc_export_error", fmt=fmt_norm, error=str(e)))
            except ImportError:
                log_func(f"❌ Pandoc {fmt_norm} export failed: {e}")
    return created
