"""Outgoing Telegram replies queued for the account listener.

The GUI and the account process must not open the same Telethon session at once.
The listener drains this directory and sends the files.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List

from whisperfast.config import BASE_DIR


def outbox_dir() -> str:
    return os.path.join(BASE_DIR, ".ftw_tg_out")


def enqueue_outgoing(chat_id: int, reply_to: int, text: str = "", files: List[Dict[str, str]] | None = None) -> str:
    folder = outbox_dir()
    os.makedirs(folder, exist_ok=True)
    payload: Dict[str, Any] = {
        "chat_id": int(chat_id),
        "reply_to": int(reply_to),
        "text": text or "",
        "files": list(files or []),
        "ts": time.time(),
    }
    name = f"{time.time_ns()}_{os.getpid()}.json"
    path = os.path.join(folder, name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp, path)
    return path


def take_outgoing() -> List[Dict[str, Any]]:
    folder = outbox_dir()
    if not os.path.isdir(folder):
        return []
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith(".json"))
    except OSError:
        return []
    out: List[Dict[str, Any]] = []
    for name in names:
        path = os.path.join(folder, name)
        data = None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError, TypeError):
            data = None
        try:
            os.remove(path)
        except OSError:
            pass
        if isinstance(data, dict) and data.get("chat_id") is not None:
            out.append(data)
    return out
