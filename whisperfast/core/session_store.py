"""Session folder sidecars: <video>.meta.json, <video>.summary.md, optional audio cleanup."""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional


def session_dir_for(path: str) -> str:
    return os.path.dirname(os.path.abspath(path or "")) or ""


def source_stem(source: str) -> str:
    """Filename stem of a video, audio, or transcript. Empty when there is no name."""
    stem = os.path.splitext(os.path.basename(source or ""))[0].strip().rstrip(".")
    return stem


def _is_meta_filename(name: str) -> bool:
    lower = (name or "").lower()
    return lower == "meta.json" or lower.endswith(".meta.json")


def meta_path(folder: str, source: str = "") -> str:
    """``Interview.meta.json`` beside the transcript. Legacy ``meta.json`` when no name is known."""
    if not folder:
        return ""
    stem = source_stem(source)
    name = f"{stem}.meta.json" if stem else "meta.json"
    return os.path.join(folder, name)


def summary_path(folder: str, source: str = "") -> str:
    """``Interview.summary.md`` beside the transcript. Legacy ``summary.md`` when no name is known."""
    if not folder:
        return ""
    stem = source_stem(source)
    name = f"{stem}.summary.md" if stem else "summary.md"
    return os.path.join(folder, name)


def _read_json_dict(path: str) -> Dict[str, Any]:
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def _same_stem(left: str, right: str) -> bool:
    a = source_stem(left)
    b = source_stem(right)
    return bool(a) and a.casefold() == b.casefold()


def session_meta_files(folder: str) -> List[str]:
    """Named ``*.meta.json`` files and a legacy ``meta.json`` in this folder."""
    if not folder or not os.path.isdir(folder):
        return []
    found = []
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    for name in names:
        if not _is_meta_filename(name):
            continue
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            found.append(os.path.abspath(path))
    found.sort()
    return found


def _folder_has_meta(folder: str) -> bool:
    return bool(session_meta_files(folder))


def load_meta(folder: str, source: str = "") -> Dict[str, Any]:
    """Meta for ``source`` when given; otherwise the newest sidecar in the folder.

    A legacy ``meta.json`` is reused only when its ``source`` field is the same video.
    """
    stem = source_stem(source)
    if stem:
        named = meta_path(folder, source)
        data = _read_json_dict(named)
        if data:
            return data
        legacy = _read_json_dict(os.path.join(folder, "meta.json"))
        if legacy and _same_stem(str(legacy.get("source") or ""), source):
            return legacy
        return {}
    files = session_meta_files(folder)
    if not files:
        return {}
    named = [p for p in files if os.path.basename(p).lower() != "meta.json"]
    pool = named or files

    def _mtime(path: str) -> float:
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    return _read_json_dict(max(pool, key=_mtime))


def write_meta(folder: str, meta: Dict[str, Any], source: str = "") -> str:
    if not folder:
        return ""
    os.makedirs(folder, exist_ok=True)
    path = meta_path(folder, source)
    payload = dict(meta or {})
    payload.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path


def merge_meta(folder: str, updates: Dict[str, Any], source: str = "") -> str:
    current = load_meta(folder, source)
    current.update({k: v for k, v in (updates or {}).items() if v is not None})
    return write_meta(folder, current, source)


def write_summary(folder: str, text: str, source: str = "") -> str:
    if not folder:
        return ""
    os.makedirs(folder, exist_ok=True)
    path = summary_path(folder, source)
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
    if _folder_has_meta(root):
        found.append(os.path.abspath(root))
    for name in names:
        path = os.path.join(root, name)
        if os.path.isdir(path) and _folder_has_meta(path):
            found.append(os.path.abspath(path))
        elif os.path.isdir(path):
            try:
                for nested in os.listdir(path):
                    child = os.path.join(path, nested)
                    if os.path.isdir(child) and _folder_has_meta(child):
                        found.append(os.path.abspath(child))
            except OSError:
                pass
    return found
