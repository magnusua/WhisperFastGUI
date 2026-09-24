"""Hand Telegram media to the running FTW window and send its outputs back."""
from __future__ import annotations

import os
import threading
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from whisperfast.core.ipc_cmd import append_command
from whisperfast.i18n import t
from whisperfast.single_instance import find_other_instance_pid

_DELIVER_ROLES = {"txt", "md", "ai", "docx"}
_AI_BUSY = {"pending", "selecting", "running"}


def gui_is_running() -> bool:
    return find_other_instance_pid() is not None


def enqueue_telegram_file(path: str, chat_id: int, message_id: int) -> None:
    """Queue a file for this process. Used when the listener runs inside the open window."""
    append_command(
        "telegram_file",
        path=os.path.abspath(path),
        chat_id=int(chat_id),
        message_id=int(message_id),
    )


def submit_to_running_gui(path: str, chat_id: int, message_id: int) -> None:
    """Ask the open FTW window to queue this file. Raises if the window is not running."""
    if not gui_is_running():
        raise RuntimeError(t("telegram_gui_required"))
    enqueue_telegram_file(path, chat_id, message_id)


def _same_path(a: str, b: str) -> bool:
    try:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
    except OSError:
        return False


def _stamp(queue_ctrl, path: str, chat_id: int, message_id: int) -> None:
    for item in queue_ctrl.queue:
        if _same_path(str(item.get("path") or ""), path):
            item["telegram_chat_id"] = int(chat_id)
            item["telegram_message_id"] = int(message_id)
    save = getattr(queue_ctrl, "save_to_file", None)
    if callable(save):
        save()


def kick_gui_queue(app) -> None:
    """Start unprocessed rows, or leave a flag so the current run continues into them."""
    ctrl = getattr(app, "queue_ctrl", None)
    if ctrl is None:
        return
    busy = False
    is_processing = getattr(ctrl, "_is_processing", None)
    if callable(is_processing):
        try:
            busy = bool(is_processing())
        except Exception:
            busy = False
    if busy:
        ctrl.watch_pending_continue = True
        return
    start = getattr(ctrl, "_start_processing", None)
    if callable(start):
        start(mode="only_new", from_watch=True)


def apply_telegram_command(app, cmd: Mapping[str, Any]) -> None:
    path = str(cmd.get("path") or "")
    if not path or not os.path.isfile(path):
        return
    try:
        chat_id = int(cmd.get("chat_id"))
        message_id = int(cmd.get("message_id"))
    except (TypeError, ValueError):
        return
    ctrl = app.queue_ctrl
    ctrl.add_files([path])
    _stamp(ctrl, path, chat_id, message_id)
    kick_gui_queue(app)


def _sent_set(app) -> set:
    sent = getattr(app, "_telegram_sent_paths", None)
    if sent is None:
        sent = set()
        app._telegram_sent_paths = sent
    return sent


def _lock(app) -> threading.Lock:
    lock = getattr(app, "_telegram_deliver_lock", None)
    if lock is None:
        lock = threading.Lock()
        app._telegram_deliver_lock = lock
    return lock


def _meta_for_source(app, source: str) -> Optional[Dict[str, int]]:
    ctrl = getattr(app, "queue_ctrl", None)
    if ctrl is None or not source:
        return None
    for item in ctrl.queue:
        if not _same_path(str(item.get("path") or ""), source):
            continue
        chat_id = item.get("telegram_chat_id")
        message_id = item.get("telegram_message_id")
        if chat_id is None or message_id is None:
            return None
        try:
            return {"chat_id": int(chat_id), "message_id": int(message_id)}
        except (TypeError, ValueError):
            return None
    return None


def _ai_busy_for_outputs(app, outputs: Sequence[Mapping[str, Any]]) -> bool:
    jobs = getattr(getattr(app, "ai_jobs", None), "_jobs", None) or {}
    watched = set()
    for out in outputs:
        role = str(out.get("role") or "")
        path = str(out.get("path") or "")
        if role in ("txt", "md") and path:
            watched.add(os.path.normcase(os.path.abspath(path)))
    if not watched:
        return False
    for job in jobs.values():
        if not isinstance(job, Mapping):
            continue
        if str(job.get("status") or "") not in _AI_BUSY:
            continue
        txt = str(job.get("txt_path") or "")
        if txt and os.path.normcase(os.path.abspath(txt)) in watched:
            return True
    return False


def _file_entry(app, file_id: Optional[str] = None, source_path: Optional[str] = None):
    store = getattr(getattr(app, "log_panel", None), "_store", None)
    if store is None:
        return None
    if file_id:
        return store.get_file(file_id)
    if source_path and hasattr(store, "find_file_by_source"):
        return store.find_file_by_source(source_path)
    return None


def _caption(role: str, path: str) -> str:
    if role in ("ai", "md", "docx"):
        return t("telegram_caption_ai", name=os.path.basename(path))
    return t("telegram_caption_transcript")


def maybe_deliver_telegram(
    app,
    file_id: Optional[str] = None,
    source_path: Optional[str] = None,
    sender: Optional[Callable] = None,
) -> None:
    """Send new Whisper/AI files for a Telegram-originated job. Waits while AI is still running."""
    entry = _file_entry(app, file_id=file_id, source_path=source_path)
    if not isinstance(entry, Mapping):
        return
    status = str(entry.get("status") or "")
    if status not in ("done", "failed", "skipped"):
        return
    source = str(entry.get("source") or source_path or "")
    meta = _meta_for_source(app, source)
    if not meta:
        return
    outputs = [o for o in (entry.get("outputs") or []) if isinstance(o, Mapping)]
    if _ai_busy_for_outputs(app, outputs):
        return

    fresh: List[Dict[str, str]] = []
    with _lock(app):
        sent = _sent_set(app)
        for out in outputs:
            role = str(out.get("role") or "")
            path = str(out.get("path") or "")
            if role not in _DELIVER_ROLES or not path or not os.path.isfile(path):
                continue
            key = os.path.normcase(os.path.abspath(path))
            if key in sent:
                continue
            sent.add(key)
            fresh.append({"path": path, "caption": _caption(role, path)})
        error = str(entry.get("error") or "") if status == "failed" else ""
        error_key = f"err:{meta['chat_id']}:{meta['message_id']}:{error}"
        send_error = bool(error) and error_key not in sent
        if send_error:
            sent.add(error_key)
    if not fresh and not send_error:
        return
    if sender is not None:
        sender(meta, fresh, error if send_error else "")
        return

    def _send():
        from whisperfast.settings import load_app_settings
        from whisperfast.telegram.account import normalize_mode
        from whisperfast.telegram.client import TelegramClient
        from whisperfast.telegram.outbox import enqueue_outgoing
        from whisperfast.telegram.worker import api_endpoint

        settings = load_app_settings()
        if normalize_mode(settings.get("telegram_mode")) == "account":
            enqueue_outgoing(
                meta["chat_id"],
                meta["message_id"],
                text=t("telegram_failed", error=error) if send_error else "",
                files=[{"path": item["path"], "caption": item["caption"]} for item in fresh],
            )
            return
        token = str(settings.get("telegram_bot_token") or "").strip()
        if not token:
            return
        base, _host, _port = api_endpoint(settings)
        client = TelegramClient(token, api_base=base)
        if send_error:
            client.send_message(
                meta["chat_id"],
                t("telegram_failed", error=error),
                reply_to=meta["message_id"],
            )
        for item in fresh:
            client.send_document(
                meta["chat_id"],
                item["path"],
                caption=item["caption"],
                reply_to=meta["message_id"],
            )

    threading.Thread(target=_send, daemon=True).start()
