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


@dataclass
class Decision:
    job: Optional[IncomingMedia] = None
    reply_chat_id: Optional[int] = None
    reply_text: Optional[str] = None
    log: Optional[str] = None


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
        return IncomingMedia(chat_id, message_id, str(obj["file_id"]), filename, kind)

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
        return IncomingMedia(chat_id, message_id, str(doc["file_id"]), filename, "document")
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
            local = materialize_media(job, client, dest_dir)
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


def accept_updates(
    updates: Sequence[Mapping[str, Any]],
    allowlist: Sequence[int],
    client: TelegramClient,
    pending: List[IncomingMedia],
    log: Optional[LogFunc] = None,
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
            log(decision.log)
        if decision.reply_text and decision.reply_chat_id is not None:
            client.send_message(decision.reply_chat_id, decision.reply_text)
        if decision.job:
            pending.append(decision.job)
            client.send_message(
                decision.job.chat_id,
                t("telegram_queued", position=len(pending)),
                reply_to=decision.job.message_id,
            )
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

    allowlist = normalize_chat_ids(settings.get("telegram_allowed_chat_ids"))
    offset = None
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        cycles += 1
        updates = client.get_updates(offset=offset, timeout=50)
        pending: List[IncomingMedia] = []
        new_offset = accept_updates(updates, allowlist, client, pending, log=log)
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


def run_from_settings() -> int:
    from whisperfast.telegram.account import normalize_mode, run_account

    settings = load_app_settings()
    try:
        if normalize_mode(settings.get("telegram_mode")) == "account":
            return run_account(settings)
        return run_bot(settings)
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
