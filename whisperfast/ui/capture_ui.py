"""Capture button / consent / hotkey helpers (Tkinter)."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox

from whisperfast.core.capture import (
    capture_available,
    default_capture_dir,
    get_capture_session,
    recover_captures,
)
from whisperfast.i18n import t


def ensure_consent(app) -> bool:
    if getattr(app, "capture_consent_shown", None) and app.capture_consent_shown.get():
        return True
    ok = messagebox.askokcancel(t("capture_title"), t("capture_consent"), parent=app.root)
    if not ok:
        return False
    if getattr(app, "capture_consent_shown", None) is not None:
        app.capture_consent_shown.set(True)
        persist = getattr(app, "_persist_settings", None)
        if callable(persist):
            persist()
    return True


def toggle_capture(app) -> None:
    session = get_capture_session()
    if session.running:
        path = session.stop(log_func=app.log)
        refresh_capture_buttons(app)
        if path and os.path.isfile(path):
            _enqueue_capture(app, path)
        return
    if not capture_available():
        messagebox.showinfo(t("capture_title"), t("capture_need_sounddevice"), parent=app.root)
        return
    if not ensure_consent(app):
        return
    out_dir = (app.output_dir.get() or "").strip()
    if not out_dir or not os.path.isdir(out_dir):
        out_dir = default_capture_dir()
    try:
        session.start(out_dir, log_func=app.log)
    except Exception as e:
        messagebox.showerror(t("capture_title"), str(e), parent=app.root)
        return
    refresh_capture_buttons(app)


def toggle_pause(app) -> None:
    session = get_capture_session()
    if not session.running:
        return
    session.toggle_pause(log_func=app.log)
    refresh_capture_buttons(app)


def recover_interrupted_captures(app) -> None:
    dirs = []
    out_dir = ""
    try:
        out_dir = (app.output_dir.get() or "").strip()
    except Exception:
        out_dir = ""
    if out_dir and os.path.isdir(out_dir):
        dirs.append(out_dir)
    default_dir = default_capture_dir()
    if default_dir not in dirs:
        dirs.append(default_dir)
    seen = set()
    for folder in dirs:
        for path in recover_captures(folder):
            key = os.path.normcase(path)
            if key in seen:
                continue
            seen.add(key)
            app.log(t("capture_recovered", path=path))
            _enqueue_capture(app, path)


def capture_blocks_shutdown() -> bool:
    return bool(get_capture_session().running)


def _enqueue_capture(app, path: str) -> None:
    try:
        app.queue_ctrl.add_files([path])
    except Exception:
        app.log(path)


def refresh_capture_buttons(app) -> None:
    session = get_capture_session()
    running = session.running
    paused = session.paused
    btn = getattr(app, "capture_btn", None)
    pause_btn = getattr(app, "capture_pause_btn", None)
    if btn is not None:
        try:
            btn.config(text=t("capture_stop") if running else t("capture_start"))
        except tk.TclError:
            pass
    if pause_btn is not None:
        try:
            pause_btn.config(
                text=t("capture_resume") if paused else t("capture_pause"),
                state=("normal" if running else "disabled"),
            )
        except tk.TclError:
            pass


def _refresh_capture_button(app) -> None:
    refresh_capture_buttons(app)


def bind_capture_hotkey(app) -> None:
    def on_key(event=None):
        del event
        toggle_capture(app)
        return "break"

    def on_pause(event=None):
        del event
        toggle_pause(app)
        return "break"

    try:
        app.root.bind_all("<Control-Shift-R>", on_key)
        app.root.bind_all("<Control-Shift-r>", on_key)
        app.root.bind_all("<Control-Shift-P>", on_pause)
        app.root.bind_all("<Control-Shift-p>", on_pause)
    except tk.TclError:
        pass
