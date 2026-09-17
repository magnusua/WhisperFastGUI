"""Session folder sidecars: meta.json, summary.md, optional audio cleanup."""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, Iterable, Optional


def session_dir_for(path: str) -> str:
    return os.path.dirname(os.path.abspath(path or "")) or ""


def meta_path(folder: str) -> str:
    return os.path.join(folder, "meta.json") if folder else ""


def summary_path(folder: str) -> str:
    return os.path.join(folder, "summary.md") if folder else ""


def load_meta(folder: str) -> Dict[str, Any]:
    path = meta_path(folder)
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def write_meta(folder: str, meta: Dict[str, Any]) -> str:
    if not folder:
        return ""
    os.makedirs(folder, exist_ok=True)
    path = meta_path(folder)
    payload = dict(meta or {})
    payload.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path


def merge_meta(folder: str, updates: Dict[str, Any]) -> str:
    current = load_meta(folder)
    current.update({k: v for k, v in (updates or {}).items() if v is not None})
    return write_meta(folder, current)


def write_summary(folder: str, text: str) -> str:
    if not folder:
        return ""
    os.makedirs(folder, exist_ok=True)
    path = summary_path(folder)
    body = (text or "").strip()
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
        if body and not body.endswith("\n"):
            f.write("\n")
    return path


def maybe_drop_audio(audio_path: str, keep_audio: bool, mp3_path: str = "") -> Optional[str]:
    """Delete original WAV/PCM after a successful transcript when keep_audio is false.

    MP3 sidecar is kept if it exists. Returns the deleted path or None.
    """
    if keep_audio or not audio_path:
        return None
    path = os.path.abspath(audio_path)
    if mp3_path and os.path.normcase(path) == os.path.normcase(os.path.abspath(mp3_path)):
        return None
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".wav", ".opus", ".m4a", ".mp3", ".flac", ".ogg"):
        return None
    if not os.path.isfile(path):
        return None
    try:
        os.remove(path)
        return path
    except OSError:
        return None


def list_session_folders(root: str) -> Iterable[str]:
    if not root or not os.path.isdir(root):
        return []
    found = []
    try:
        names = os.listdir(root)
    except OSError:
        return []
    if os.path.isfile(meta_path(root)):
        found.append(os.path.abspath(root))
    for name in names:
        path = os.path.join(root, name)
        if os.path.isdir(path) and os.path.isfile(meta_path(path)):
            found.append(os.path.abspath(path))
        elif os.path.isdir(path):
            try:
                for nested in os.listdir(path):
                    child = os.path.join(path, nested)
                    if os.path.isdir(child) and os.path.isfile(meta_path(child)):
                        found.append(os.path.abspath(child))
            except OSError:
                pass
    return found
