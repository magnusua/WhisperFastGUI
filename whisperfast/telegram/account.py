"""Listen to private chats on the signed-in Telegram account (not a bot)."""
from __future__ import annotations

import asyncio
import os
from typing import Any, Callable, Mapping, Optional, Sequence

from whisperfast.config import BASE_DIR
from whisperfast.i18n import t
from whisperfast.settings import normalize_chat_ids, normalize_chat_names
from whisperfast.telegram.outbox import take_outgoing
from whisperfast.telegram.worker import _extension_ok, chat_display_name, resolve_work_dir, safe_filename

LogFunc = Callable[[str], None]


def normalize_mode(value: Any) -> str:
    mode = str(value or "bot").strip().lower()
    return mode if mode in ("bot", "account") else "bot"


def session_base_path() -> str:
    """Telethon adds the .session suffix itself."""
    return os.path.join(BASE_DIR, "telegram_user")


def chat_name_keys(chat) -> set:
    """Names a private chat can be matched by: full name, first name, username, title."""
    keys = set()

    def add(value):
        text = str(value or "").strip().casefold().lstrip("@")
        if text:
            keys.add(text)

    add(getattr(chat, "title", ""))
    first = str(getattr(chat, "first_name", "") or "").strip()
    last = str(getattr(chat, "last_name", "") or "").strip()
    add(first)
    add(f"{first} {last}".strip())
    add(getattr(chat, "username", ""))
    return keys


def names_match(chat_names: Sequence[str], wanted: Sequence[str]) -> bool:
    keys = {str(name).strip().casefold().lstrip("@") for name in chat_names if str(name).strip()}
    targets = {str(name).strip().casefold().lstrip("@") for name in wanted if str(name).strip()}
    return bool(keys & targets)


def accept_private_chat(
    chat_id: int,
    *,
    is_private: bool,
    sender_is_bot: bool,
    outgoing: bool,
    self_id: int,
    allowlist: Sequence[int],
    self_names: Sequence[str] = (),
    chat_names: Sequence[str] = (),
    is_group: bool = False,
) -> bool:
    """Private chats, plus groups whose title is listed. Channels stay out."""
    if sender_is_bot or not (is_private or is_group):
        return False
    if is_group:
        if not names_match(chat_names, self_names):
            return False
    elif outgoing and int(chat_id) != int(self_id) and not names_match(chat_names, self_names):
        return False
    allowed = set(int(item) for item in allowlist)
    if allowed and int(chat_id) not in allowed:
        return False
    return True


def media_filename(name: str, mime: str) -> Optional[str]:
    mime_l = (mime or "").lower()
    filename = name or ""
    if not _extension_ok(filename, mime_l):
        return None
    if not os.path.splitext(filename)[1]:
        if mime_l.startswith("video/"):
            filename = (filename or "video") + ".mp4"
        elif mime_l.startswith("audio/"):
            filename = (filename or "voice") + ".ogg"
        else:
            filename = (filename or "file") + ".bin"
    return safe_filename(filename, "file.bin")


def _file_bits(message) -> tuple:
    handle = getattr(message, "file", None)
    if handle is None:
        return "", ""
    return str(getattr(handle, "name", "") or ""), str(getattr(handle, "mime_type", "") or "")


async def _flush_outbox(client) -> None:
    for item in take_outgoing():
        chat_id = int(item["chat_id"])
        reply_to = item.get("reply_to")
        reply = int(reply_to) if reply_to is not None else None
        text = str(item.get("text") or "")
        if text:
            await client.send_message(chat_id, text, reply_to=reply)
        for entry in item.get("files") or []:
            path = str(entry.get("path") or "")
            if path and os.path.isfile(path):
                await client.send_file(
                    chat_id,
                    path,
                    caption=str(entry.get("caption") or ""),
                    reply_to=reply,
                )


async def _handle_message(client, event, settings, submit, gui_running, self_id: int, log: LogFunc) -> None:
    message = event.message
    chat_id = int(getattr(event, "chat_id", 0) or 0)
    sender = await event.get_sender()
    sender_is_bot = bool(getattr(sender, "bot", False))
    from whisperfast.settings import normalize_chat_names

    allowlist = normalize_chat_ids(settings.get("telegram_allowed_chat_ids"))
    self_names = normalize_chat_names(settings.get("telegram_self_chat_names"))
    chat = await event.get_chat()
    if not accept_private_chat(
        chat_id,
        is_private=bool(getattr(event, "is_private", False)),
        is_group=bool(getattr(event, "is_group", False)),
        sender_is_bot=sender_is_bot,
        outgoing=bool(getattr(event, "out", False)),
        self_id=self_id,
        allowlist=allowlist,
        self_names=self_names,
        chat_names=chat_name_keys(chat),
    ):
        return
    name, mime = _file_bits(message)
    filename = media_filename(name, mime)
    if not filename:
        return
    log(t("telegram_found", name=filename, chat=chat_display_name(chat, chat_id)))
    if not gui_running():
        await event.reply(t("telegram_gui_required"))
        return
    await event.reply(t("telegram_queued", position=1))
    folder = os.path.join(resolve_work_dir(settings), f"{chat_id}_{int(message.id)}")
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, filename)
    downloaded = await message.download_media(file=dest)
    local = downloaded or dest
    if not local or not os.path.isfile(local):
        await event.reply(t("telegram_failed", error=filename))
        return
    try:
        submit(local, chat_id, int(message.id))
    except Exception as exc:
        await event.reply(t("telegram_failed", error=str(exc)))
        return
    await event.reply(t("telegram_gui_added", name=os.path.basename(local)))
    log(t("telegram_gui_added", name=os.path.basename(local)))


def run_account(
    settings: Mapping[str, Any],
    *,
    submit: Optional[Callable] = None,
    gui_running: Optional[Callable[[], bool]] = None,
    log: Optional[LogFunc] = None,
    stop: Optional[Callable[[], bool]] = None,
) -> int:
    log = log or print
    try:
        from telethon import TelegramClient, events
    except ImportError:
        log(t("telegram_telethon_missing"))
        return 1

    api_id = str(settings.get("telegram_api_id") or "").strip()
    api_hash = str(settings.get("telegram_api_hash") or "").strip()
    phone = str(settings.get("telegram_phone") or "").strip()
    if not api_id or not api_hash:
        log(t("telegram_no_api_credentials"))
        return 1
    if not phone:
        log(t("telegram_phone_required"))
        return 1

    from whisperfast.telegram.gui_bridge import gui_is_running, submit_to_running_gui

    if submit is None:
        submit = submit_to_running_gui
    if gui_running is None:
        gui_running = gui_is_running

    async def _main():
        client = TelegramClient(session_base_path(), int(api_id), api_hash)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                log(t("telegram_sign_in_first"))
                return 1
            me = await client.get_me()
            self_id = int(me.id)

            @client.on(events.NewMessage)
            async def _on_message(event):
                await _handle_message(client, event, settings, submit, gui_running, self_id, log)

            async def _pump():
                while not (stop and stop()):
                    await _flush_outbox(client)
                    await asyncio.sleep(1)
                await client.disconnect()

            pump = asyncio.create_task(_pump())
            own_names = normalize_chat_names(settings.get("telegram_self_chat_names"))
            own_list = ", ".join(own_names) if own_names else t("telegram_own_only_saved")
            started = t(
                "telegram_account_listening",
                name=(me.first_name or me.username or str(me.id)),
                chats=own_list,
            )
            log(started)
            try:
                await client.send_message("me", started)
            except Exception as exc:
                log(t("telegram_start_notice_failed", error=str(exc)))
            try:
                await client.run_until_disconnected()
            finally:
                pump.cancel()
                try:
                    await pump
                except asyncio.CancelledError:
                    pass
            return 0
        finally:
            await client.disconnect()

    try:
        return int(asyncio.run(_main()) or 0)
    except KeyboardInterrupt:
        print("stopped")
        return 0
    except Exception as exc:
        log(str(exc))
        return 1


def sign_in(api_id: str, api_hash: str, phone: str, ask_code: Callable[[], str], ask_password: Callable[[], str]) -> str:
    """Interactive login. Returns a short display name. Raises RuntimeError on failure."""
    try:
        from telethon import TelegramClient
        from telethon.errors import SessionPasswordNeededError
    except ImportError as exc:
        raise RuntimeError(t("telegram_telethon_missing")) from exc

    async def _main():
        client = TelegramClient(session_base_path(), int(api_id), api_hash)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                sent = await client.send_code_request(phone)
                code = (ask_code() or "").strip()
                if not code:
                    raise RuntimeError(t("telegram_code_cancelled"))
                try:
                    await client.sign_in(phone, code, phone_code_hash=sent.phone_code_hash)
                except SessionPasswordNeededError:
                    password = ask_password() or ""
                    if not password:
                        raise RuntimeError(t("telegram_code_cancelled"))
                    await client.sign_in(password=password)
            me = await client.get_me()
            label = (me.first_name or "").strip()
            if me.username:
                label = (label + f" (@{me.username})").strip()
            return label or str(me.id)
        finally:
            await client.disconnect()

    try:
        return str(asyncio.run(_main()))
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc


def signed_in_label(api_id: str, api_hash: str) -> str:
    """Empty string when the saved session is missing or not authorized."""
    try:
        from telethon import TelegramClient
    except ImportError:
        return ""
    if not str(api_id or "").strip() or not str(api_hash or "").strip():
        return ""

    async def _main():
        client = TelegramClient(session_base_path(), int(api_id), api_hash)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                return ""
            me = await client.get_me()
            label = (me.first_name or "").strip()
            if me.username:
                label = (label + f" (@{me.username})").strip()
            return label or str(me.id)
        finally:
            await client.disconnect()

    try:
        return str(asyncio.run(_main()) or "")
    except Exception:
        return ""
