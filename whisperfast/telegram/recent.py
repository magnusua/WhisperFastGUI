"""The last groups and contacts that received a video or its results."""
from __future__ import annotations

import json
import os

from whisperfast.config import BASE_DIR

_FILE = os.path.join(BASE_DIR, ".ftw_tg_recent.json")
_LIMIT = 10


def recent_names() -> list:
    try:
        with open(_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    names = []
    for item in data:
        text = str(item or "").strip()
        if text and text.casefold() not in {name.casefold() for name in names}:
            names.append(text)
        if len(names) >= _LIMIT:
            break
    return names


def remember_name(name: str) -> None:
    text = str(name or "").strip()
    if not text or text.isdigit():
        return
    names = [item for item in recent_names() if item.casefold() != text.casefold()]
    names.insert(0, text)
    tmp = _FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(names[:_LIMIT], handle, ensure_ascii=False, indent=2)
    os.replace(tmp, _FILE)
