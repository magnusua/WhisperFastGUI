"""Long-polling worker: allowlisted chats, one media file at a time."""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable, List, Mapping, Optional, Sequence
from urllib.parse import urlparse

from whisperfast.config import AUDIO_EXTENSIONS, BASE_DIR, VIDEO_EXTENSIONS
from whisperfast.i18n import t
from whisperfast.platform_util import win_no_window_kwargs
from whisperfast.settings import load_app_settings, normalize_chat_ids
from whisperfast.telegram.client import TelegramClient

MEDIA_FIELDS = ("voice", "audio", "video", "video_note")
_MEDIA_EXTENSIONS = AUDIO_EXTENSIONS + VIDEO_EXTENSIONS
_DEFAULT_EXT = {
    "voice": ".ogg",
    "audio": ".mp3",
    "video": ".mp4",
    "video_note": ".mp4",
    "document": ".bin",
}
_MIME_EXT = {
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/flac": ".flac",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "video/x-matroska": ".mkv",
}

LogFunc = Callable[[str], None]
BOT_API_RELEASES = "https://github.com/tdlib/telegram-bot-api"


@dataclass
class IncomingMedia:
    chat_id: int
    message_id: int
    file_id: str
    filename: str
    kind: str
    unique_id: str = ""
    local_path: str = ""


@dataclass
class Decision:
    job: Optional[IncomingMedia] = None
    reply_chat_id: Optional[int] = None
    reply_text: Optional[str] = None
    log: Optional[str] = None


def chat_display_name(chat, chat_id) -> str:
    """Title, person name, or @username for a log line. Falls back to the chat id."""

    def pick(key: str) -> str:
        if isinstance(chat, Mapping):
            return str(chat.get(key) or "").strip()
        return str(getattr(chat, key, "") or "").strip()

    title = pick("title")
    if title:
        return title
    full = f"{pick('first_name')} {pick('last_name')}".strip()
    if full:
        return full
    username = pick("username").lstrip("@")
    if username:
        return "@" + username
    return str(chat_id)


def safe_filename(name: str, fallback: str) -> str:
    base = os.path.basename((name or "").replace("\\", "/")).strip()
    if not base or base in (".", ".."):
        base = fallback
    cleaned = "".join("_" if ch in '<>:"|?*' else ch for ch in base)
    cleaned = cleaned.strip(" .")
    return (cleaned or fallback)[:180]


def _extension_ok(filename: str, mime: str) -> bool:
    ext = os.path.splitext(filename)[1].lower()
    if ext in _MEDIA_EXTENSIONS:
        return True
    mime_l = (mime or "").lower()
    return mime_l.startswith("audio/") or mime_l.startswith("video/")


def is_start_command(text: str) -> bool:
    token = (text or "").strip().split()[:1]
    if not token:
        return False
    head = token[0].split("@", 1)[0]
    return head == "/start"


def media_from_message(message: Mapping[str, Any]) -> Optional[IncomingMedia]:
    if not isinstance(message, Mapping):
        return None
    if isinstance(message.get("sticker"), Mapping):
        return None
    chat = message.get("chat") or {}
    if not isinstance(chat, Mapping) or chat.get("id") is None:
        return None
    try:
        chat_id = int(chat["id"])
        message_id = int(message.get("message_id"))
    except (TypeError, ValueError):
        return None

    for kind in MEDIA_FIELDS:
        obj = message.get(kind)
        if not isinstance(obj, Mapping) or not obj.get("file_id"):
            continue
        original = str(obj.get("file_name") or "")
        filename = safe_filename(original, f"{kind}{_DEFAULT_EXT[kind]}")
        if not os.path.splitext(filename)[1]:
            filename += _DEFAULT_EXT[kind]
        from whisperfast.telegram.seen import bot_file_key

        return IncomingMedia(
            chat_id, message_id, str(obj["file_id"]), filename, kind,
            unique_id=bot_file_key(obj) or "",
        )

    doc = message.get("document")
    if isinstance(doc, Mapping) and doc.get("file_id"):
        original = str(doc.get("file_name") or "")
        mime = str(doc.get("mime_type") or "")
        if not _extension_ok(original, mime):
            return None
        filename = safe_filename(original, "document" + _DEFAULT_EXT["document"])
        if not os.path.splitext(filename)[1]:
            mime_l = mime.lower()
            ext = _MIME_EXT.get(mime_l)
            if not ext and mime_l.startswith("audio/"):
                ext = ".mp3"
            elif not ext and mime_l.startswith("video/"):
                ext = ".mp4"
            filename += ext or _DEFAULT_EXT["document"]
        from whisperfast.telegram.seen import bot_file_key

        return IncomingMedia(
            chat_id, message_id, str(doc["file_id"]), filename, "document",
            unique_id=bot_file_key(doc) or "",
        )
    return None


def decide_update(update: Mapping[str, Any], allowlist: Sequence[int]) -> Decision:
    """Accept media from allowlisted chats. Empty allowlist only answers /start."""
    message = update.get("message") if isinstance(update, Mapping) else None
    if not isinstance(message, Mapping):
        return Decision()
    chat = message.get("chat") or {}
    if not isinstance(chat, Mapping) or chat.get("id") is None:
        return Decision()
    try:
        chat_id = int(chat["id"])
    except (TypeError, ValueError):
        return Decision()

    allowed = set(int(item) for item in allowlist)
    text = message.get("text") if isinstance(message.get("text"), str) else ""
    if not allowed:
        if is_start_command(text):
            return Decision(
                reply_chat_id=chat_id,
                reply_text=t("telegram_start_id", chat_id=chat_id),
            )
        return Decision(log=t("telegram_ignored_chat", chat_id=chat_id))

    if chat_id not in allowed:
        return Decision(log=t("telegram_ignored_chat", chat_id=chat_id))

    if is_start_command(text):
        return Decision(
            reply_chat_id=chat_id,
            reply_text=t("telegram_start_id", chat_id=chat_id),
        )
    job = media_from_message(message)
    if job:
        return Decision(job=job)
    return Decision()


def resolve_work_dir(settings: Mapping[str, Any]) -> str:
    raw = str(settings.get("telegram_work_dir") or "").strip()
    path = os.path.abspath(raw) if raw else os.path.join(BASE_DIR, "telegram_inbox")
    os.makedirs(path, exist_ok=True)
    return path


def api_endpoint(settings: Mapping[str, Any]):
    base = str(settings.get("telegram_api_base") or "http://127.0.0.1:8081").rstrip("/")
    parsed = urlparse(base)
    host = parsed.hostname or "127.0.0.1"
    if parsed.port:
        port = parsed.port
    else:
        port = 443 if parsed.scheme == "https" else 80
    return base, host, port


def port_is_open(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def ensure_local_api(
    settings: Mapping[str, Any],
    log: Optional[LogFunc] = None,
    popen: Callable = subprocess.Popen,
    wait_s: float = 30,
) -> None:
    """Start telegram-bot-api --local when the configured port is closed."""
    log = log or print
    base, host, port = api_endpoint(settings)
    if port_is_open(host, port):
        log(t("telegram_server_started", base=base))
        return

    exe = str(settings.get("telegram_bot_api_exe") or "").strip()
    if not exe:
        raise SystemExit(t("telegram_no_server"))
    if not os.path.isfile(exe):
        raise SystemExit(t("telegram_server_missing_exe", path=exe) + " " + BOT_API_RELEASES)

    api_id = str(settings.get("telegram_api_id") or "").strip()
    api_hash = str(settings.get("telegram_api_hash") or "").strip()
    if not api_id or not api_hash:
        raise SystemExit(t("telegram_no_api_credentials"))

    server_dir = os.path.join(resolve_work_dir(settings), "bot-api")
    os.makedirs(server_dir, exist_ok=True)
    cmd = [
        exe,
        f"--api-id={api_id}",
        f"--api-hash={api_hash}",
        "--local",
        f"--http-ip-address={host}",
        f"--http-port={port}",
        f"--dir={server_dir}",
    ]
    log_path = os.path.join(server_dir, "telegram-bot-api.log")
    log_handle = open(log_path, "a", encoding="utf-8")
    try:
        popen(
            cmd,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            **win_no_window_kwargs(),
        )
    finally:
        log_handle.close()
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if port_is_open(host, port):
            log(t("telegram_server_started", base=base))
            return
        time.sleep(0.3)
    raise SystemExit(t("telegram_server_timeout", host=host, port=port))


def materialize_media(job: IncomingMedia, client: TelegramClient, dest_dir: str) -> str:
    src = client.get_file_path(job.file_id)
    if not src or not os.path.isfile(src):
        raise FileNotFoundError(src or job.file_id)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, job.filename)
    if os.path.normcase(os.path.abspath(src)) != os.path.normcase(os.path.abspath(dest)):
        shutil.copy2(src, dest)
    return dest


def process_pending(
    jobs: Sequence[IncomingMedia],
    *,
    client: TelegramClient,
    settings: Mapping[str, Any],
    submit: Optional[Callable] = None,
    gui_running: Optional[Callable[[], bool]] = None,
    work_dir: Optional[str] = None,
) -> None:
    """Download each file and hand it to the running FTW queue, one at a time."""
    from whisperfast.telegram.gui_bridge import gui_is_running, submit_to_running_gui

    if submit is None:
        submit = submit_to_running_gui
    if gui_running is None:
        gui_running = gui_is_running
    root = work_dir or resolve_work_dir(settings)
    for job in jobs:
        if not gui_running():
            client.send_message(
                job.chat_id,
                t("telegram_gui_required"),
                reply_to=job.message_id,
            )
            continue
        dest_dir = os.path.join(root, f"{job.chat_id}_{job.message_id}")
        try:
            if job.local_path and os.path.isfile(job.local_path):
                local = job.local_path
            else:
                local = materialize_media(job, client, dest_dir)
            from whisperfast.telegram.seen import note_download

            note_download(job.unique_id, local)
            submit(local, job.chat_id, job.message_id)
        except Exception as exc:
            client.send_message(
                job.chat_id,
                t("telegram_failed", error=str(exc)),
                reply_to=job.message_id,
            )
            continue
        client.send_message(
            job.chat_id,
            t("telegram_gui_added", name=job.filename),
            reply_to=job.message_id,
        )


def _reuse_known_file(job: IncomingMedia, client: TelegramClient, log: LogFunc) -> bool:
    """True when this file was already downloaded, so it is not fetched again."""
    from whisperfast.telegram.seen import add_target, classify

    action, known = classify(job.unique_id)
    if action == "download":
        return False
    name = job.filename
    if action == "send":
        text = t("telegram_same_ai" if known.get("ai") else "telegram_same_done", name=name)
        log(text)
        client.send_message(job.chat_id, text, reply_to=job.message_id)
        for item in known.get("outputs") or []:
            client.send_document(
                job.chat_id,
                item["path"],
                caption=str(item.get("caption") or ""),
                reply_to=job.message_id,
            )
        return True
    if action == "wait":
        add_target(job.unique_id, job.chat_id, job.message_id)
        text = t("telegram_same_queued", name=name)
        log(text)
        client.send_message(job.chat_id, text, reply_to=job.message_id)
        return True
    job.local_path = str(known.get("path") or "")
    return False


def _ingest_bot_links(message, settings, client, submit, log: LogFunc) -> None:
    from whisperfast.telegram.links import claim_link, extract_video_urls, remember_link, remember_wait
    from whisperfast.telegram.seen import classify

    text = message.get("text") if isinstance(message.get("text"), str) else ""
    urls = extract_video_urls(text)[:3]
    if not urls:
        return
    try:
        chat_id = int((message.get("chat") or {}).get("id"))
        message_id = int(message.get("message_id"))
    except (TypeError, ValueError):
        return
    work = resolve_work_dir(settings)
    for url in urls:
        dest = os.path.join(work, f"{chat_id}_{message_id}")
        remember_link(url)
        if classify("url:" + url)[0] == "download":
            notice = t("telegram_link_downloading", url=url)
            log(notice)
            client.send_message(chat_id, notice, reply_to=message_id)
        else:
            log(url)
        try:
            action, path, known = claim_link(url, dest)
            remember_link(url, path or str((known or {}).get("path") or ""))
        except Exception as exc:
            err = t("telegram_link_failed", error=str(exc))
            log(err)
            client.send_message(chat_id, err, reply_to=message_id)
            continue
        if action == "send":
            text_out = t(
                "telegram_same_ai" if known.get("ai") else "telegram_same_done",
                name=os.path.basename(url),
            )
            log(text_out)
            client.send_message(chat_id, text_out, reply_to=message_id)
            for item in (known.get("outputs") or []):
                client.send_document(
                    chat_id, item["path"], caption=str(item.get("caption") or ""), reply_to=message_id,
                )
            continue
        if action == "wait":
            remember_wait(url, chat_id, message_id)
            text_out = t("telegram_same_queued", name=os.path.basename(url))
            log(text_out)
            client.send_message(chat_id, text_out, reply_to=message_id)
            continue
        from whisperfast.settings import load_app_settings
        from whisperfast.telegram.links import (
            log_downloaded_file,
            mark_own_upload,
            social_videos_go_to_queue,
        )

        if not social_videos_go_to_queue(load_app_settings()):
            mark_own_upload(chat_id, path)
            notice = t("telegram_link_saved")
            log(notice)
            log_downloaded_file(log, path)
            client.send_document(chat_id, path, caption=notice, reply_to=message_id)
            continue
        if submit is None:
            from whisperfast.telegram.gui_bridge import submit_to_running_gui
            submit = submit_to_running_gui
        try:
            submit(path, chat_id, message_id)
        except Exception as exc:
            client.send_message(chat_id, t("telegram_failed", error=str(exc)), reply_to=message_id)
            continue
        client.send_message(
            chat_id, t("telegram_gui_added", name=os.path.basename(path)), reply_to=message_id,
        )
        log(t("telegram_gui_added", name=os.path.basename(path)))
        log_downloaded_file(log, path)


def _defer_learn(message, log: LogFunc, ask: Optional[Callable]) -> bool:
    """Ask about media from a chat that is not automatic. True when this update is held."""
    from whisperfast.settings import load_app_settings
    from whisperfast.telegram.learn import (
        chat_always_asks,
        chat_is_automatic,
        chat_is_ignored,
        learn_enabled,
        material_label,
        push_ready,
    )
    from whisperfast.telegram.links import extract_video_urls

    settings = load_app_settings()
    chat = message.get("chat") if isinstance(message.get("chat"), Mapping) else {}
    try:
        chat_id = int(chat.get("id"))
    except (TypeError, ValueError):
        return False
    names = [chat_display_name(chat, chat_id)]
    if chat_is_ignored(chat_id, names, settings):
        return True
    if not learn_enabled(settings):
        return False
    if chat_is_automatic(chat_id, names, settings) and not chat_always_asks(chat_id, names):
        return False
    job = media_from_message(message)
    text = message.get("text") if isinstance(message.get("text"), str) else ""
    urls = [] if job else extract_video_urls(text)[:3]
    if not job and not urls:
        return False
    material = material_label(job.filename if job else "", urls)
    chat_name = chat_display_name(chat, chat_id)
    kind = "media" if job else "links"
    payload = job if job else message

    def settle(decision, kind=kind, payload=payload):
        push_ready((decision, kind, payload))

    if ask is None:
        log(t("telegram_learn_question", chat=chat_name, material=material))
        settle("no")
        return True
    ask(chat_name, material, settle, chat_id)
    return True


def accept_updates(
    updates: Sequence[Mapping[str, Any]],
    allowlist: Sequence[int],
    client: TelegramClient,
    pending: List[IncomingMedia],
    log: Optional[LogFunc] = None,
    settings: Optional[Mapping[str, Any]] = None,
    submit: Optional[Callable] = None,
    ask: Optional[Callable] = None,
) -> Optional[int]:
    """Enqueue accepted media. Returns the next getUpdates offset, if any."""
    log = log or (lambda _msg: None)
    offset = None
    for update in updates:
        if isinstance(update, Mapping) and update.get("update_id") is not None:
            try:
                offset = int(update["update_id"]) + 1
            except (TypeError, ValueError):
                pass
        decision = decide_update(update, allowlist)
        if decision.log:
            message = update.get("message") if isinstance(update, Mapping) else None
            if isinstance(message, Mapping) and _defer_learn(message, log, ask):
                continue
            log(decision.log)
        if decision.reply_text and decision.reply_chat_id is not None:
            client.send_message(decision.reply_chat_id, decision.reply_text)
        if decision.job:
            message = update.get("message") if isinstance(update, Mapping) else None
            if isinstance(message, Mapping) and _defer_learn(message, log, ask):
                continue
            chat = message.get("chat") if isinstance(message, Mapping) else None
            log(t(
                "telegram_found",
                name=decision.job.filename,
                chat=chat_display_name(chat, decision.job.chat_id),
            ))
            if _reuse_known_file(decision.job, client, log):
                continue
            pending.append(decision.job)
            client.send_message(
                decision.job.chat_id,
                t("telegram_queued", position=len(pending)),
                reply_to=decision.job.message_id,
            )
            continue
        if settings is not None and not decision.log and not decision.reply_text:
            message = update.get("message") if isinstance(update, Mapping) else None
            if isinstance(message, Mapping):
                if _defer_learn(message, log, ask):
                    continue
                _ingest_bot_links(message, settings, client, submit, log)
    return offset


def run_bot(
    settings: Mapping[str, Any],
    *,
    client: Optional[TelegramClient] = None,
    submit: Optional[Callable] = None,
    gui_running: Optional[Callable[[], bool]] = None,
    ensure_api: Callable = ensure_local_api,
    log: Optional[LogFunc] = None,
    max_cycles: Optional[int] = None,
    poll_timeout: int = 50,
    stop: Optional[Callable[[], bool]] = None,
    ask: Optional[Callable] = None,
) -> int:
    log = log or print
    token = str(settings.get("telegram_bot_token") or "").strip()
    if not token:
        log(t("telegram_no_token"))
        return 1
    ensure_api(settings, log)
    if client is None:
        base, _host, _port = api_endpoint(settings)
        client = TelegramClient(token, api_base=base)

    offset = None
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        if stop and stop():
            break
        cycles += 1
        fresh = load_app_settings()
        from whisperfast.telegram.learn import load_learn_chats

        allowlist = normalize_chat_ids(fresh.get("telegram_allowed_chat_ids"))
        allowlist = list(allowlist) + [
            int(row["id"]) for row in load_learn_chats().get("always") or [] if row.get("id")
        ]
        updates = client.get_updates(offset=offset, timeout=poll_timeout)
        pending: List[IncomingMedia] = []
        new_offset = accept_updates(
            updates, allowlist, client, pending, log=log, settings=fresh, submit=submit, ask=ask,
        )
        from whisperfast.telegram.learn import take_ready

        for decision, kind, payload in take_ready():
            if decision not in ("once", "always"):
                continue
            if kind == "media":
                pending.append(payload)
            elif kind == "links":
                _ingest_bot_links(payload, settings, client, submit, log)
        if new_offset is not None:
            offset = new_offset
        if pending:
            process_pending(
                pending,
                client=client,
                settings=settings,
                submit=submit,
                gui_running=gui_running,
            )
    return 0


def run_from_settings(
    *,
    submit: Optional[Callable] = None,
    gui_running: Optional[Callable[[], bool]] = None,
    stop: Optional[Callable[[], bool]] = None,
    log: Optional[LogFunc] = None,
    poll_timeout: int = 50,
    ask: Optional[Callable] = None,
) -> int:
    from whisperfast.telegram.account import normalize_mode, run_account

    settings = load_app_settings()
    try:
        if normalize_mode(settings.get("telegram_mode")) == "account":
            return run_account(
                settings, submit=submit, gui_running=gui_running, stop=stop, log=log, ask=ask,
            )
        return run_bot(
            settings,
            submit=submit,
            gui_running=gui_running,
            stop=stop,
            log=log,
            poll_timeout=poll_timeout,
            ask=ask,
        )
    except KeyboardInterrupt:
        print("stopped")
        return 0
    except SystemExit as exc:
        code = exc.code
        if isinstance(code, int):
            return code
        if code:
            print(code)
        return 1
