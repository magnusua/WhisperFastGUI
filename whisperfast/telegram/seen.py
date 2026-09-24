"""Remember a Telegram file id so a forward is not downloaded or processed again."""
from __future__ import annotations

import json
import os
from typing import Any, Mapping, Optional, Sequence

from whisperfast.config import BASE_DIR

_FILE = os.path.join(BASE_DIR, ".ftw_tg_seen.json")
_QUEUE = os.path.join(BASE_DIR, "request_queue.json")


def telethon_file_key(message) -> Optional[str]:
    """Stable id of a user-account file. The same media keeps it when forwarded."""
    for attr in ("document", "photo"):
        obj = getattr(message, attr, None)
        file_id = getattr(obj, "id", None)
        if file_id:
            return f"{attr}:{file_id}"
    handle = getattr(message, "file", None)
    file_id = getattr(handle, "id", None) if handle is not None else None
    if file_id:
        return f"file:{file_id}"
    return None


def bot_file_key(obj: Mapping[str, Any]) -> Optional[str]:
    """Bot API file_unique_id. file_id itself changes between messages."""
    if not isinstance(obj, Mapping):
        return None
    unique = str(obj.get("file_unique_id") or "").strip()
    if unique:
        return f"uniq:{unique}"
    return None


def _key_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _load() -> dict:
    try:
        with open(_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    tmp = _FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, _FILE)


def note_download(file_key: str, path: str, size: Optional[int] = None) -> None:
    if not file_key or not path:
        return
    data = _load()
    row = data.get(file_key) if isinstance(data.get(file_key), dict) else {}
    row = dict(row)
    row["path"] = os.path.abspath(path)
    if size is not None:
        row["size"] = int(size)
    row.setdefault("outputs", [])
    row.setdefault("targets", [])
    data[file_key] = row
    _save(data)


def note_outputs(path: str, files: Sequence[Mapping[str, Any]]) -> None:
    """Remember the transcript and AI files produced for this download."""
    if not path:
        return
    want = _key_path(path)
    data = _load()
    changed = False
    stored = []
    for item in files:
        if not isinstance(item, Mapping):
            continue
        file_path = str(item.get("path") or "")
        if not file_path:
            continue
        stored.append({
            "path": os.path.abspath(file_path),
            "caption": str(item.get("caption") or ""),
            "role": str(item.get("role") or ""),
        })
    if not stored:
        return
    for row in data.values():
        if not isinstance(row, dict):
            continue
        if _key_path(str(row.get("path") or "")) != want:
            continue
        row["outputs"] = stored
        changed = True
    if changed:
        _save(data)


def add_target(file_key: str, chat_id: int, message_id: int) -> None:
    data = _load()
    row = data.get(file_key)
    if not isinstance(row, dict):
        return
    targets = list(row.get("targets") or [])
    item = {"chat_id": int(chat_id), "message_id": int(message_id)}
    if item not in targets:
        targets.append(item)
    row["targets"] = targets
    data[file_key] = row
    _save(data)


def take_targets(path: str) -> list:
    """Chats that forwarded this file while it was still being processed."""
    if not path:
        return []
    want = _key_path(path)
    data = _load()
    found = []
    changed = False
    for row in data.values():
        if not isinstance(row, dict):
            continue
        if _key_path(str(row.get("path") or "")) != want:
            continue
        found.extend(list(row.get("targets") or []))
        if row.get("targets"):
            row["targets"] = []
            changed = True
    if changed:
        _save(data)
    return found


def retarget(old_path: str, new_path: str) -> None:
    if not old_path or not new_path:
        return
    old = _key_path(old_path)
    data = _load()
    changed = False
    for row in data.values():
        if isinstance(row, dict) and _key_path(str(row.get("path") or "")) == old:
            row["path"] = os.path.abspath(new_path)
            changed = True
    if changed:
        _save(data)


def path_in_queue(path: str) -> bool:
    want = _key_path(path)
    try:
        with open(_QUEUE, "r", encoding="utf-8") as handle:
            rows = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(rows, list):
        return False
    for row in rows:
        if isinstance(row, dict) and _key_path(str(row.get("path") or "")) == want:
            return not bool(row.get("processed"))
    return False


def classify(file_key: Optional[str]) -> tuple:
    """download, enqueue, wait, or send. The record is the stored download, if any."""
    if not file_key:
        return "download", None
    row = _load().get(file_key)
    if not isinstance(row, dict):
        return "download", None
    path = str(row.get("path") or "")
    if not path or not os.path.isfile(path):
        return "download", None
    live = []
    for item in row.get("outputs") or []:
        if isinstance(item, Mapping) and item.get("path") and os.path.isfile(str(item["path"])):
            live.append(dict(item))
    if live:
        ready = dict(row)
        ready["outputs"] = live
        ready["ai"] = any(str(item.get("role") or "") == "ai" for item in live)
        return "send", ready
    if path_in_queue(path):
        return "wait", row
    return "enqueue", row
