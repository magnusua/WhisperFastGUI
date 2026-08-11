"""Інтерактивний лог з групуванням по днях (Text + app_log.json).

Ієрархія згортання:
  1. День — сьогодні розгорнутий за замовчуванням, минулі згорнуті.
     Тіло дня вивантажується при згортанні (без вкладеного elide).
  2. Файл — розгорнутий за замовчуванням (elide тіла).
  3.1 Транскрипція — згорнута за замовчуванням.
  3.2 Створені файли — розгорнуті за замовчуванням (+ окрема кнопка промтів).
"""

from __future__ import annotations

import os
import uuid
import tkinter as tk

from whisperfast.i18n import t
from whisperfast.log_store import (
    KIND_FILE,
    KIND_LINE,
    LogStore,
    today_key,
)
from whisperfast.open_path import open_file, open_file_location
from whisperfast.ui.widgets import LOG_MAX_LINES

_ROLE_I18N = {
    "source": "log_file_output_source",
    "txt": "txt_file",
    "srt": "srt_file",
    "mp3": "audio_mp3_file",
    "md": "doc_md_file",
}

# Рядки «файли створено…» не дублюємо — їх замінює секція «Створені файли» + кнопка промтів
_FILES_CREATED_MARKERS = (
    "files created for",
    "файли створено для",
    "файлы созданы для",
)


class LogPanel:
    """Керує ScrolledText-логом: store, дні, file-сесії, link/action, меню копіювання."""

    def __init__(self, root):
        self.root = root
        self.log_box = None
        self.log_menu = None
        self._store = LogStore(
            on_schedule_flush=lambda delay: self.root.after(
                int(delay * 1000), self._store.flush
            )
        )
        self._action_callbacks = {}
        self._day_expanded = {}
        self._day_body_loaded = {}
        self._ui_day = None
        self._active_file_id = None
        self._file_block_tags = {}  # file_id -> tag name spanning the block
        self._file_expanded = {}  # file_id -> bool (default True)
        self._file_tx_expanded = {}  # file_id -> bool (default False)
        self._file_out_expanded = {}  # file_id -> bool (default True)
        self._file_action_callbacks = {}  # file_id -> latest action callback

    def bind_widget(self, log_box):
        self.log_box = log_box

    def flush(self):
        self._store.flush()

    # --- Plain lines ---------------------------------------------------------

    def log(self, msg, tag=None):
        text = str(msg) + ("" if str(msg).endswith("\n") else "\n")
        entry = self._store.append_line(text, tag=tag)

        def _do_log():
            self._append_line_ui(entry.get("day") or today_key(), text, tag)
            self._scroll_to_end_if_today()

        self.root.after(0, _do_log)

    def log_action(self, msg, callback):
        """Клікабельний рядок лога (синій, як link), викликає callback."""
        text = str(msg) + ("" if str(msg).endswith("\n") else "\n")
        entry = self._store.append_line(text, tag="action")
        action_tag = f"action_{uuid.uuid4().hex}"

        def _do_log():
            self._action_callbacks[action_tag] = callback
            self._append_line_ui(
                entry.get("day") or today_key(),
                text,
                "action",
                extra_tags=(action_tag,),
            )
            self._scroll_to_end_if_today()

        self.root.after(0, _do_log)

    # --- File sessions -------------------------------------------------------

    def begin_file(self, source, name=None, current=None, total=None):
        entry = self._store.begin_file(source, name=name, current=current, total=total)
        file_id = entry["id"]
        self._active_file_id = file_id

        def _do():
            self._render_file_entry(entry, insert_new=True)
            self._refresh_day_header(entry.get("day") or today_key())
            self._scroll_to_end_if_today()

        self.root.after(0, _do)
        return file_id

    def attach_file(self, file_id):
        """Зробити сесію активною (напр. AI-job для того ж файлу)."""
        if file_id:
            self._active_file_id = file_id
        return file_id

    def find_file_id_for_path(self, path):
        entry = self._store.find_file_by_output(path) or self._store.find_file_by_source(path)
        return entry.get("id") if entry else None

    def log_file_event(self, msg, tag=None, file_id=None, callback=None):
        fid = file_id or self._active_file_id
        if not fid:
            if callback is not None:
                self.log_action(msg, callback)
            else:
                self.log(msg, tag)
            return
        text = str(msg) + ("" if str(msg).endswith("\n") else "\n")
        use_tag = "action" if callback is not None else tag
        entry = self._store.add_file_event(fid, text, tag=use_tag)

        def _do():
            if callback is not None:
                self._file_action_callbacks[fid] = callback
            if entry:
                self._render_file_entry(entry, insert_new=False)
            self._scroll_to_end_if_today()

        self.root.after(0, _do)

    def log_file_segment(self, t_str, text, count=None, file_id=None):
        fid = file_id or self._active_file_id
        if not fid:
            self.log(f"   [{t_str}] {text}")
            return
        entry = self._store.set_file_segment(fid, t_str, text, count=count)

        def _do():
            if entry:
                self._render_file_entry(entry, insert_new=False)
            self._scroll_to_end_if_today()

        self.root.after(0, _do)

    def add_file_output(self, role, path, label=None, file_id=None):
        fid = file_id or self._active_file_id
        if not fid:
            self.log(path, "link")
            return
        entry = self._store.add_file_output(fid, role, path, label=label)

        def _do():
            if entry:
                self._render_file_entry(entry, insert_new=False)
            self._scroll_to_end_if_today()

        self.root.after(0, _do)

    def set_file_source(self, path, file_id=None):
        """Оновити шлях исходника після переносу поруч із результатами."""
        fid = file_id or self._active_file_id
        if not fid or not path:
            return
        entry = self._store.set_file_source(fid, path)

        def _do():
            if entry:
                self._render_file_entry(entry, insert_new=False)
            self._scroll_to_end_if_today()

        self.root.after(0, _do)

    def end_file(self, status="done", error=None, file_id=None):
        fid = file_id or self._active_file_id
        if not fid:
            return
        entry = self._store.end_file(fid, status=status, error=error)
        if self._active_file_id == fid:
            self._active_file_id = None

        def _do():
            if entry:
                self._render_file_entry(entry, insert_new=False)
            self._scroll_to_end_if_today()

        self.root.after(0, _do)

    def set_file_prompt_callback(self, file_id, callback):
        """Прив'язати кнопку «Обрати промти» до file-сесії (без action-рядка в events)."""
        fid = file_id or self._active_file_id
        if not fid or callback is None:
            return

        def _do():
            self._file_action_callbacks[fid] = callback
            entry = self._store.get_file(fid)
            if entry:
                self._render_file_entry(entry, insert_new=False)
            self._scroll_to_end_if_today()

        self.root.after(0, _do)

    def make_file_logger(self, file_id):
        """Callable сумісний з log_func(msg, tag=None) — пише в file-сесію."""

        def _log(msg, tag=None):
            self.log_file_event(msg, tag=tag, file_id=file_id)

        return _log

    # --- Clear / styles / reload ---------------------------------------------

    def clear(self):
        self._store.clear()
        self._action_callbacks.clear()
        self._file_action_callbacks.clear()
        self._day_expanded.clear()
        self._day_body_loaded.clear()
        self._file_block_tags.clear()
        self._file_expanded.clear()
        self._file_tx_expanded.clear()
        self._file_out_expanded.clear()
        self._ui_day = None
        self._active_file_id = None
        if self.log_box is None:
            return
        self.log_box.config(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.config(state="disabled")

    def setup_styles(self):
        """Інтерактивні посилання та меню копіювання."""
        box = self.log_box
        box.tag_config("link", foreground="blue", underline=1)
        box.tag_bind("link", "<Button-1>", self.on_link_click)
        box.tag_bind("link", "<Shift-Button-1>", self.on_link_shift_click)
        box.tag_bind("link", "<Enter>", lambda e: box.config(cursor="hand2"))
        box.tag_bind("link", "<Leave>", lambda e: box.config(cursor=""))
        box.tag_config("action", foreground="blue", underline=1)
        box.tag_bind("action", "<Button-1>", self.on_action_click)
        box.tag_bind("action", "<Enter>", lambda e: box.config(cursor="hand2"))
        box.tag_bind("action", "<Leave>", lambda e: box.config(cursor=""))
        box.tag_config(
            "day_header",
            foreground="#1a5fb4",
            font=("Consolas", 9, "bold"),
            spacing1=6,
            spacing3=2,
        )
        box.tag_bind("day_header", "<Button-1>", self.on_day_header_click)
        box.tag_bind("day_header", "<Enter>", lambda e: box.config(cursor="hand2"))
        box.tag_bind("day_header", "<Leave>", lambda e: box.config(cursor=""))
        box.tag_config(
            "file_header",
            foreground="#206040",
            font=("Consolas", 9, "bold"),
        )
        box.tag_bind("file_header", "<Button-1>", self.on_file_header_click)
        box.tag_bind("file_header", "<Enter>", lambda e: box.config(cursor="hand2"))
        box.tag_bind("file_header", "<Leave>", lambda e: box.config(cursor=""))
        box.tag_config(
            "file_section_header",
            foreground="#4a4a4a",
            font=("Consolas", 9, "bold"),
        )
        box.tag_bind("file_section_header", "<Button-1>", self.on_file_section_header_click)
        box.tag_bind(
            "file_section_header", "<Enter>", lambda e: box.config(cursor="hand2")
        )
        box.tag_bind("file_section_header", "<Leave>", lambda e: box.config(cursor=""))
        self.log_menu = tk.Menu(self.root, tearoff=0)
        self.log_menu.add_command(label=t("copy"), command=self.copy_selection)
        box.bind("<Button-3>", lambda e: self.log_menu.tk_popup(e.x_root, e.y_root))
        box.bind("<Control-c>", self._copy_event)
        box.bind("<<Copy>>", self._copy_event)

    def reload_from_store(self):
        """Побудувати лог з JSON: минулі дні — лише заголовки; сьогодні — повністю."""
        self._store.prune_days_without_files(keep_today=True)
        self._action_callbacks.clear()
        self._file_action_callbacks.clear()
        self._day_expanded.clear()
        self._day_body_loaded.clear()
        self._file_block_tags.clear()
        self._file_expanded.clear()
        self._file_tx_expanded.clear()
        self._file_out_expanded.clear()
        self._ui_day = None
        box = self.log_box
        box.config(state="normal")
        box.delete("1.0", "end")
        box.config(state="disabled")

        today = today_key()
        for day_key in self._visible_day_keys():
            expanded = day_key == today
            self._day_expanded[day_key] = expanded
            self._day_body_loaded[day_key] = False
            box.config(state="normal")
            box.insert(
                "end",
                self._day_header_text(day_key, expanded),
                ("day_header", self._day_header_tag(day_key)),
            )
            box.config(state="disabled")
            self._ui_day = day_key
            if expanded:
                self._load_day_body(day_key)

        if today not in self._day_expanded:
            self._ensure_day_ui(today)

        self._scroll_to_end_if_today()

    def refresh_i18n(self):
        """Перемалювати лог після зміни мови; зберегти згортання та кнопку промтів."""
        if self.log_box is None:
            return
        day_exp = dict(self._day_expanded)
        file_exp = dict(self._file_expanded)
        tx_exp = dict(self._file_tx_expanded)
        out_exp = dict(self._file_out_expanded)
        cbs = dict(self._file_action_callbacks)

        self._action_callbacks.clear()
        self._day_body_loaded.clear()
        self._file_block_tags.clear()
        self._day_expanded.clear()
        self._file_expanded = file_exp
        self._file_tx_expanded = tx_exp
        self._file_out_expanded = out_exp
        self._file_action_callbacks = cbs
        self._ui_day = None

        box = self.log_box
        box.config(state="normal")
        box.delete("1.0", "end")
        box.config(state="disabled")

        today = today_key()
        for day_key in self._visible_day_keys():
            if day_key in day_exp:
                expanded = bool(day_exp[day_key])
            else:
                expanded = day_key == today
            self._day_expanded[day_key] = expanded
            self._day_body_loaded[day_key] = False
            box.config(state="normal")
            box.insert(
                "end",
                self._day_header_text(day_key, expanded),
                ("day_header", self._day_header_tag(day_key)),
            )
            box.config(state="disabled")
            self._ui_day = day_key
            if expanded:
                self._load_day_body(day_key)

        if today not in self._day_expanded:
            self._ensure_day_ui(today)

    def update_copy_menu_label(self):
        try:
            if self.log_menu is not None:
                self.log_menu.entryconfig(0, label=t("copy"))
        except (tk.TclError, IndexError):
            pass

    # --- Day helpers ---------------------------------------------------------

    def _visible_day_keys(self):
        """Дні для UI: сьогодні завжди; інші — лише з ≥1 file-сесією."""
        today = today_key()
        keys = []
        for key in self._store.day_keys():
            if key == today or self._store.count_file_entries(key) > 0:
                keys.append(key)
        if today not in keys:
            keys.append(today)
        return sorted(keys)

    def _day_body_tag(self, day_key):
        return f"day_body_{day_key}"

    def _day_header_tag(self, day_key):
        return f"day_header_{day_key}"

    def _day_header_text(self, day_key, expanded):
        mark = "▼" if expanded else "▶"
        count = self._store.count_file_entries(day_key)
        if day_key == today_key():
            return (
                t("log_day_header_today", mark=mark, date=day_key, count=count) + "\n"
            )
        return t("log_day_header", mark=mark, date=day_key, count=count) + "\n"

    def _rewrite_day_header(self, day_key):
        if day_key not in self._day_expanded or self.log_box is None:
            return
        expanded = self._day_expanded.get(day_key, False)
        header_tag = self._day_header_tag(day_key)
        ranges = self.log_box.tag_ranges(header_tag)
        if len(ranges) < 2:
            return
        self.log_box.config(state="normal")
        self.log_box.delete(ranges[0], ranges[1])
        self.log_box.insert(
            ranges[0],
            self._day_header_text(day_key, expanded),
            ("day_header", header_tag),
        )
        self.log_box.config(state="disabled")

    def _refresh_day_header(self, day_key):
        self._rewrite_day_header(day_key)

    def _unload_day_body(self, day_key):
        """Видалити тіло дня з Text (згортання без вкладеного elide)."""
        if self.log_box is None or not self._day_body_loaded.get(day_key):
            self._day_body_loaded[day_key] = False
            return
        body_tag = self._day_body_tag(day_key)
        ranges = self.log_box.tag_ranges(body_tag)
        self.log_box.config(state="normal")
        pairs = list(zip(ranges[0::2], ranges[1::2]))
        for start, end in reversed(pairs):
            self.log_box.delete(start, end)
        self.log_box.config(state="disabled")
        self._day_body_loaded[day_key] = False
        for entry in self._store.get_entries(day_key):
            if entry.get("kind") == KIND_FILE:
                fid = entry.get("id")
                if fid:
                    self._file_block_tags.pop(fid, None)

    def _set_day_expanded(self, day_key, expanded):
        """Згорнути/розгорнути день: тіло вивантажується / підвантажується."""
        expanded = bool(expanded)
        self._day_expanded[day_key] = expanded
        if expanded:
            if not self._day_body_loaded.get(day_key):
                self._load_day_body(day_key)
        else:
            self._unload_day_body(day_key)
        self._rewrite_day_header(day_key)

    def _ensure_day_ui(self, day_key):
        today = today_key()

        if day_key not in self._day_expanded:
            # Новий день: не чіпаємо розгорнутість інших днів
            expanded = day_key == today
            self._day_expanded[day_key] = expanded
            self._day_body_loaded[day_key] = bool(expanded)
            self.log_box.config(state="normal")
            self.log_box.insert(
                "end",
                self._day_header_text(day_key, expanded),
                ("day_header", self._day_header_tag(day_key)),
            )
            self.log_box.config(state="disabled")
            self._ui_day = day_key
            return

        if self._day_expanded.get(day_key) and not self._day_body_loaded.get(day_key):
            self._load_day_body(day_key)
        self._ui_day = day_key

    def _load_day_body(self, day_key):
        """Вставити записи дня після заголовка (lazy для згорнутих днів)."""
        if self._day_body_loaded.get(day_key):
            return
        self._day_body_loaded[day_key] = True
        box = self.log_box
        header_tag = self._day_header_tag(day_key)
        ranges = box.tag_ranges(header_tag)
        insert_at = ranges[1] if len(ranges) >= 2 else "end"

        box.config(state="normal")
        cursor = insert_at
        for entry in self._store.get_entries(day_key):
            kind = entry.get("kind") or KIND_LINE
            if kind == KIND_FILE:
                cursor = self._insert_file_block(entry, index=cursor, day_key=day_key)
            else:
                text = entry.get("text") or ""
                if not text:
                    continue
                if not text.endswith("\n"):
                    text += "\n"
                tag = entry.get("tag")
                tags = [self._day_body_tag(day_key)]
                if tag == "link":
                    tags.append("link")
                elif tag == "action":
                    tags.append("action")
                box.insert(cursor, text, tuple(tags))
                cursor = box.index(f"{cursor}+{len(text)}c")
        box.config(state="disabled")

    # --- Insert / render -----------------------------------------------------

    def _append_line_ui(self, day_key, text, tag=None, extra_tags=()):
        if self.log_box is None:
            return
        self._ensure_day_ui(day_key)
        if not self._day_expanded.get(day_key, False):
            return
        if not self._day_body_loaded.get(day_key):
            self._load_day_body(day_key)
            return
        tags = [self._day_body_tag(day_key)]
        if tag:
            tags.append(tag)
        tags.extend(extra_tags)
        self.log_box.config(state="normal")
        self.log_box.insert("end", text, tuple(tags))
        self._trim_if_needed()
        self.log_box.config(state="disabled")

    def _file_block_tag(self, file_id):
        return f"file_block_{file_id}"

    def _file_header_tag(self, file_id):
        return f"file_header_{file_id}"

    def _file_body_tag(self, file_id):
        return f"file_body_{file_id}"

    def _file_tx_header_tag(self, file_id):
        return f"file_tx_header_{file_id}"

    def _file_tx_body_tag(self, file_id):
        return f"file_tx_body_{file_id}"

    def _file_out_header_tag(self, file_id):
        return f"file_out_header_{file_id}"

    def _file_out_body_tag(self, file_id):
        return f"file_out_body_{file_id}"

    def _is_file_expanded(self, file_id):
        return self._file_expanded.get(file_id, True)

    def _is_file_tx_expanded(self, file_id):
        return self._file_tx_expanded.get(file_id, False)

    def _is_file_out_expanded(self, file_id):
        return self._file_out_expanded.get(file_id, True)

    def _configure_file_body_elide(self, file_id, expanded):
        body = self._file_body_tag(file_id)
        try:
            self.log_box.tag_config(body, elide=not expanded)
        except tk.TclError:
            pass

    def _configure_file_tx_elide(self, file_id, expanded):
        try:
            self.log_box.tag_config(self._file_tx_body_tag(file_id), elide=not expanded)
        except tk.TclError:
            pass

    def _configure_file_out_elide(self, file_id, expanded):
        try:
            self.log_box.tag_config(
                self._file_out_body_tag(file_id), elide=not expanded
            )
        except tk.TclError:
            pass

    def _set_file_expanded(self, file_id, expanded):
        """Згорнути/розгорнути тіло file-блоку; оновити ▶/▼ у заголовку."""
        if not file_id or self.log_box is None:
            return
        expanded = bool(expanded)
        self._file_expanded[file_id] = expanded
        self._configure_file_body_elide(file_id, expanded)
        header_tag = self._file_header_tag(file_id)
        ranges = self.log_box.tag_ranges(header_tag)
        if len(ranges) < 2:
            return
        entry = self._store.get_file(file_id)
        if not entry:
            return
        day_key = entry.get("day") or today_key()
        block_tag = self._file_block_tag(file_id)
        body_day = self._day_body_tag(day_key)
        header_text = self._format_file_header_line(entry, expanded)
        self.log_box.config(state="normal")
        self.log_box.delete(ranges[0], ranges[1])
        self.log_box.insert(
            ranges[0],
            header_text,
            (body_day, block_tag, "file_header", header_tag),
        )
        self.log_box.config(state="disabled")

    def _set_file_tx_expanded(self, file_id, expanded):
        if not file_id or self.log_box is None:
            return
        expanded = bool(expanded)
        self._file_tx_expanded[file_id] = expanded
        self._configure_file_tx_elide(file_id, expanded)
        entry = self._store.get_file(file_id)
        if not entry:
            return
        header_tag = self._file_tx_header_tag(file_id)
        ranges = self.log_box.tag_ranges(header_tag)
        if len(ranges) < 2:
            return
        day_key = entry.get("day") or today_key()
        segs = entry.get("segments") or {}
        count = int(segs.get("count") or 0)
        mark = "▼" if expanded else "▶"
        text = "   " + t("log_file_tx_header", mark=mark, count=count) + "\n"
        tags = (
            self._day_body_tag(day_key),
            self._file_block_tag(file_id),
            self._file_body_tag(file_id),
            "file_section_header",
            header_tag,
        )
        self.log_box.config(state="normal")
        self.log_box.delete(ranges[0], ranges[1])
        self.log_box.insert(ranges[0], text, tags)
        self.log_box.config(state="disabled")

    def _set_file_out_expanded(self, file_id, expanded):
        if not file_id or self.log_box is None:
            return
        expanded = bool(expanded)
        self._file_out_expanded[file_id] = expanded
        self._configure_file_out_elide(file_id, expanded)
        entry = self._store.get_file(file_id)
        if not entry:
            return
        header_tag = self._file_out_header_tag(file_id)
        ranges = self.log_box.tag_ranges(header_tag)
        if len(ranges) < 2:
            return
        day_key = entry.get("day") or today_key()
        mark = "▼" if expanded else "▶"
        text = "   " + t("log_file_outputs_header", mark=mark) + "\n"
        tags = (
            self._day_body_tag(day_key),
            self._file_block_tag(file_id),
            self._file_body_tag(file_id),
            "file_section_header",
            header_tag,
        )
        self.log_box.config(state="normal")
        self.log_box.delete(ranges[0], ranges[1])
        self.log_box.insert(ranges[0], text, tags)
        self.log_box.config(state="disabled")

    def _render_file_entry(self, entry, insert_new=False):
        if self.log_box is None or not entry:
            return
        day_key = entry.get("day") or today_key()
        self._ensure_day_ui(day_key)
        # Згорнутий день: дані вже в store; UI з’явиться при розгортанні
        if not self._day_expanded.get(day_key, False):
            return
        if not self._day_body_loaded.get(day_key):
            self._load_day_body(day_key)
            return

        file_id = entry.get("id")
        block_tag = self._file_block_tag(file_id)
        self._file_block_tags[file_id] = block_tag
        if file_id not in self._file_expanded:
            self._file_expanded[file_id] = True
        if file_id not in self._file_tx_expanded:
            self._file_tx_expanded[file_id] = False
        if file_id not in self._file_out_expanded:
            self._file_out_expanded[file_id] = True

        if not insert_new:
            ranges = self.log_box.tag_ranges(block_tag)
            if len(ranges) >= 2:
                self.log_box.config(state="normal")
                self.log_box.delete(ranges[0], ranges[1])
                self._insert_file_block(entry, index=ranges[0], day_key=day_key)
                self._trim_if_needed()
                self.log_box.config(state="disabled")
                return

        self.log_box.config(state="normal")
        self._insert_file_block(entry, index="end", day_key=day_key)
        self._trim_if_needed()
        self.log_box.config(state="disabled")

    def _insert_file_block(self, entry, index="end", day_key=None):
        """Insert rendered file block at index; returns index after the block."""
        box = self.log_box
        day_key = day_key or entry.get("day") or today_key()
        file_id = entry.get("id")
        if file_id not in self._file_expanded:
            self._file_expanded[file_id] = True
        if file_id not in self._file_tx_expanded:
            self._file_tx_expanded[file_id] = False
        if file_id not in self._file_out_expanded:
            self._file_out_expanded[file_id] = True
        self._file_block_tags[file_id] = self._file_block_tag(file_id)

        block_tag = self._file_block_tag(file_id)
        header_tag = self._file_header_tag(file_id)
        body_tag = self._file_body_tag(file_id)
        body_day = self._day_body_tag(day_key)
        action_tag = f"file_action_{file_id}"
        expanded = self._is_file_expanded(file_id)
        tx_expanded = self._is_file_tx_expanded(file_id)
        out_expanded = self._is_file_out_expanded(file_id)
        self._configure_file_body_elide(file_id, expanded)
        self._configure_file_tx_elide(file_id, tx_expanded)
        self._configure_file_out_elide(file_id, out_expanded)

        header_text = self._format_file_header_line(entry, expanded)
        cursor = index
        box.insert(
            cursor,
            header_text,
            (body_day, block_tag, "file_header", header_tag),
        )
        cursor = box.index(f"{cursor}+{len(header_text)}c")

        base_body = [body_day, block_tag, body_tag]

        # 3.1 Transcription
        segs = entry.get("segments") or {}
        count = int(segs.get("count") or 0)
        last = segs.get("last") or []
        if count or last:
            mark = "▼" if tx_expanded else "▶"
            tx_h = "   " + t("log_file_tx_header", mark=mark, count=count) + "\n"
            box.insert(
                cursor,
                tx_h,
                tuple(
                    base_body
                    + ["file_section_header", self._file_tx_header_tag(file_id)]
                ),
            )
            cursor = box.index(f"{cursor}+{len(tx_h)}c")
            tx_body_tag = self._file_tx_body_tag(file_id)
            for prev in last:
                t_str = prev.get("t") or ""
                txt = (prev.get("text") or "").strip()
                line = f"      [{t_str}] {txt}\n"
                box.insert(cursor, line, tuple(base_body + [tx_body_tag]))
                cursor = box.index(f"{cursor}+{len(line)}c")

        # 3.2 Created files (+ окрема кнопка промтів поза списком файлів)
        outputs = entry.get("outputs") or []
        has_prompt = self._file_action_callbacks.get(file_id) is not None
        if outputs or has_prompt:
            mark = "▼" if out_expanded else "▶"
            out_h = "   " + t("log_file_outputs_header", mark=mark) + "\n"
            box.insert(
                cursor,
                out_h,
                tuple(
                    base_body
                    + ["file_section_header", self._file_out_header_tag(file_id)]
                ),
            )
            cursor = box.index(f"{cursor}+{len(out_h)}c")
            out_body_tag = self._file_out_body_tag(file_id)
            for out in outputs:
                role = out.get("role") or "file"
                path = out.get("path") or ""
                label = out.get("label")
                role_key = _ROLE_I18N.get(role)
                if role == "ai":
                    role_txt = t(
                        "log_file_output_ai",
                        label=label or os.path.basename(path),
                    )
                elif role == "docx":
                    role_txt = t("log_file_output_docx")
                elif role_key:
                    role_txt = t(role_key)
                else:
                    role_txt = f"{role}:"
                line = f"      {role_txt.rstrip()}\n"
                box.insert(cursor, line, tuple(base_body + [out_body_tag]))
                cursor = box.index(f"{cursor}+{len(line)}c")
                if path:
                    pline = f"      {path}\n"
                    box.insert(
                        cursor,
                        pline,
                        tuple(base_body + [out_body_tag, "link"]),
                    )
                    cursor = box.index(f"{cursor}+{len(pline)}c")
            # Кнопка промтів — окремо від згортання списку файлів
            if has_prompt:
                btn = "      " + t("log_file_select_prompt_btn") + "\n"
                box.insert(
                    cursor,
                    btn,
                    tuple(base_body + ["action", action_tag]),
                )
                cursor = box.index(f"{cursor}+{len(btn)}c")

        if entry.get("error"):
            err = "   " + t("error_occurred", error=entry["error"]) + "\n"
            box.insert(cursor, err, tuple(base_body))
            cursor = box.index(f"{cursor}+{len(err)}c")

        for ev in entry.get("events") or []:
            text = ev.get("text") or ""
            if not text:
                continue
            tag = ev.get("tag")
            # Action / «файли створено» — секція Created files + кнопка промтів
            if tag == "action" or self._is_files_created_event(text):
                continue
            if not text.endswith("\n"):
                text += "\n"
            display = (
                text if text.startswith(" ") or text.startswith("\n") else "   " + text
            )
            tags = list(base_body)
            if tag == "link":
                tags.append("link")
            box.insert(cursor, display, tuple(tags))
            cursor = box.index(f"{cursor}+{len(display)}c")

        return cursor

    @staticmethod
    def _is_files_created_event(text):
        low = (text or "").lower()
        return any(m in low for m in _FILES_CREATED_MARKERS)

    def _format_file_header_line(self, entry, expanded=True):
        name = entry.get("name") or os.path.basename(entry.get("source") or "") or "?"
        status = entry.get("status") or "running"
        idx = entry.get("index") or {}
        current, total = idx.get("current"), idx.get("total")
        status_key = f"log_file_status_{status}"
        status_label = t(status_key)
        if status_label == status_key:
            status_label = status
        mark = "▼" if expanded else "▶"
        if current is not None and total is not None:
            title = t(
                "log_file_header_indexed",
                mark=mark,
                current=current,
                total=total,
                name=name,
                status=status_label,
            )
        else:
            title = t(
                "log_file_header",
                mark=mark,
                name=name,
                status=status_label,
            )
        return title + "\n"

    def _trim_if_needed(self):
        try:
            index_str = self.log_box.index("end-1c")
            line_count = int(index_str.split(".")[0])
        except (ValueError, tk.TclError, IndexError):
            line_count = 0
        if line_count > LOG_MAX_LINES:
            self.log_box.delete("1.0", f"{line_count - LOG_MAX_LINES}.0")

    def _scroll_to_end_if_today(self):
        if self.log_box is None:
            return
        if not self._day_expanded.get(today_key(), True):
            return
        try:
            self.log_box.see("end")
        except tk.TclError:
            pass

    # --- Events --------------------------------------------------------------

    def on_day_header_click(self, event):
        idx = self.log_box.index(f"@{event.x},{event.y}")
        day_key = None
        for tag in self.log_box.tag_names(idx):
            if tag.startswith("day_header_") and tag != "day_header":
                day_key = tag[len("day_header_") :]
                break
        if not day_key:
            return "break"
        expanded = not self._day_expanded.get(day_key, False)
        self._set_day_expanded(day_key, expanded)
        if expanded and day_key == today_key():
            self._scroll_to_end_if_today()
        return "break"

    def on_file_header_click(self, event):
        idx = self.log_box.index(f"@{event.x},{event.y}")
        file_id = None
        for tag in self.log_box.tag_names(idx):
            if tag.startswith("file_header_") and tag != "file_header":
                file_id = tag[len("file_header_") :]
                break
        if not file_id:
            return "break"
        expanded = not self._is_file_expanded(file_id)
        self._set_file_expanded(file_id, expanded)
        return "break"

    def on_file_section_header_click(self, event):
        idx = self.log_box.index(f"@{event.x},{event.y}")
        for tag in self.log_box.tag_names(idx):
            if tag.startswith("file_tx_header_"):
                file_id = tag[len("file_tx_header_") :]
                self._set_file_tx_expanded(
                    file_id, not self._is_file_tx_expanded(file_id)
                )
                return "break"
            if tag.startswith("file_out_header_"):
                file_id = tag[len("file_out_header_") :]
                self._set_file_out_expanded(
                    file_id, not self._is_file_out_expanded(file_id)
                )
                return "break"
        return "break"

    def _link_range_at(self, idx):
        if "link" not in self.log_box.tag_names(idx):
            return None
        ranges = self.log_box.tag_ranges("link")
        for i in range(0, len(ranges), 2):
            start, end = ranges[i], ranges[i + 1]
            if self.log_box.compare(start, "<=", idx) and self.log_box.compare(
                idx, "<", end
            ):
                return start, end
        return (
            self.log_box.index(f"{idx} linestart"),
            self.log_box.index(f"{idx} lineend"),
        )

    def on_link_click(self, event):
        if event.state & 0x0001:
            return self.on_link_shift_click(event)
        idx = self.log_box.index(f"@{event.x},{event.y}")
        rng = self._link_range_at(idx)
        if not rng:
            return "break"
        path = self.log_box.get(rng[0], rng[1]).strip().strip('"')
        if path:
            open_file(path)
        return "break"

    def on_link_shift_click(self, event):
        idx = self.log_box.index(f"@{event.x},{event.y}")
        rng = self._link_range_at(idx)
        if not rng:
            return "break"
        path = self.log_box.get(rng[0], rng[1]).strip().strip('"')
        if not path:
            return "break"
        ok = open_file_location(path)
        if not ok:
            parent = os.path.dirname(path)
            if parent and os.path.isdir(parent):
                open_file_location(parent)
        return "break"

    def on_action_click(self, event):
        idx = self.log_box.index(f"@{event.x},{event.y}")
        for tag in self.log_box.tag_names(idx):
            cb = self._action_callbacks.get(tag)
            if cb is not None:
                try:
                    cb()
                except Exception:
                    pass
                return "break"
            if tag.startswith("file_action_"):
                fid = tag[len("file_action_") :]
                cb = self._file_action_callbacks.get(fid)
                if cb is not None:
                    try:
                        cb()
                    except Exception:
                        pass
                    return "break"
        return "break"

    def _copy_event(self, event=None):
        self.copy_selection()
        return "break"

    def copy_selection(self):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.log_box.selection_get())
        except tk.TclError:
            pass
