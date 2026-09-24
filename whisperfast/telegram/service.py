"""Start and stop the Telegram listener inside the open FTW window."""
from __future__ import annotations

import threading
from typing import Any, Callable, Optional

_lock = threading.Lock()
_stop = threading.Event()
_thread: Optional[threading.Thread] = None


def is_running() -> bool:
    thread = _thread
    return thread is not None and thread.is_alive()


def is_stopping() -> bool:
    return _stop.is_set() and is_running()


def start(log: Optional[Callable[[str], None]] = None, on_done: Optional[Callable[[int], None]] = None) -> bool:
    """Start the listener thread. Returns False if it is already running."""
    global _thread
    with _lock:
        if is_running():
            return False
        _stop.clear()

        def _run():
            from whisperfast.telegram.gui_bridge import enqueue_telegram_file
            from whisperfast.telegram.worker import run_from_settings

            code = 1
            try:
                code = run_from_settings(
                    submit=enqueue_telegram_file,
                    gui_running=lambda: True,
                    stop=_stop.is_set,
                    log=log or (lambda _msg: None),
                    poll_timeout=5,
                )
            finally:
                if on_done is not None:
                    on_done(code)

        _thread = threading.Thread(target=_run, name="ftw-telegram", daemon=True)
        _thread.start()
        return True


def stop() -> None:
    _stop.set()
