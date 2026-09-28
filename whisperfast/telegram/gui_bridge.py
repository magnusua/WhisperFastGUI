"""Hand Telegram media to the running FTW window and send its outputs back."""
from __future__ import annotations

import os
import re
import threading
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from whisperfast.config import VIDEO_EXTENSIONS
from whisperfast.core.ipc_cmd import append_command
from whisperfast.i18n import t
from whisperfast.single_instance import find_other_instance_pid

_CLIP_AUDIO = re.compile(r"_\d{2}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_audio\.mp3$", re.IGNORECASE)
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
    """Підштовхнути Whisper, якщо він простоює. Не чекає кінця і не чіпає AI/соц-чергу."""
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
    from whisperfast.telegram.origin import remember

    remember(path, chat_id, message_id)
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
    if ctrl is not None and source:
        for item in ctrl.queue:
            if not _same_path(str(item.get("path") or ""), source):
                continue
            chat_id = item.get("telegram_chat_id")
            message_id = item.get("telegram_message_id")
            if chat_id is None or message_id is None:
                break
            try:
                return {"chat_id": int(chat_id), "message_id": int(message_id)}
            except (TypeError, ValueError):
                break
    from whisperfast.telegram.origin import lookup

    return lookup(source)


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


def _is_clip_audio(path: str) -> bool:
    return bool(_CLIP_AUDIO.search(os.path.basename(path)))


def _norm_path(path: str) -> str:
    try:
        return os.path.normcase(os.path.abspath(path))
    except OSError:
        return (path or "").strip()


def merge_disk_ai_outputs(
    source: str, outputs: Sequence[Mapping[str, Any]]
) -> List[Dict[str, Any]]:
    """Додає з диска `stem_*.md` біля TXT, якщо їх немає в outputs (AI міг не зареєструвати)."""
    merged: List[Dict[str, Any]] = []
    known: set[str] = set()
    for out in outputs or []:
        if not isinstance(out, Mapping):
            continue
        path = str(out.get("path") or "").strip()
        row = dict(out)
        if path:
            known.add(_norm_path(path))
        merged.append(row)

    txt = ""
    for out in merged:
        if str(out.get("role") or "") == "txt" and out.get("path"):
            txt = str(out["path"])
            break
    if not txt or not os.path.isfile(txt):
        base = os.path.splitext(os.path.basename(source or ""))[0]
        folder = os.path.dirname(os.path.abspath(source or "")) if source else ""
        if base and folder:
            candidate = os.path.join(folder, base + ".txt")
            if os.path.isfile(candidate):
                txt = candidate
                key = _norm_path(txt)
                if key not in known:
                    merged.append({"role": "txt", "path": txt})
                    known.add(key)
    if not txt or not os.path.isfile(txt):
        return merged

    stem = os.path.splitext(os.path.basename(txt))[0]
    folder = os.path.dirname(os.path.abspath(txt))
    prefix = stem + "_"
    try:
        names = os.listdir(folder)
    except OSError:
        return merged
    for name in sorted(names):
        low = name.lower()
        if not (low.endswith(".md") or low.endswith(".markdown")):
            continue
        if not name.startswith(prefix):
            continue
        path = os.path.join(folder, name)
        key = _norm_path(path)
        if key in known or not os.path.isfile(path):
            continue
        label = os.path.splitext(name[len(prefix) :])[0]
        merged.append({"role": "ai", "path": path, "label": label})
        known.add(key)
    return merged


def select_telegram_files(source: str, outputs: Sequence[Mapping[str, Any]]) -> List[Dict[str, str]]:
    """txt always; every AI file; mp3 of a video; a later clip's audio, with a clip caption.

    An mp3 made from an audio source is not sent. A clip cut from that audio is.
    """
    ext = os.path.splitext(source or "")[1].lower()
    video = ext in VIDEO_EXTENSIONS
    fresh: List[Dict[str, str]] = []
    for out in outputs:
        if not isinstance(out, Mapping):
            continue
        role = str(out.get("role") or "")
        path = str(out.get("path") or "")
        if not path or not os.path.isfile(path):
            continue
        if role == "txt":
            caption = t("telegram_caption_transcript")
        elif role == "ai":
            caption = t("telegram_caption_ai", name=os.path.basename(path))
        elif role == "mp3" and _is_clip_audio(path):
            caption = t("telegram_caption_clip")
        elif role == "mp3" and video:
            caption = t("telegram_caption_audio")
        else:
            continue
        fresh.append({"path": path, "caption": caption, "role": role})
    return fresh


def collect_unsent_telegram_files(
    app,
    source: str,
    outputs: Sequence[Mapping[str, Any]],
    *,
    include_ai: bool = True,
    mark: bool = False,
) -> List[Dict[str, str]]:
    """Файли для TG, яких ще немає в `_sent_set`. optionally mark as reserved."""
    merged = merge_disk_ai_outputs(source, outputs)
    candidates = select_telegram_files(source, merged)
    if not include_ai:
        candidates = [row for row in candidates if str(row.get("role") or "") != "ai"]
    fresh: List[Dict[str, str]] = []
    with _lock(app):
        sent = _sent_set(app)
        for item in candidates:
            key = _norm_path(item["path"])
            if key in sent:
                continue
            if mark:
                sent.add(key)
            fresh.append(item)
    return fresh


def mark_telegram_paths_sent(app, paths: Sequence[str]) -> None:
    with _lock(app):
        sent = _sent_set(app)
        for path in paths:
            if path:
                sent.add(_norm_path(path))


def maybe_deliver_telegram(
    app,
    file_id: Optional[str] = None,
    source_path: Optional[str] = None,
    sender: Optional[Callable] = None,
) -> None:
    """Надіслати ще не відправлені Whisper/AI файли. TXT іде одразу; AI — коли промпт не running."""
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
    ai_busy = _ai_busy_for_outputs(app, outputs)
    fresh = collect_unsent_telegram_files(
        app,
        source,
        outputs,
        include_ai=not ai_busy,
        mark=True,
    )
    error = str(entry.get("error") or "") if status == "failed" else ""
    error_key = f"err:{meta['chat_id']}:{meta['message_id']}:{error}"
    send_error = False
    with _lock(app):
        sent = _sent_set(app)
        send_error = bool(error) and error_key not in sent
        if send_error:
            sent.add(error_key)
    if not fresh and not send_error:
        return
    from whisperfast.telegram.seen import note_outputs, take_targets

    note_outputs(source, fresh)
    extras = take_targets(source)

    def _log_sent():
        try:
            if not fresh:
                return
            names = ", ".join(os.path.basename(item["path"]) for item in fresh)
            chat_ids = [str(int(meta["chat_id"]))]
            for extra in extras:
                try:
                    label = str(int(extra["chat_id"]))
                except (KeyError, TypeError, ValueError):
                    continue
                if label not in chat_ids:
                    chat_ids.append(label)
            chat = ", ".join(chat_ids)
            # Якщо в архіві вже є людська мітка «Кому» — покажемо її замість сирих id
            try:
                from whisperfast.library import get_library

                job = get_library().find_by_source(source) or {}
                stored = str(job.get("telegram_to") or "").strip()
                if stored:
                    chat = stored
            except Exception:
                pass
            app.log(t("telegram_sent_files", chat=chat, names=names))
        except Exception:
            pass

    try:
        app.root.after(0, _log_sent)
    except Exception:
        _log_sent()

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
        files = [{"path": item["path"], "caption": item["caption"]} for item in fresh]
        if fresh:
            sent_labels = [str(int(meta["chat_id"]))]
            for extra in extras:
                try:
                    sent_labels.append(str(int(extra["chat_id"])))
                except (KeyError, TypeError, ValueError):
                    continue

            def _mark_sent(source=source, labels=tuple(sent_labels)):
                ctrl = getattr(app, "queue_ctrl", None)
                if ctrl is not None:
                    ctrl.set_result(source, tg_sent=True)
                try:
                    from whisperfast.library import note_telegram_recipient

                    for label in labels or []:
                        note_telegram_recipient(source, label)
                except Exception:
                    pass
                refresh = getattr(app, "_refresh_archive_window", None)
                if callable(refresh):
                    try:
                        refresh()
                    except Exception:
                        pass

            try:
                app.root.after(0, _mark_sent)
            except Exception:
                _mark_sent()
        if normalize_mode(settings.get("telegram_mode")) == "account":
            enqueue_outgoing(
                meta["chat_id"],
                meta["message_id"],
                text=t("telegram_failed", error=error) if send_error else "",
                files=files,
            )
            for extra in extras:
                enqueue_outgoing(
                    int(extra["chat_id"]),
                    int(extra["message_id"]),
                    text="",
                    files=files,
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
        for extra in extras:
            for item in fresh:
                client.send_document(
                    int(extra["chat_id"]),
                    item["path"],
                    caption=item["caption"],
                    reply_to=int(extra["message_id"]),
                )

    threading.Thread(target=_send, daemon=True).start()
