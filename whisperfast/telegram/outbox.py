"""Outgoing Telegram replies queued for the account listener.

The GUI and the account process must not open the same Telethon session at once.
The listener drains this directory and sends the files.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from whisperfast.config import BASE_DIR


def outbox_dir() -> str:
    return os.path.join(BASE_DIR, ".ftw_tg_out")


def enqueue_outgoing(
    chat_id: int | None,
    reply_to: int | None,
    text: str = "",
    files: List[Dict[str, str]] | None = None,
    chat_name: str = "",
    *,
    kind: str = "",
    source_message_id: int | None = None,
    status_token: int | None = None,
    delete_source: bool = False,
) -> str:
    folder = outbox_dir()
    os.makedirs(folder, exist_ok=True)
    payload: Dict[str, Any] = {
        "chat_id": None if chat_id is None else int(chat_id),
        "chat_name": str(chat_name or "").strip(),
        "reply_to": None if reply_to is None else int(reply_to),
        "text": text or "",
        "files": list(files or []),
        "ts": time.time(),
        "attempts": 0,
        "kind": str(kind or ""),
        "source_message_id": None if source_message_id is None else int(source_message_id),
        "status_token": None if status_token is None else int(status_token),
        "delete_source": bool(delete_source),
    }
    name = f"{time.time_ns()}_{os.getpid()}.json"
    path = os.path.join(folder, name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp, path)
    return path


def list_outgoing_paths() -> List[str]:
    """Paths of pending outbox JSON files (oldest first). Does not delete."""
    folder = outbox_dir()
    if not os.path.isdir(folder):
        return []
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith(".json"))
    except OSError:
        return []
    return [os.path.join(folder, name) for name in names]


def load_outgoing(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("chat_id") is None and not str(data.get("chat_name") or "").strip():
        return None
    return data


def save_outgoing(path: str, data: Dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp, path)


def remove_outgoing(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def take_outgoing() -> List[Dict[str, Any]]:
    """Legacy helper for tests: read and remove every pending item."""
    out: List[Dict[str, Any]] = []
    for path in list_outgoing_paths():
        data = load_outgoing(path)
        remove_outgoing(path)
        if data is not None:
            out.append(data)
    return out
