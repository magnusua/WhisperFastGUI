"""
Черга файлів і слідкування за каталогами.
Persist request_queue.json, додавання/видалення, DirectoryWatcher з pending/стабільністю.
"""
from __future__ import annotations

import os
import json
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from whisperfast.config import BASE_DIR, VALID_EXTS, DEFAULT_START_TIMESTAMP
from whisperfast.i18n import t
from whisperfast.core.input_files import add_files_to_queue_controller, is_valid_file
from whisperfast.utils import (
    make_queue_item,
    normalize_queue_path,
    normalize_display_path,
)

# --- Константи слідкування ---
WATCH_POLL_INTERVAL_S = 10.0
WATCH_MIN_AGE_S = 10.0
WATCH_STABLE_S = 15.0  # у діапазоні ~10–20 с
WATCH_MAX_DECODE_RETRIES = 2


def parse_watch_dirs(raw):
    """Розбирає рядок каталогів з settings (через кому) у список нормалізованих шляхів."""
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        parts = [str(p) for p in raw]
    else:
        parts = str(raw).split(",")
    result = []
    seen = set()
    for part in parts:
        path = normalize_display_path(part.strip().strip('"').strip("'"))
        if not path:
            continue
        key = os.path.normcase(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def serialize_watch_dirs(dirs):
    """Зберігає список каталогів у рядок через кому для settings.json."""
    parts = []
    seen = set()
    for d in dirs or []:
        path = normalize_display_path((d or "").strip().strip('"').strip("'"))
        if not path:
            continue
        key = os.path.normcase(path)
        if key in seen:
            continue
        seen.add(key)
        parts.append(path)
    return ", ".join(parts)


def valid_watch_dirs(dirs):
    """Повертає лише існуючі каталоги."""
    out = []
    for d in parse_watch_dirs(dirs) if isinstance(dirs, str) else (dirs or []):
        path = normalize_display_path(d)
        if path and os.path.isdir(path):
            out.append(path)
    return out


def _watch_filename_is_program_output(name):
    return name.lower().endswith("_audio.mp3")


def _file_age_seconds(path):
    try:
        return max(0.0, time.time() - os.path.getmtime(path))
    except OSError:
        return 0.0


def _file_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def _file_openable(path):
    """Чи можна відкрити файл на читання (не заблокований записом)."""
    try:
        with open(path, "rb") as f:
            f.read(1)
        return True
    except OSError:
        return False


def _next_entry_batch_size(current_count):
    """Скільки порожніх рядків додає кнопка +: 1 → 5 → 10."""
    if current_count < 5:
        return 1
    if current_count < 10:
        return 5
    return 10


def _path_match_keys(path):
    keys = set()
    n = normalize_queue_path(path)
    if not n:
        return keys
    keys.add(n)
    try:
        keys.add(os.path.normpath(os.path.abspath(n)))
    except OSError:
        pass
    return keys


class DirectoryWatcher:
    """
    Опитує каталоги кожні 10 с.
    Новий файл → pending → мін. вік ≥ 10 с → size стабільний ~15 с і open ok → callback.
    При помилці декодування — до 2 retry з pending.
    """

    def __init__(self, on_file_ready, log_func=None, get_dirs=None):
        self._on_file_ready = on_file_ready
        self._log = log_func or (lambda *_a, **_k: None)
        self._get_dirs = get_dirs or (lambda: [])
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._seen = set()  # вже враховані / віддані в чергу / вичерпані retry
        self._pending = {}  # path -> state dict
        self._decode_retries = {}  # path -> int

    def register_output_paths(self, paths):
        norm = []
        for p in paths:
            if not p:
                continue
            try:
                norm.append(os.path.normpath(os.path.abspath(p)))
            except OSError:
                continue
        if not norm:
            return
        with self._lock:
            self._seen.update(norm)

    def notify_decode_failed(self, path):
        """Повертає файл у pending для повторної спроби (макс. WATCH_MAX_DECODE_RETRIES)."""
        path = normalize_queue_path(path) or path
        if not path:
            return False
        try:
            path = os.path.normpath(os.path.abspath(path))
        except OSError:
            path = os.path.normpath(path)
        with self._lock:
            retries = self._decode_retries.get(path, 0) + 1
            self._decode_retries[path] = retries
            self._seen.discard(path)
            if retries > WATCH_MAX_DECODE_RETRIES:
                self._seen.add(path)
                self._pending.pop(path, None)
                self._log(t("watch_decode_give_up", name=os.path.basename(path), count=retries))
                return False
            size = _file_size(path)
            self._pending[path] = {
                "first_seen": time.time(),
                "last_size": size,
                "stable_since": None,
                "retries": retries,
            }
            self._log(t("watch_decode_retry", name=os.path.basename(path), attempt=retries))
            return True

    def start(self):
        self.stop()
        self._stop.clear()
        dirs = valid_watch_dirs(self._get_dirs())
        initial = set()
        for watch_path in dirs:
            try:
                for f in os.listdir(watch_path):
                    full = os.path.normpath(os.path.abspath(os.path.join(watch_path, f)))
                    if not (os.path.isfile(full) and f.lower().endswith(VALID_EXTS)):
                        continue
                    if _watch_filename_is_program_output(f):
                        initial.add(full)
                        continue
                    initial.add(full)
            except OSError:
                continue
        with self._lock:
            self._seen = initial
            self._pending = {}
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        paths_str = "; ".join(dirs) if dirs else ""
        self._log(t("watch_started", path=paths_str or "—"))

    def stop(self):
        self._stop.set()
        self._thread = None

    def is_running(self):
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def _scan_current_files(self):
        current = set()
        for watch_path in valid_watch_dirs(self._get_dirs()):
            try:
                for f in os.listdir(watch_path):
                    full = os.path.normpath(os.path.abspath(os.path.join(watch_path, f)))
                    if not (os.path.isfile(full) and f.lower().endswith(VALID_EXTS)):
                        continue
                    if _watch_filename_is_program_output(f):
                        with self._lock:
                            self._seen.add(full)
                        continue
                    current.add(full)
            except OSError:
                continue
        return current

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except OSError:
                pass
            for _ in range(int(WATCH_POLL_INTERVAL_S / 0.25)):
                if self._stop.is_set():
                    break
                time.sleep(0.25)

    def _tick(self):
        dirs = valid_watch_dirs(self._get_dirs())
        if not dirs:
            return
        current = self._scan_current_files()
        now = time.time()
        ready = []

        with self._lock:
            for path in current:
                if path in self._seen:
                    continue
                if path not in self._pending:
                    self._pending[path] = {
                        "first_seen": now,
                        "last_size": _file_size(path),
                        "stable_since": None,
                        "retries": self._decode_retries.get(path, 0),
                    }
                    self._log(t("watch_pending", name=os.path.basename(path)))

            gone = [p for p in self._pending if p not in current]
            for p in gone:
                self._pending.pop(p, None)

            for path, state in list(self._pending.items()):
                age = now - state["first_seen"]
                if age < WATCH_MIN_AGE_S:
                    continue
                if _file_age_seconds(path) < WATCH_MIN_AGE_S:
                    continue
                size = _file_size(path)
                if size is None:
                    state["stable_since"] = None
                    continue
                if not _file_openable(path):
                    state["stable_since"] = None
                    state["last_size"] = size
                    continue
                if state["last_size"] != size:
                    state["last_size"] = size
                    state["stable_since"] = None
                    continue
                if state["stable_since"] is None:
                    state["stable_since"] = now
                    continue
                if now - state["stable_since"] < WATCH_STABLE_S:
                    continue
                self._pending.pop(path, None)
                self._seen.add(path)
                ready.append(path)

        for path in ready:
            self._log(t("watch_new_file", name=os.path.basename(path)))
            try:
                self._on_file_ready(path)
            except Exception:
                with self._lock:
                    self._seen.discard(path)


class QueueController:
    """Керування чергою (request_queue.json) і зв’язок зі слідкуванням."""

    def __init__(self, request_queue_file=None, log_func=None, root_after=None):
        self.queue = []
        self._request_queue_file = request_queue_file or os.path.join(BASE_DIR, "request_queue.json")
        self._log = log_func or (lambda *_a, **_k: None)
        self._root_after = root_after  # callable(ms_or_0, fn) — Tk after
        self.queue_list = None
        self.watch_pending_continue = False
        self.watcher = DirectoryWatcher(
            on_file_ready=self._on_watch_file_ready_threadsafe,
            log_func=self._log,
            get_dirs=lambda: [],
        )
        self._start_processing = None  # set by GUI: fn(mode, target_idx=None, from_watch=False)
        self._is_processing = None  # set by GUI: fn() -> bool

    def configure(self, *, get_watch_dirs, start_processing, is_processing, log_func=None, root_after=None):
        self.watcher._get_dirs = get_watch_dirs
        self._start_processing = start_processing
        self._is_processing = is_processing
        if log_func:
            self._log = log_func
            self.watcher._log = log_func
        if root_after:
            self._root_after = root_after

    def bind_treeview(self, treeview):
        self.queue_list = treeview

    # --- Persist ---

    def load_from_file(self):
        if not os.path.exists(self._request_queue_file):
            return
        try:
            with open(self._request_queue_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                return
            self.queue.clear()
            for item in data:
                path = normalize_queue_path(item.get("path"))
                if not path or not os.path.isfile(path):
                    continue
                overrides = {
                    "start": item.get("start") or DEFAULT_START_TIMESTAMP,
                    "end_segment_1": item.get("end_segment_1") or "",
                    "end_segment_2": item.get("end_segment_2") or "",
                    "processed": item.get("processed", False),
                }
                if item.get("end"):
                    overrides["end"] = item.get("end")
                self.queue.append(make_queue_item(path, **overrides))
            self.refresh_treeview()
        except (json.JSONDecodeError, OSError):
            pass

    def save_to_file(self):
        try:
            data = [
                {
                    "path": q["path"],
                    "start": q["start"],
                    "end_segment_1": q.get("end_segment_1", ""),
                    "end_segment_2": q.get("end_segment_2", ""),
                    "end": q["end"],
                    "processed": q.get("processed", False),
                }
                for q in self.queue
            ]
            with open(self._request_queue_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def ensure_file_exists(self):
        if not os.path.exists(self._request_queue_file):
            self.save_to_file()

    def refresh_treeview(self):
        if self.queue_list is None:
            return
        self.queue_list.delete(*self.queue_list.get_children())
        for i, q in enumerate(self.queue):
            name = os.path.basename(q["path"])
            status_text = t("status_processed") if q.get("processed") else t("status_not_processed")
            self.queue_list.insert(
                "",
                "end",
                values=(
                    i + 1,
                    name,
                    q["start"],
                    q.get("end_segment_1", ""),
                    q.get("end_segment_2", ""),
                    q["end"],
                    status_text,
                ),
            )

    # --- Mutations ---

    def add_files(self, file_paths):
        if self.queue_list is None:
            return 0, 0
        added, skipped = add_files_to_queue_controller(
            file_paths, self.queue, self.queue_list, log_func=self._log
        )
        if added:
            self.save_to_file()
        return added, skipped

    def clear(self):
        self.queue.clear()
        if self.queue_list is not None:
            self.queue_list.delete(*self.queue_list.get_children())
        self.save_to_file()

    def delete_indices(self, indices):
        for idx in sorted(set(indices), reverse=True):
            if 0 <= idx < len(self.queue):
                del self.queue[idx]
        self.refresh_treeview()
        self.save_to_file()

    def reorder(self, from_idx, to_idx):
        if from_idx == to_idx:
            return
        if not (0 <= from_idx < len(self.queue) and 0 <= to_idx < len(self.queue)):
            return
        item = self.queue.pop(from_idx)
        self.queue.insert(to_idx, item)
        self.refresh_treeview()
        self.save_to_file()

    def mark_done_by_path(self, path):
        keys = _path_match_keys(path)
        for q in self.queue:
            if _path_match_keys(q.get("path")) & keys:
                q["processed"] = True
                break
        self.refresh_treeview()
        self.save_to_file()

    def mark_done(self, idx):
        if 0 <= idx < len(self.queue):
            self.queue[idx]["processed"] = True
            self.refresh_treeview()
            self.save_to_file()

    def remove_paths(self, paths):
        skipped = set()
        for p in paths or []:
            skipped |= _path_match_keys(p)
        if not skipped:
            return
        self.queue[:] = [q for q in self.queue if not (_path_match_keys(q.get("path")) & skipped)]
        self.refresh_treeview()
        self.save_to_file()

    def update_row(self, idx, **fields):
        if 0 <= idx < len(self.queue):
            self.queue[idx].update(fields)
            self.refresh_treeview()
            self.save_to_file()

    # --- Watch integration ---

    def start_watch(self):
        self.watcher.start()

    def stop_watch(self):
        self.watcher.stop()

    def register_output_paths(self, paths):
        self.watcher.register_output_paths(paths)

    def notify_decode_failed(self, path):
        return self.watcher.notify_decode_failed(path)

    def _on_watch_file_ready_threadsafe(self, path):
        after = self._root_after
        if after:
            after(0, lambda p=path: self._add_watch_file_to_queue(p))
        else:
            self._add_watch_file_to_queue(path)

    def _add_watch_file_to_queue(self, path):
        if not os.path.isfile(path) or not is_valid_file(path):
            return
        before = len(self.queue)
        self.add_files([path])
        if len(self.queue) <= before:
            return
        idx = len(self.queue) - 1
        busy = False
        if self._is_processing:
            try:
                busy = bool(self._is_processing())
            except Exception:
                busy = False
        if busy:
            self.watch_pending_continue = True
            self._log(t("watch_queued_for_later", name=os.path.basename(path)))
        elif self._start_processing:
            self._start_processing(mode="single", target_idx=idx, from_watch=True)

    def continue_after_processing(self, cancel_requested=False):
        """Після завершення задачі — обробити файли, додані слідкуванням під час зайнятості."""
        if cancel_requested:
            self.watch_pending_continue = False
            return False
        if not self.watch_pending_continue:
            return False
        if not any(not q.get("processed") for q in self.queue):
            self.watch_pending_continue = False
            return False
        self.watch_pending_continue = False
        self._log(t("watch_continue_queue"))
        if self._start_processing:
            self._start_processing(mode="only_new", from_watch=True)
        return True


def open_watch_dirs_dialog(parent, initial_dirs, on_save, center_fn=None):
    """
    Модальне вікно списку каталогів слідкування.
    Зберегти — викликає on_save(list); закриття вікна = скасування.
    """
    initial = list(initial_dirs or [])
    if not initial:
        initial = [""]

    dialog = tk.Toplevel(parent)
    dialog.title(t("watch_dirs_dialog_title"))
    dialog.transient(parent)
    dialog.grab_set()
    dialog.resizable(True, True)

    outer = ttk.Frame(dialog, padding=12)
    outer.pack(fill="both", expand=True)

    ttk.Label(outer, text=t("watch_dirs_dialog_hint")).pack(anchor="w", pady=(0, 8))

    entries_host = ttk.Frame(outer)
    entries_host.pack(fill="both", expand=True)

    entry_vars = []

    def _rebuild_layout():
        for child in entries_host.winfo_children():
            child.destroy()
        n = len(entry_vars)
        cols = 2 if n > 10 else 1
        rows_per_col = (n + cols - 1) // cols if cols else n
        for i, var in enumerate(entry_vars):
            col = i // rows_per_col if cols > 1 else 0
            row = i % rows_per_col if cols > 1 else i
            cell = ttk.Frame(entries_host)
            cell.grid(row=row, column=col, sticky="ew", padx=4, pady=2)
            ent = ttk.Entry(cell, textvariable=var, width=42)
            ent.pack(side="left", fill="x", expand=True)

            def browse(v=var):
                d = filedialog.askdirectory(parent=dialog)
                if d:
                    v.set(normalize_display_path(d))

            ttk.Button(cell, text="…", width=3, command=browse).pack(side="left", padx=(4, 0))
        for c in range(cols):
            entries_host.columnconfigure(c, weight=1)
        _resize_dialog()

    def _resize_dialog():
        dialog.update_idletasks()
        n = len(entry_vars)
        cols = 2 if n > 10 else 1
        rows = (n + cols - 1) // cols
        row_h = 32
        base_h = 120
        base_w = 520 if cols == 1 else 980
        h = min(base_h + rows * row_h, 700)
        w = base_w
        dialog.geometry(f"{w}x{h}")
        if center_fn:
            center_fn(dialog)

    def add_rows():
        batch = _next_entry_batch_size(len(entry_vars))
        for _ in range(batch):
            entry_vars.append(tk.StringVar(value=""))
        _rebuild_layout()

    for path in initial:
        entry_vars.append(tk.StringVar(value=normalize_display_path(path) if path else ""))
    _rebuild_layout()

    btns = ttk.Frame(outer)
    btns.pack(fill="x", pady=(10, 0))

    def close_cancel():
        dialog.destroy()

    def save_and_close():
        dirs = []
        for var in entry_vars:
            raw = (var.get() or "").strip()
            if not raw:
                continue
            path = normalize_display_path(raw.strip('"').strip("'"))
            if not path:
                continue
            if not os.path.isdir(path):
                messagebox.showerror(
                    t("error"),
                    t("watch_dir_invalid", path=path),
                    parent=dialog,
                )
                return
            dirs.append(path)
        on_save(dirs)
        dialog.destroy()

    ttk.Button(btns, text="+", width=4, command=add_rows).pack(side="left")
    ttk.Button(btns, text=t("save"), command=save_and_close).pack(side="right", padx=(5, 0))
    ttk.Button(btns, text=t("cancel_btn"), command=close_cancel).pack(side="right")

    dialog.protocol("WM_DELETE_WINDOW", close_cancel)
    dialog.bind("<Escape>", lambda e: close_cancel())
    if center_fn:
        center_fn(dialog)
    else:
        dialog.update_idletasks()
        dialog.geometry("+%d+%d" % (parent.winfo_rootx() + 40, parent.winfo_rooty() + 40))
    dialog.wait_window()
