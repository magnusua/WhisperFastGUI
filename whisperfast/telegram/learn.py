"""Ask before processing a chat that is not on the automatic list."""
from __future__ import annotations

import queue
from typing import Sequence
from urllib.parse import urlsplit

from whisperfast.settings import normalize_chat_ids, normalize_chat_names
from whisperfast.telegram.account import names_match

_ready = queue.Queue()


def learn_enabled(settings) -> bool:
    return bool(settings.get("telegram_learn_mode"))


def chat_is_automatic(chat_id: int, chat_names: Sequence[str], settings) -> bool:
    """Named chats and allowlisted ids are processed without a question."""
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
