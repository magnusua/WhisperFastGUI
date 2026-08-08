"""Load and format multilingual release notes from resources/release_notes.json."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from whisperfast.config import RESOURCES_DIR
from whisperfast.i18n import t

RELEASE_NOTES_FILENAME = "release_notes.json"
_SUPPORTED = ("EN", "UK", "RU")


def release_notes_path() -> str:
    return os.path.join(RESOURCES_DIR, RELEASE_NOTES_FILENAME)


def load_release_notes() -> List[Dict[str, Any]]:
    path = release_notes_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, TypeError):
        return []
    releases = data.get("releases") if isinstance(data, dict) else None
    if not isinstance(releases, list):
        return []
    return [r for r in releases if isinstance(r, dict) and r.get("version")]


def _notes_for_lang(notes_obj: Any, lang: str) -> List[str]:
    lang = (lang or "EN").upper()
    if lang not in _SUPPORTED:
        lang = "EN"
    if not isinstance(notes_obj, dict):
        return []
    lines = notes_obj.get(lang)
    if not isinstance(lines, list) or not lines:
        for fallback in ("EN", "UK", "RU"):
            lines = notes_obj.get(fallback)
            if isinstance(lines, list) and lines:
                break
        else:
            return []
    return [str(x).strip() for x in lines if str(x).strip()]


def format_release_notes_text(lang: Optional[str] = None) -> str:
    """Plain text for the release-notes window (current UI language)."""
    lang = (lang or "EN").upper()
    releases = load_release_notes()
    if not releases:
        return t("release_notes_empty")

    blocks: List[str] = []
    for rel in releases:
        version = str(rel.get("version") or "").strip()
        date = str(rel.get("date") or "").strip()
        header = t("release_notes_version_header", version=version, date=date or "—")
        bullets = _notes_for_lang(rel.get("notes"), lang)
        body = "\n".join(f"• {line}" for line in bullets) if bullets else "—"
        blocks.append(f"{header}\n{body}")
    return "\n\n".join(blocks) + "\n"
