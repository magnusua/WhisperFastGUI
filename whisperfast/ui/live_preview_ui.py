"""Small overlay for disposable live transcript preview."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from whisperfast.core.live_preview import is_running, last_error, snapshot_text, stop_preview
from whisperfast.i18n import t
from whisperfast.ui.dialogs import center_toplevel


def show_live_preview(app):
    existing = getattr(app, "_live_preview_window", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.lift()
                return existing
        except tk.TclError:
            app._live_preview_window = None

    dialog = tk.Toplevel(app.root)
    dialog.title(t("live_preview_title"))
    dialog.transient(app.root)
    dialog.geometry("420x240")
    dialog.minsize(320, 180)
    frame = ttk.Frame(dialog, padding=8)
    frame.pack(fill="both", expand=True)
    hint = ttk.Label(frame, text=t("live_preview_hint"), wraplength=380)
    hint.pack(anchor="w")
    text = tk.Text(frame, wrap="word", height=10, font=("Segoe UI", 10))
    text.pack(fill="both", expand=True, pady=(6, 0))
    err = ttk.Label(frame, text="")
    err.pack(anchor="w")

    def refresh():
        try:
            body = snapshot_text() or t("live_preview_waiting")
            text.delete("1.0", tk.END)
            text.insert("1.0", body)
            e = last_error()
            err.config(text=e[:200] if e else "")
            if is_running():
                dialog.after(1000, refresh)
        except tk.TclError:
            pass

    def _on_close():
        stop_preview()
        try:
            dialog.destroy()
        except tk.TclError:
            pass
        if getattr(app, "_live_preview_window", None) is dialog:
            app._live_preview_window = None

    dialog.protocol("WM_DELETE_WINDOW", _on_close)
    center_toplevel(app, dialog)
    app._live_preview_window = dialog
    refresh()
    return dialog
