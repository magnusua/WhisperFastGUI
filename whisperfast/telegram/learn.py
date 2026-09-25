"""Ask before processing a chat that is not on the automatic list."""
from __future__ import annotations

import json
import os
import queue
from typing import Sequence
from urllib.parse import urlsplit

from whisperfast.config import BASE_DIR
from whisperfast.settings import normalize_chat_ids, normalize_chat_names
from whisperfast.telegram.account import names_match

_ready = queue.Queue()
_FILE = os.path.join(BASE_DIR, ".ftw_tg_learn.json")
_BUCKETS = ("always", "never", "ask")


def learn_enabled(settings) -> bool:
    return bool(settings.get("telegram_learn_mode"))


def load_learn_chats() -> dict:
    """Chats remembered from learning: always, never, and ask-every-time."""
    try:
        with open(_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    result = {}
    for bucket in _BUCKETS:
        rows = []
        seen = set()
        for item in data.get(bucket) or []:
            if isinstance(item, str):
                name, number = item, 0
            elif isinstance(item, dict):
                name = str(item.get("name") or "")
                try:
                    number = int(item.get("id") or 0)
                except (TypeError, ValueError):
                    number = 0
            else:
                continue
            name = name.strip()
            key = (name.casefold(), number)
            if (not name and not number) or key in seen:
                continue
            seen.add(key)
            rows.append({"name": name, "id": number})
        result[bucket] = rows
    return result


def save_learn_chats(chats: dict) -> None:
    clean = {bucket: [] for bucket in _BUCKETS}
    for bucket in _BUCKETS:
        for item in chats.get(bucket) or []:
            name = str(item.get("name") or "").strip()
            try:
                number = int(item.get("id") or 0)
            except (TypeError, ValueError):
                number = 0
            if name or number:
                clean[bucket].append({"name": name, "id": number})
    tmp = _FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(clean, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, _FILE)


def remember_learn_chat(bucket: str, name: str, chat_id) -> None:
    """Move one chat into always, never, or ask and drop it from the other two."""
    if bucket not in _BUCKETS:
        return
    title = str(name or "").strip()
    try:
        number = int(chat_id or 0)
    except (TypeError, ValueError):
        number = 0
    chats = load_learn_chats()
    for key in _BUCKETS:
        kept = []
        for item in chats[key]:
            same_name = title and item["name"].casefold() == title.casefold()
            same_id = number and item["id"] == number
            if not same_name and not same_id:
                kept.append(item)
        chats[key] = kept
    if title or number:
        chats[bucket].append({"name": title, "id": number})
    save_learn_chats(chats)


def _in_bucket(bucket: str, chat_id: int, chat_names: Sequence[str]) -> bool:
    rows = load_learn_chats().get(bucket) or []
    names = [row["name"] for row in rows if row.get("name")]
    if names_match(chat_names, names):
        return True
    try:
        number = int(chat_id)
    except (TypeError, ValueError):
        return False
    return any(row.get("id") == number for row in rows if row.get("id"))


def chat_always_asks(chat_id: int, chat_names: Sequence[str]) -> bool:
    return _in_bucket("ask", chat_id, chat_names)


def chat_is_ignored(chat_id: int, chat_names: Sequence[str], settings) -> bool:
    """Chats the user refused once stay out of the queue."""
    if _in_bucket("never", chat_id, chat_names):
        return True
    if names_match(chat_names, normalize_chat_names(settings.get("telegram_ignored_chat_names"))):
        return True
    try:
        number = int(chat_id)
    except (TypeError, ValueError):
        return False
    return number in set(normalize_chat_ids(settings.get("telegram_ignored_chat_ids")))


def chat_is_automatic(chat_id: int, chat_names: Sequence[str], settings) -> bool:
    """Named chats and allowlisted ids are processed without a question."""
    if chat_always_asks(chat_id, chat_names):
        return False
    if _in_bucket("always", chat_id, chat_names):
        return True
    if names_match(chat_names, normalize_chat_names(settings.get("telegram_self_chat_names"))):
        return True
    return int(chat_id) in set(normalize_chat_ids(settings.get("telegram_allowed_chat_ids")))


def network_name(url: str) -> str:
    host = urlsplit(url or "").netloc.lower().split(":")[0]
    if host.startswith("www.") or host.startswith("m."):
        host = host.split(".", 1)[1]
    if host in ("facebook.com", "fb.watch", "fb.com"):
        return "Facebook"
    if host == "instagram.com":
        return "Instagram"
    if host in ("youtube.com", "youtu.be"):
        return "YouTube"
    return host or url


def material_label(filename: str = "", urls: Sequence[str] = ()) -> str:
    """File name, or a short link name plus the social network."""
    links = []
    for url in urls or ():
        tail = urlsplit(url).path.strip("/").split("/")[-1] or network_name(url)
        links.append(f"{tail} ({network_name(url)})")
    if filename and links:
        return filename + ", " + ", ".join(links)
    if links:
        return ", ".join(links)
    return filename or ""


def push_ready(item) -> None:
    _ready.put(item)


def take_ready():
    items = []
    while True:
        try:
            items.append(_ready.get_nowait())
        except queue.Empty:
            return items
