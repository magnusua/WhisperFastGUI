"""Окрема черга скачування та постобробки соцмереж (yt-dlp → чат / Whisper).

Незалежна від:
- черги транскрибації (`app._process_queue_lock`)
- черги AI-промптів (`AiJobQueue`)

Whisper отримує файл лише через fire-and-forget submit; соц-воркер
ніколи не чекає кінця транскрибації чи Cursor.
"""
from __future__ import annotations

import os
import queue
import threading
import time
from typing import Any, Callable, List, Mapping, Optional, Sequence

from whisperfast.i18n import t

LogFunc = Callable[..., None]
ReplyFn = Callable[[str], None]
SendFilesFn = Callable[[List[dict]], None]
SubmitWhisperFn = Callable[[str, int, int], None]
RetryHook = Callable[[Callable[[], None]], None]

_SOCIAL_WORKER_NAME = "ftw-social-queue"


class SocialJobQueue:
    """Серійний воркер: одне соц-посилання/пачка за раз, поза Whisper/AI."""

    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue()
        self._started = False
        self._lock = threading.Lock()
        self._active = 0

    def submit(self, work: Callable[[], None]) -> None:
        """Поставити роботу в соц-чергу (неблокуюче для викликача)."""
        if getattr(self, "_social_inline", False):
            work()
            return
        self._ensure_worker()
        self._q.put(work)

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            threading.Thread(
                target=self._loop,
                name=_SOCIAL_WORKER_NAME,
                daemon=True,
            ).start()

    def _loop(self) -> None:
        while True:
            work = self._q.get()
            if work is None:
                self._q.task_done()
                break
            try:
                with self._lock:
                    self._active += 1
                work()
            except Exception:
                pass
            finally:
                with self._lock:
                    self._active = max(0, self._active - 1)
                self._q.task_done()

    def wait_idle(self, timeout: float = 5.0) -> bool:
        if getattr(self, "_social_inline", False):
            return True
        self._ensure_worker()
        deadline = time.monotonic() + max(0.05, float(timeout))
        while time.monotonic() < deadline:
            if self._q.unfinished_tasks == 0:
                with self._lock:
                    if self._active == 0:
                        return True
            time.sleep(0.02)
        return False


_queue: Optional[SocialJobQueue] = None
_queue_lock = threading.Lock()


def get_social_queue() -> SocialJobQueue:
    global _queue
    with _queue_lock:
        if _queue is None:
            _queue = SocialJobQueue()
        return _queue


def reset_social_queue_for_tests() -> SocialJobQueue:
    """Новий інстанс із inline-режимом для юніт-тестів."""
    global _queue
    with _queue_lock:
        q = SocialJobQueue()
        q._social_inline = True  # type: ignore[attr-defined]
        _queue = q
        return q


def process_social_urls(
    urls: Sequence[str],
    *,
    chat_id: int,
    message_id: int,
    settings: Mapping[str, Any],
    log: LogFunc,
    reply: ReplyFn,
    send_files: SendFilesFn,
    submit_whisper: Optional[SubmitWhisperFn] = None,
    on_error_retry: Optional[RetryHook] = None,
) -> None:
    """Скачати URL і постобробити: назад у чат і/або в чергу Whisper (без очікування)."""
    from whisperfast.settings import load_app_settings
    from whisperfast.telegram.links import (
        claim_link,
        log_downloaded_file,
        mark_own_upload,
        remember_link,
        remember_wait,
        report_link_failure,
        social_videos_go_to_queue,
    )
    from whisperfast.telegram.seen import classify
    from whisperfast.telegram.worker import resolve_work_dir

    work_dir = resolve_work_dir(settings)
    for url in list(urls)[:3]:
        dest = os.path.join(work_dir, f"{chat_id}_{message_id}")
        remember_link(url)
        try:
            log(url)
        except TypeError:
            log(str(url))
        if classify("url:" + url)[0] == "download":
            reply(t("telegram_link_downloading"))
        try:
            action, path, known = claim_link(url, dest)
            remember_link(url, path or str((known or {}).get("path") or ""))
        except Exception as exc:
            reply(t("telegram_link_failed", error=str(exc)))

            def retry(one=url):
                process_social_urls(
                    [one],
                    chat_id=chat_id,
                    message_id=message_id,
                    settings=settings,
                    log=log,
                    reply=reply,
                    send_files=send_files,
                    submit_whisper=submit_whisper,
                    on_error_retry=on_error_retry,
                )

            if on_error_retry is not None:
                on_error_retry(retry)
            else:
                report_link_failure(log, exc, retry)
            continue

        if action == "send":
            text = t(
                "telegram_same_ai" if (known or {}).get("ai") else "telegram_same_done",
                name=os.path.basename(url),
            )
            log(text)
            reply(text)
            send_files(
                [
                    {"path": item["path"], "caption": str(item.get("caption") or "")}
                    for item in ((known or {}).get("outputs") or [])
                    if isinstance(item, Mapping) and item.get("path")
                ]
            )
            continue

        if action == "wait":
            remember_wait(url, chat_id, message_id)
            text = t("telegram_same_queued", name=os.path.basename(url))
            log(text)
            reply(text)
            continue

        # action new / enqueue — свіже або вже скачане відео
        if not social_videos_go_to_queue(load_app_settings()):
            mark_own_upload(chat_id, path)
            send_files([{"path": path, "caption": ""}])
            log_downloaded_file(log, path)
            continue

        if submit_whisper is None:
            reply(t("telegram_gui_required"))
            continue
        try:
            # Fire-and-forget у Whisper; соц-черга тут завершує свою роботу.
            submit_whisper(path, chat_id, message_id)
        except Exception as exc:
            reply(t("telegram_failed", error=str(exc)))
            continue
        reply(t("telegram_gui_added", name=os.path.basename(path)))
        log(t("telegram_gui_added", name=os.path.basename(path)))
        log_downloaded_file(log, path)


def enqueue_social_urls(
    urls: Sequence[str],
    *,
    chat_id: int,
    message_id: int,
    settings: Mapping[str, Any],
    log: LogFunc,
    reply: ReplyFn,
    send_files: SendFilesFn,
    submit_whisper: Optional[SubmitWhisperFn] = None,
    on_error_retry: Optional[RetryHook] = None,
) -> None:
    """Поставити обробку URL у соц-чергу (не Whisper, не AI)."""

    def work():
        try:
            process_social_urls(
                urls,
                chat_id=chat_id,
                message_id=message_id,
                settings=settings,
                log=log,
                reply=reply,
                send_files=send_files,
                submit_whisper=submit_whisper,
                on_error_retry=on_error_retry,
            )
        except Exception as exc:
            try:
                log(t("telegram_link_failed", error=str(exc)))
            except Exception:
                pass
            try:
                reply(t("telegram_link_failed", error=str(exc)))
            except Exception:
                pass

    get_social_queue().submit(work)
