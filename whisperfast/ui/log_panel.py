"""Інтерактивний лог з групуванням по днях (Text + app_log.json)."""

from __future__ import annotations

import os
import uuid
import tkinter as tk

from whisperfast.i18n import t
from whisperfast.log_store import LogStore, today_key
from whisperfast.open_path import open_file, open_file_location
from whisperfast.ui.widgets import LOG_MAX_LINES


class LogPanel:
    """Керує ScrolledText-логом: store, дні, link/action, меню копіювання."""

    def __init__(self, root):
        self.root = root
        self.log_box = None
        self.log_menu = None
        self._store = LogStore()
        self._action_callbacks = {}
        self._day_expanded = {}
        self._ui_day = None

    def bind_widget(self, log_box):
        self.log_box = log_box

    def log(self, msg, tag=None):
        text = str(msg) + ("" if str(msg).endswith("\n") else "\n")
        entry = self._store.append(text, tag=tag)

        def _do_log():
            self._append_entry_ui(entry.get("day") or today_key(), text, tag)
            self._scroll_to_end_if_today()

        self.root.after(0, _do_log)

    def log_action(self, msg, callback):
        """Клікабельний рядок лога (синій, як link), викликає callback."""
        text = str(msg) + ("" if str(msg).endswith("\n") else "\n")
        entry = self._store.append(text, tag="action")
        action_tag = f"action_{uuid.uuid4().hex}"

        def _do_log():
            self._action_callbacks[action_tag] = callback
            self._append_entry_ui(
                entry.get("day") or today_key(),
                text,
                "action",
                extra_tags=(action_tag,),
            )
            self._scroll_to_end_if_today()

        self.root.after(0, _do_log)

    def clear(self):
        self._store.clear()
        self._action_callbacks.clear()
        self._day_expanded.clear()
        self._ui_day = None
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
        self.log_menu = tk.Menu(self.root, tearoff=0)
        self.log_menu.add_command(label=t("copy"), command=self.copy_selection)
        box.bind("<Button-3>", lambda e: self.log_menu.tk_popup(e.x_root, e.y_root))
        # Ctrl+C / <<Copy>> — будь-яка розкладка (Windows).
        box.bind("<Control-c>", self._copy_event)
        box.bind("<<Copy>>", self._copy_event)

    def reload_from_store(self):
        """Побудувати лог з JSON: минулі дні згорнуті, сьогодні розгорнутий."""
        self._action_callbacks.clear()
        self._day_expanded.clear()
        self._ui_day = None
        box = self.log_box
        box.config(state="normal")
        box.delete("1.0", "end")
        box.config(state="disabled")

        today = today_key()
        for day_key in self._store.day_keys():
            expanded = day_key == today
            self._day_expanded[day_key] = expanded
            self._configure_day_body_elide(day_key, expanded)
            box.config(state="normal")
            box.insert(
                "end",
                self._day_header_text(day_key, expanded),
                ("day_header", self._day_header_tag(day_key)),
            )
            for entry in self._store.get_entries(day_key):
                text = entry.get("text") or ""
                if not text:
                    continue
                if not text.endswith("\n"):
                    text += "\n"
                tag = entry.get("tag")
                tags = [self._day_body_tag(day_key)]
                if tag == "link":
                    tags.append("link")
                box.insert("end", text, tuple(tags))
            box.config(state="disabled")
            self._ui_day = day_key

        if today not in self._day_expanded:
            self._ensure_day_ui(today)

        self._scroll_to_end_if_today()

    def update_copy_menu_label(self):
        try:
            if self.log_menu is not None:
                self.log_menu.entryconfig(0, label=t("copy"))
        except (tk.TclError, IndexError):
            pass

    def _day_body_tag(self, day_key):
        return f"day_body_{day_key}"

    def _day_header_tag(self, day_key):
        return f"day_header_{day_key}"

    def _day_header_text(self, day_key, expanded):
        mark = "▼" if expanded else "▶"
        if day_key == today_key():
            return t("log_day_header_today", mark=mark, date=day_key) + "\n"
        return t("log_day_header", mark=mark, date=day_key) + "\n"

    def _configure_day_body_elide(self, day_key, expanded):
        body = self._day_body_tag(day_key)
        try:
            self.log_box.tag_config(body, elide=not expanded)
        except tk.TclError:
            pass

    def _set_day_expanded(self, day_key, expanded):
        """Розгорнути/згорнути день. «Сьогодні» завжди лишається розгорнутим."""
        if day_key == today_key():
            expanded = True
        self._day_expanded[day_key] = bool(expanded)
        self._configure_day_body_elide(day_key, expanded)
        header_tag = self._day_header_tag(day_key)
        ranges = self.log_box.tag_ranges(header_tag)
        if len(ranges) >= 2:
            self.log_box.config(state="normal")
            self.log_box.delete(ranges[0], ranges[1])
            self.log_box.insert(
                ranges[0],
                self._day_header_text(day_key, expanded),
                ("day_header", header_tag),
            )
            self.log_box.config(state="disabled")

    def _ensure_day_ui(self, day_key):
        """Гарантує заголовок дня в Text; при новому дні — згортає інші."""
        today = today_key()

        if day_key not in self._day_expanded:
            for d, exp in list(self._day_expanded.items()):
                if exp and d != day_key:
                    self._set_day_expanded(d, False)
            expanded = day_key == today
            self._day_expanded[day_key] = expanded
            self._configure_day_body_elide(day_key, expanded)
            self.log_box.config(state="normal")
            self.log_box.insert(
                "end",
                self._day_header_text(day_key, expanded),
                ("day_header", self._day_header_tag(day_key)),
            )
            self.log_box.config(state="disabled")
            self._ui_day = day_key
            return

        if day_key == today and not self._day_expanded.get(day_key, False):
            self._set_day_expanded(day_key, True)
        self._ui_day = day_key

    def _append_entry_ui(self, day_key, text, tag=None, extra_tags=()):
        if self.log_box is None:
            return
        self._ensure_day_ui(day_key)
        if day_key == today_key() and not self._day_expanded.get(day_key, False):
            self._set_day_expanded(day_key, True)
        tags = [self._day_body_tag(day_key)]
        if tag:
            tags.append(tag)
        tags.extend(extra_tags)
        self.log_box.config(state="normal")
        self.log_box.insert("end", text, tuple(tags))
        try:
            index_str = self.log_box.index("end-1c")
            line_count = int(index_str.split(".")[0])
        except (ValueError, tk.TclError, IndexError):
            line_count = 0
        if line_count > LOG_MAX_LINES:
            self.log_box.delete("1.0", f"{line_count - LOG_MAX_LINES}.0")
        self.log_box.config(state="disabled")

    def _scroll_to_end_if_today(self):
        if self.log_box is None:
            return
        try:
            self.log_box.see("end")
        except tk.TclError:
            pass

    def on_day_header_click(self, event):
        idx = self.log_box.index(f"@{event.x},{event.y}")
        day_key = None
        for tag in self.log_box.tag_names(idx):
            if tag.startswith("day_header_") and tag != "day_header":
                day_key = tag[len("day_header_") :]
                break
        if not day_key:
            return
        if day_key == today_key():
            self._set_day_expanded(day_key, True)
            self._scroll_to_end_if_today()
            return
        expanded = not self._day_expanded.get(day_key, False)
        self._set_day_expanded(day_key, expanded)

    def _link_range_at(self, idx):
        """Повертає (start, end) діапазону tag «link», що містить idx, або None."""
        if "link" not in self.log_box.tag_names(idx):
            return None
        ranges = self.log_box.tag_ranges("link")
        for i in range(0, len(ranges), 2):
            start, end = ranges[i], ranges[i + 1]
            if self.log_box.compare(start, "<=", idx) and self.log_box.compare(idx, "<", end):
                return start, end
        return (
            self.log_box.index(f"{idx} linestart"),
            self.log_box.index(f"{idx} lineend"),
        )

    def on_link_click(self, event):
        idx = self.log_box.index(f"@{event.x},{event.y}")
        rng = self._link_range_at(idx)
        if not rng:
            return "break"
        path = self.log_box.get(rng[0], rng[1]).strip().strip('"')
        if not path:
            return "break"
        if event.state & 0x0001:
            ok = open_file_location(path)
            if not ok:
                parent = os.path.dirname(path)
                if parent and os.path.isdir(parent):
                    open_file_location(parent)
        else:
            open_file(path)
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
                return

    def _copy_event(self, event=None):
        self.copy_selection()
        return "break"

    def copy_selection(self):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.log_box.selection_get())
        except tk.TclError:
            pass
