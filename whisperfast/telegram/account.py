"""Listen to private chats on the signed-in Telegram account (not a bot)."""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Callable, Mapping, Optional, Sequence

from whisperfast.config import BASE_DIR
from whisperfast.i18n import t
from whisperfast.settings import normalize_chat_ids, normalize_chat_names
from whisperfast.telegram.worker import _extension_ok, chat_display_name, resolve_work_dir, safe_filename

LogFunc = Callable[[str], None]
_link_tasks = set()
_settings_cache: tuple[float, Optional[dict]] = (0.0, None)
_SETTINGS_TTL_S = 1.5


def _fresh_settings(fallback: Optional[Mapping[str, Any]] = None) -> dict:
    """Reload settings at most every ~1.5s so allowlist edits apply without disk spam."""
    global _settings_cache
    now = time.monotonic()
    ts, cached = _settings_cache
    if cached is not None and now - ts < _SETTINGS_TTL_S:
        return cached
    from whisperfast.settings import load_app_settings

    data = load_app_settings()
    if not isinstance(data, dict):
        data = dict(fallback or {})
    _settings_cache = (now, data)
    return data


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
    contacts_only: bool = False,
    sender_is_contact: bool = False,
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
    if contacts_only and is_private and not is_group:
        if int(chat_id) == int(self_id):
            return True
        if outgoing and names_match(chat_names, self_names):
            return True
        if not sender_is_contact and int(chat_id) != int(self_id):
            return False
    return True


def is_telegram_sticker(message) -> bool:
    """Static, animated, and video stickers. Video stickers are silent webm."""
    return getattr(message, "sticker", None) is not None


def is_telegram_gif(message) -> bool:
    """Telegram GIFs are silent mp4 (or a large image/gif). Nothing to transcribe."""
    return getattr(message, "gif", None) is not None


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


async def _resolve_sender(event):
    sender = getattr(event, "sender", None)
    if sender is not None:
        return sender
    try:
        return await event.get_sender()
    except Exception:
        return None


async def _resolve_chat(event):
    chat = getattr(event, "chat", None)
    if chat is not None:
        return chat
    try:
        return await event.get_chat()
    except Exception:
        return None


async def _find_chat_id(client, wanted: str):
    """Match a typed group or contact name the same way the listener matches chats."""
    target = [wanted]
    async for dialog in client.iter_dialogs():
        title = str(getattr(dialog, "name", "") or "")
        entity = getattr(dialog, "entity", None)
        names = chat_name_keys(entity) if entity is not None else set()
        names.add(title.strip().casefold().lstrip("@"))
        if names_match(names, target):
            return int(dialog.id)
    return None


async def _flush_outbox(client, log=None) -> None:
    """Надіслати pending outbox. Файл зникає лише після успіху; помилка лишає його для retry."""
    from whisperfast.telegram.outbox import (
        list_outgoing_paths,
        load_outgoing,
        remove_outgoing,
        save_outgoing,
    )

    def _safe_log(msg: str) -> None:
        if not log:
            return
        try:
            log(msg)
        except Exception:
            pass

    async def _send_message(chat_id: int, text: str, reply: int | None) -> None:
        try:
            await client.send_message(chat_id, text, reply_to=reply)
        except Exception:
            if reply is None:
                raise
            await client.send_message(chat_id, text, reply_to=None)

    async def _send_file(chat_id: int, path: str, caption: str, reply: int | None) -> None:
        from whisperfast.telegram.send_media import telethon_send_kwargs

        extra = telethon_send_kwargs(path)
        try:
            await client.send_file(chat_id, path, caption=caption, reply_to=reply, **extra)
        except Exception:
            if reply is None:
                raise
            await client.send_file(chat_id, path, caption=caption, reply_to=None, **extra)

    for path in list_outgoing_paths():
        item = load_outgoing(path)
        if item is None:
            remove_outgoing(path)
            continue
        attempts = int(item.get("attempts") or 0)
        if attempts >= 8:
            remove_outgoing(path)
            _safe_log(t("telegram_outbox_give_up", name=os.path.basename(path)))
            continue
        try:
            raw_id = item.get("chat_id")
            chat_id = int(raw_id) if raw_id is not None else None
            wanted = str(item.get("chat_name") or "").strip()
            if chat_id is None:
                chat_id = await _find_chat_id(client, wanted) if wanted else None
                if chat_id is None:
                    _safe_log(t("telegram_chat_not_found", name=wanted or "?"))
                    remove_outgoing(path)
                    continue
            reply_to = item.get("reply_to")
            reply = int(reply_to) if reply_to is not None else None
            text = str(item.get("text") or "")
            if text:
                await _send_message(chat_id, text, reply)
            sent_names: list[str] = []
            for entry in item.get("files") or []:
                file_path = str(entry.get("path") or "")
                if file_path and os.path.isfile(file_path):
                    await _send_file(
                        chat_id,
                        file_path,
                        str(entry.get("caption") or ""),
                        reply,
                    )
                    sent_names.append(os.path.basename(file_path))
            remove_outgoing(path)
            label = wanted
            if not label:
                try:
                    from whisperfast.telegram.worker import chat_display_name

                    entity = await client.get_entity(chat_id)
                    label = chat_display_name(entity, chat_id)
                except Exception:
                    label = str(chat_id)
            if label:
                from whisperfast.telegram.recent import remember_name

                remember_name(label)
            if sent_names:
                _safe_log(
                    t(
                        "telegram_sent_files",
                        chat=label or str(chat_id),
                        names=", ".join(sent_names),
                    )
                )
        except Exception as exc:
            item["attempts"] = attempts + 1
            item["last_error"] = str(exc)
            try:
                save_outgoing(path, item)
            except OSError:
                pass
            _safe_log(t("telegram_outbox_failed", error=str(exc)))


async def _take_video_links(
    event,
    urls,
    settings,
    submit,
    log,
    chat_id: int,
    message_id: int,
    *,
    is_private: bool = False,
) -> None:
    """Поставити соц-посилання в окрему чергу (не Whisper/AI, не блокує Telethon loop)."""
    import threading

    from whisperfast.telegram.links import social_videos_go_to_queue
    from whisperfast.telegram.outbox import enqueue_outgoing
    from whisperfast.telegram.prompts_ask import (
        PROMPT_ASK_TIMEOUT_S,
        ask_prompt_nums,
        should_ask_prompts,
    )
    from whisperfast.telegram.social_queue import enqueue_social_urls

    def reply(text: str) -> None:
        enqueue_outgoing(chat_id, message_id, text=text)

    def send_files(files) -> None:
        enqueue_outgoing(
            chat_id,
            message_id,
            text="",
            files=[
                {"path": str(item.get("path") or ""), "caption": str(item.get("caption") or "")}
                for item in (files or [])
                if isinstance(item, dict) and item.get("path")
            ],
        )

    ask_holder: dict = {"nums": None, "done": threading.Event()}
    ask_needed = social_videos_go_to_queue(settings) and should_ask_prompts(
        settings, is_private=is_private
    )

    async def _run_ask() -> None:
        try:
            ask_holder["nums"] = await ask_prompt_nums(
                event, settings, on_timeout="none"
            )
        except Exception:
            ask_holder["nums"] = []
        finally:
            ask_holder["done"].set()

    if ask_needed:
        ask_task = asyncio.create_task(_run_ask())
        _link_tasks.add(ask_task)
        ask_task.add_done_callback(_link_tasks.discard)
    else:
        ask_holder["done"].set()

    def submit_whisper(path: str, cid: int, mid: int, prompt_nums=None) -> None:
        nums = prompt_nums
        if ask_needed:
            ask_holder["done"].wait(timeout=float(PROMPT_ASK_TIMEOUT_S) + 5.0)
            nums = ask_holder["nums"]
            if nums is None:
                nums = []
        try:
            submit(path, cid, mid, prompt_nums=nums)
        except TypeError:
            submit(path, cid, mid)

    enqueue_social_urls(
        list(urls),
        chat_id=chat_id,
        message_id=message_id,
        settings=settings,
        log=log,
        reply=reply,
        send_files=send_files,
        submit_whisper=submit_whisper,
    )


async def _take_video_links_guarded(
    event,
    urls,
    settings,
    submit,
    log,
    chat_id: int,
    message_id: int,
    *,
    is_private: bool = False,
) -> None:
    """Fire-and-forget у соц-чергу; збій не валить account listener."""
    try:
        await _take_video_links(
            event,
            urls,
            settings,
            submit,
            log,
            chat_id,
            message_id,
            is_private=is_private,
        )
    except Exception as exc:
        text = t("telegram_link_failed", error=str(exc))
        log(text)
        try:
            from whisperfast.telegram.outbox import enqueue_outgoing

            enqueue_outgoing(chat_id, message_id, text=text)
        except Exception:
            try:
                await event.reply(text)
            except Exception:
                pass


async def _await_learn(ask, log, chat_name: str, material: str, chat_id: int, outgoing: bool = False, replay=None):
    """Wait until the window answers. Closed dialog stays pending."""
    if not material:
        return False
    if ask is None:
        key = "telegram_learn_question_own" if outgoing else "telegram_learn_question"
        log(t(key, chat=chat_name, material=material))
        return False
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def settle(decision):
        if not future.done():
            loop.call_soon_threadsafe(future.set_result, decision)

    ask(chat_name, material, settle, chat_id, outgoing, replay)
    return await future in ("once", "always")


def _call_submit(submit, path: str, chat_id: int, message_id: int, prompt_nums=None) -> None:
    if prompt_nums is None:
        submit(path, chat_id, message_id)
        return
    try:
        submit(path, chat_id, message_id, prompt_nums=prompt_nums)
    except TypeError:
        submit(path, chat_id, message_id)


async def _handle_message(client, event, settings, submit, gui_running, self_id: int, log: LogFunc, ask=None) -> None:
    settings = _fresh_settings(settings)
    message = event.message
    if is_telegram_sticker(message) or is_telegram_gif(message):
        return
    chat_id = int(getattr(event, "chat_id", 0) or 0)
    text = str(getattr(message, "message", None) or getattr(message, "raw_text", None) or "")
    from whisperfast.telegram.prompts_ask import take_pending_reply

    name, mime = _file_bits(message)
    filename_early = media_filename(name, mime)
    if not filename_early and take_pending_reply(chat_id, text):
        return

    sender = await _resolve_sender(event)
    sender_is_bot = bool(getattr(sender, "bot", False))
    sender_is_contact = bool(getattr(sender, "contact", False))
    from whisperfast.telegram.learn import (
        chat_always_asks,
        chat_is_automatic,
        chat_is_ignored,
        learn_enabled,
        material_label,
    )

    allowlist = normalize_chat_ids(settings.get("telegram_allowed_chat_ids"))
    self_names = normalize_chat_names(settings.get("telegram_self_chat_names"))
    contacts_only = bool(settings.get("telegram_contacts_only"))
    chat = await _resolve_chat(event)
    chat_names = chat_name_keys(chat) if chat is not None else set()
    is_private = bool(getattr(event, "is_private", False))
    is_group = bool(getattr(event, "is_group", False))
    accepted = accept_private_chat(
        chat_id,
        is_private=is_private,
        is_group=is_group,
        sender_is_bot=sender_is_bot,
        outgoing=bool(getattr(event, "out", False)),
        self_id=self_id,
        allowlist=allowlist,
        self_names=self_names,
        chat_names=chat_names,
        contacts_only=contacts_only,
        sender_is_contact=sender_is_contact,
    )
    if chat_is_ignored(chat_id, chat_names, settings):
        return
    learning = learn_enabled(settings)
    if not accepted and not learning:
        return
    if not accepted and (sender_is_bot or not (is_private or is_group)):
        return
    filename = filename_early
    if filename:
        from whisperfast.telegram.links import consume_own_upload

        if consume_own_upload(chat_id, filename):
            return
    urls = []
    if not filename:
        from whisperfast.telegram.links import extract_video_urls

        urls = extract_video_urls(text)[:3]
    from whisperfast.telegram.learn import intake_allows

    if (filename or urls) and not intake_allows(
        settings, media=bool(filename), links=bool(urls) and not filename,
    ):
        return

    async def deliver(prompt_nums=None):
        await _deliver_learned(
            event,
            message,
            settings,
            submit,
            gui_running,
            log,
            chat,
            chat_id,
            filename,
            urls,
            prompt_nums=prompt_nums,
            is_private=is_private,
        )

    if learning and (
        chat_always_asks(chat_id, chat_names)
        or not chat_is_automatic(chat_id, chat_names, settings)
    ):
        if not filename and not urls:
            return
        material = material_label(filename or "", urls)
        from whisperfast.telegram.learn import batch_material, schedule_learn_batch, take_batch_snapshot

        loop = asyncio.get_running_loop()
        decision_future = loop.create_future()
        chat_name = chat_display_name(chat, chat_id)
        outgoing = bool(getattr(event, "out", False))

        def on_decision(decision, future=decision_future):
            if not future.done():
                loop.call_soon_threadsafe(future.set_result, decision)

        def replay_one():
            asyncio.run_coroutine_threadsafe(deliver(), loop)

        def flush(batch, chat_name=chat_name):
            labels = [item.get("material") or "" for item in take_batch_snapshot(batch)]
            pack_outgoing = all(item.get("outgoing") for item in take_batch_snapshot(batch))

            def settle(decision, batch=batch):
                for item in take_batch_snapshot(batch):
                    item["on_decision"](decision)

            def replay(batch=batch):
                for item in take_batch_snapshot(batch):
                    item["replay"]()

            if ask is None:
                key = "telegram_learn_question_own" if pack_outgoing else "telegram_learn_question"
                log(t(key, chat=chat_name, material=batch_material(labels)))
                settle("no")
                return
            ask(chat_name, batch_material(labels), settle, chat_id, pack_outgoing, replay)

        schedule_learn_batch(chat_id, {
            "material": material,
            "outgoing": outgoing,
            "on_decision": on_decision,
            "replay": replay_one,
        }, flush)
        if await decision_future not in ("once", "always"):
            return
    elif not accepted and not chat_is_automatic(chat_id, chat_names, settings):
        return
    await deliver()


async def _deliver_learned(
    event,
    message,
    settings,
    submit,
    gui_running,
    log,
    chat,
    chat_id,
    filename,
    urls,
    prompt_nums=None,
    is_private: bool = False,
):
    if not filename:
        if urls:
            task = asyncio.create_task(_take_video_links_guarded(
                event, urls, settings, submit, log, chat_id, int(message.id),
                is_private=is_private,
            ))
            _link_tasks.add(task)
            task.add_done_callback(_link_tasks.discard)
        return
    log(t("telegram_found", name=filename, chat=chat_display_name(chat, chat_id)))
    from whisperfast.telegram.links import log_downloaded_file
    from whisperfast.telegram.prompts_ask import ask_prompt_nums, should_ask_prompts
    from whisperfast.telegram.seen import add_target, classify, note_download, telethon_file_key

    file_key = telethon_file_key(message)
    action, known = classify(file_key)
    if action == "send":
        text = t("telegram_same_ai" if known.get("ai") else "telegram_same_done", name=filename)
        log(text)
        await event.reply(text)
        from whisperfast.telegram.outbox import enqueue_outgoing

        enqueue_outgoing(
            chat_id,
            int(message.id),
            text="",
            files=[{"path": item["path"], "caption": item["caption"]} for item in known.get("outputs") or []],
        )
        return
    if action == "wait":
        add_target(file_key, chat_id, int(message.id))
        text = t("telegram_same_queued", name=filename)
        log(text)
        await event.reply(text)
        return

    if not gui_running():
        await event.reply(t("telegram_gui_required"))
        return

    ask_task = None
    if prompt_nums is None and should_ask_prompts(settings, is_private=is_private):
        ask_task = asyncio.create_task(ask_prompt_nums(event, settings))

    if action == "enqueue":
        chosen = prompt_nums
        if ask_task is not None:
            chosen = await ask_task
        try:
            _call_submit(submit, known["path"], chat_id, int(message.id), prompt_nums=chosen)
        except Exception as exc:
            await event.reply(t("telegram_failed", error=str(exc)))
            return
        await event.reply(t("telegram_gui_added", name=os.path.basename(known["path"])))
        log(t("telegram_gui_added", name=os.path.basename(known["path"])))
        log_downloaded_file(log, known["path"])
        return

    await event.reply(t("telegram_queued", position=1))
    folder = os.path.join(resolve_work_dir(settings), f"{chat_id}_{int(message.id)}")
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, filename)
    download_task = asyncio.create_task(message.download_media(file=dest))
    downloaded = await download_task
    local = downloaded or dest
    if not local or not os.path.isfile(local):
        if ask_task is not None:
            ask_task.cancel()
        await event.reply(t("telegram_failed", error=filename))
        return
    chosen = prompt_nums
    if ask_task is not None:
        chosen = await ask_task
    try:
        _call_submit(submit, local, chat_id, int(message.id), prompt_nums=chosen)
    except Exception as exc:
        await event.reply(t("telegram_failed", error=str(exc)))
        return
    size = getattr(getattr(message, "file", None), "size", None)
    note_download(file_key, local, size if isinstance(size, int) else None)
    await event.reply(t("telegram_gui_added", name=os.path.basename(local)))
    log(t("telegram_gui_added", name=os.path.basename(local)))
    log_downloaded_file(log, local)


def run_account(
    settings: Mapping[str, Any],
    *,
    submit: Optional[Callable] = None,
    gui_running: Optional[Callable[[], bool]] = None,
    log: Optional[LogFunc] = None,
    stop: Optional[Callable[[], bool]] = None,
    ask=None,
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
                await _handle_message(client, event, settings, submit, gui_running, self_id, log, ask)

            def soft_log(msg, tag=None):
                if not log:
                    return
                try:
                    if tag is None:
                        log(msg)
                    else:
                        log(msg, tag)
                except Exception:
                    pass

            async def _pump():
                while not (stop and stop()):
                    try:
                        await _flush_outbox(client, soft_log)
                    except Exception as exc:
                        soft_log(str(exc))
                    await asyncio.sleep(0.35)
                await client.disconnect()

            pump = asyncio.create_task(_pump())
            own_names = normalize_chat_names(settings.get("telegram_self_chat_names"))
            own_list = ", ".join(own_names) if own_names else t("telegram_own_only_saved")
            started = t(
                "telegram_account_listening",
                name=(me.first_name or me.username or str(me.id)),
                chats=own_list,
            )
            soft_log(started)
            try:
                await client.send_message("me", started)
            except Exception as exc:
                soft_log(t("telegram_start_notice_failed", error=str(exc)))
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
        try:
            if log:
                log(str(exc))
        except Exception:
            pass
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
