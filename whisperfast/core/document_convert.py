"""Convert document/text inputs to Markdown for Cursor post-processing."""
from __future__ import annotations

import os
import shutil
from typing import Tuple

from whisperfast.config import OFFICE_TO_MD_EXTENSIONS, TEXT_EXTENSIONS


def is_document_file(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    return ext in TEXT_EXTENSIONS or ext in OFFICE_TO_MD_EXTENSIONS


def needs_office_to_md(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in OFFICE_TO_MD_EXTENSIONS


def markdown_output_path(source_path: str, output_dir: str) -> str:
    base = os.path.splitext(os.path.basename(source_path))[0]
    return os.path.abspath(os.path.join(output_dir, base + ".md"))


def _read_text_file(path: str) -> str:
    for encoding in ("utf-8", "utf-8-sig", "cp1251", "latin-1"):
        try:
            with open(path, "r", encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    with open(path, "rb") as f:
        return f.read().decode("utf-8", errors="replace")


def convert_office_to_markdown(source_path: str, md_path: str) -> str:
    """Convert PDF/DOC/DOCX to Markdown via markitdown. Returns md_path."""
    try:
        from markitdown import MarkItDown
    except ImportError as e:
        raise RuntimeError(
            "markitdown is not installed. Use [Dependencies] or: pip install \"markitdown[pdf]\""
        ) from e

    converter = MarkItDown()
    result = converter.convert(source_path)
    text = (getattr(result, "text_content", None) or "").strip()
    if not text:
        raise RuntimeError("conversion produced empty Markdown")
    os.makedirs(os.path.dirname(md_path) or ".", exist_ok=True)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(text)
        if not text.endswith("\n"):
            f.write("\n")
    return md_path


def ensure_markdown_for_cursor(source_path: str, output_dir: str) -> Tuple[str, bool]:
    """
    Prepare a Markdown file for Cursor.

    Returns (md_path, was_converted) where was_converted is True for PDF/DOC/DOCX
    (or when a non-md text file was written as .md in the output dir).
    """
    source_path = os.path.abspath(source_path)
    ext = os.path.splitext(source_path)[1].lower()
    md_path = markdown_output_path(source_path, output_dir)

    if needs_office_to_md(source_path):
        convert_office_to_markdown(source_path, md_path)
        return md_path, True

    if ext in (".md", ".markdown"):
        if os.path.normcase(os.path.abspath(source_path)) == os.path.normcase(md_path):
            return source_path, False
        os.makedirs(output_dir, exist_ok=True)
        shutil.copy2(source_path, md_path)
        return md_path, False

    # Other text formats → write .md copy in the output directory
    text = _read_text_file(source_path)
    os.makedirs(output_dir, exist_ok=True)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(text)
        if text and not text.endswith("\n"):
            f.write("\n")
    return md_path, True
