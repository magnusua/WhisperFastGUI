"""Resolve output path conflicts: overwrite, timed name, or skip write."""
from __future__ import annotations

import os
import threading
from datetime import datetime
from typing import Callable, List, Optional, Sequence

AskOverwriteFn = Callable[[str, str], Optional[bool]]
# ask(path, alt_name) -> True overwrite, False use alt, None skip write


def make_timed_alt_path(path: str) -> str:
    """name.ext → name_HHMM.ext (hour+minute). If taken, use HHMMSS."""
    path = os.path.abspath(path)
    base, ext = os.path.splitext(path)
    stamp = datetime.now().strftime("%H%M")
    candidate = f"{base}_{stamp}{ext}"
    if not os.path.exists(candidate):
        return candidate
    stamp = datetime.now().strftime("%H%M%S")
    candidate = f"{base}_{stamp}{ext}"
    n = 1
    while os.path.exists(candidate):
        candidate = f"{base}_{stamp}_{n}{ext}"
        n += 1
    return candidate


def apply_time_suffix_to_paths(paths: Sequence[str], stamp: Optional[str] = None) -> List[str]:
    """Apply the same _HHMM (or given stamp) to every path."""
    stamp = stamp or datetime.now().strftime("%H%M")
    result: List[str] = []
    for path in paths:
        if not path:
            result.append(path)
            continue
        path = os.path.abspath(path)
        base, ext = os.path.splitext(path)
        candidate = f"{base}_{stamp}{ext}"
        if os.path.exists(candidate):
            stamp_s = datetime.now().strftime("%H%M%S")
            candidate = f"{base}_{stamp_s}{ext}"
            n = 1
            while os.path.exists(candidate):
                candidate = f"{base}_{stamp_s}_{n}{ext}"
                n += 1
        result.append(candidate)
    return result


def resolve_output_paths(
    paths: Sequence[str],
    ask_overwrite: AskOverwriteFn,
) -> List[str]:
    """
    If any path already exists, ask once (first existing file).
    Yes → keep paths (overwrite). No → append _HHMM to all paths in the group.
    Skip → empty strings (caller must not write).
    """
    normalized = [os.path.abspath(p) if p else p for p in paths]
    existing = [p for p in normalized if p and os.path.isfile(p)]
    if not existing:
        return normalized

    sample = existing[0]
    alt = make_timed_alt_path(sample)
    overwrite = ask_overwrite(sample, os.path.basename(alt))
    if overwrite is None:
        return [""] * len(normalized)
    if overwrite:
        return normalized

    # Same clock stamp for the whole group (txt+srt+mp3)
    stamp = datetime.now().strftime("%H%M")
    # Prefer alt's stamp if it matches pattern
    base_sample, _ = os.path.splitext(sample)
    alt_base, _ = os.path.splitext(alt)
    if alt_base.startswith(base_sample + "_"):
        stamp = alt_base[len(base_sample) + 1 :]
    return apply_time_suffix_to_paths(normalized, stamp=stamp)


def resolve_single_output_path(path: str, ask_overwrite: AskOverwriteFn) -> str:
    return resolve_output_paths([path], ask_overwrite)[0]


def ai_prompts_dialog_is_open(app) -> bool:
    """True if a «Промты» window is open (do not stack another modal over it)."""
    ai_jobs = getattr(app, "ai_jobs", None)
    if ai_jobs is None:
        return False
    if hasattr(ai_jobs, "has_open_prompt_dialog"):
        try:
            return bool(ai_jobs.has_open_prompt_dialog())
        except Exception:
            pass
    return bool(getattr(ai_jobs, "_prompt_dialog_job_id", None))


def ask_overwrite_via_tk(app, path: str, alt_name: str) -> Optional[bool]:
    """
    Blocking ask from a worker thread using Tk main loop.
    Returns True (overwrite), False (use timed name), or None (skip write / closed).

    Якщо відкрите вікно «Промты» — не питаємо (щоб діалог не ховався
    під ним і не стопорив Whisper); одразу збереження з суфіксом часу.
    """
    import tkinter as tk
    from tkinter import ttk

    from whisperfast.i18n import t

    if ai_prompts_dialog_is_open(app):
        return False

    pending = object()
    choice: List[object] = [pending]
    done = threading.Event()

    def ask():
        try:
            # Якщо за час очікування відкрили «Промты» — без запитання
            if ai_prompts_dialog_is_open(app):
                choice[0] = False
                done.set()
                return

            parent = getattr(app, "root", None)
            dlg = tk.Toplevel(parent)
            dlg.title(t("file_exists_title"))
            dlg.resizable(False, False)
            if parent is not None:
                dlg.transient(parent)
            dlg.grab_set()

            body = ttk.Frame(dlg, padding=16)
            body.pack(fill="both", expand=True)
            ttk.Label(
                body,
                text=t(
                    "file_exists_msg",
                    name=os.path.basename(path),
                    alt=alt_name,
                ),
                justify="left",
                wraplength=420,
            ).pack(anchor="w")

            bf = ttk.Frame(body)
            bf.pack(fill="x", pady=(16, 0))

            def finish(val: Optional[bool]):
                choice[0] = val
                try:
                    dlg.grab_release()
                except Exception:
                    pass
                try:
                    dlg.destroy()
                except Exception:
                    pass
                done.set()

            # Right-aligned: Yes | No | Skip
            ttk.Button(bf, text=t("file_exists_skip"), command=lambda: finish(None)).pack(
                side="right"
            )
            ttk.Button(bf, text=t("file_exists_no"), command=lambda: finish(False)).pack(
                side="right", padx=(0, 8)
            )
            yes_btn = ttk.Button(bf, text=t("file_exists_yes"), command=lambda: finish(True))
            yes_btn.pack(side="right", padx=(0, 8))

            dlg.protocol("WM_DELETE_WINDOW", lambda: finish(None))
            dlg.bind("<Escape>", lambda _e: finish(None))
            dlg.bind("<Return>", lambda _e: finish(True))

            dlg.update_idletasks()
            if parent is not None:
                try:
                    from whisperfast.ui.dialogs import center_toplevel

                    center_toplevel(app, dlg, parent=parent)
                except Exception:
                    pass
            yes_btn.focus_set()
        except Exception:
            choice[0] = False
            done.set()

    try:
        app.root.after(0, ask)
    except Exception:
        return False

    while not done.is_set():
        if getattr(app, "cancel_requested", False):
            return False
        if ai_prompts_dialog_is_open(app):
            return False
        done.wait(timeout=0.05)

    result = choice[0]
    if result is True:
        return True
    if result is False:
        return False
    return None
