"""Remember which Telegram chat a media file came from, even after the queue row is gone."""
from __future__ import annotations

import json
import os
from typing import Optional

from whisperfast.config import BASE_DIR

_FILE = os.path.join(BASE_DIR, ".ftw_tg_origin.json")


def _key(path: str) -> str:
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


def remember(path: str, chat_id: int, message_id: int) -> None:
    if not path:
        return
    data = _load()
    data[_key(path)] = {"chat_id": int(chat_id), "message_id": int(message_id)}
    _save(data)


def retarget(old_path: str, new_path: str) -> None:
    if not old_path or not new_path:
        return
    data = _load()
    row = data.get(_key(old_path))
    if not row:
        return
    data.pop(_key(old_path), None)
    data[_key(new_path)] = row
    _save(data)


def lookup(path: str) -> Optional[dict]:
    if not path:
        return None
    row = _load().get(_key(path))
    if not isinstance(row, dict):
        return None
    try:
        return {"chat_id": int(row["chat_id"]), "message_id": int(row["message_id"])}
    except (KeyError, TypeError, ValueError):
        return None
