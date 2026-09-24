"""UI widgets shared by the main window."""
import tkinter as tk

from whisperfast.i18n import t

# Задержка показа подсказки (мс)
TOOLTIP_DELAY_MS = 1000

# Ширина, под которую спроектирован интерфейс; при меньшей ширине окна масштаб уменьшается
UI_DESIGN_WIDTH = 1050
UI_MIN_SCALE = 0.5
UI_BASE_FONT_SIZE = 9
LOG_MAX_LINES = 10000  # ограничение размера лога для длинных сессий


def placeholder_entry(parent, variable, placeholder_key, secret=False, width=None):
    """Empty field shows a grey example. The example is not written into the variable."""
    kwargs = {}
    if width is not None:
        kwargs["width"] = width
    entry = tk.Entry(
        parent,
        relief="flat",
        bd=1,
        highlightthickness=1,
        highlightbackground="#c8c8c8",
        highlightcolor="#5b8def",
        **kwargs,
    )
    entry._is_hint = False

    def paint_hint():
        entry._is_hint = True
        entry.configure(show="", fg="#8a8a8a")
        entry.delete(0, tk.END)
        entry.insert(0, t(placeholder_key))

    def paint_value(value):
        entry._is_hint = False
        entry.configure(show="*" if secret else "", fg="#1a1a1a")
        entry.delete(0, tk.END)
        entry.insert(0, value)

    def refresh(*_args):
        if entry.focus_get() is entry and not entry._is_hint:
            return
        value = variable.get() or ""
        if str(value).strip():
            paint_value(str(value))
        elif not entry._is_hint:
            paint_hint()

    def commit(_event=None):
        if entry._is_hint:
            return
        variable.set(entry.get())

    def on_focus_in(_event):
        if not entry._is_hint:
            return
        entry._is_hint = False
        entry.configure(show="*" if secret else "", fg="#1a1a1a")
        entry.delete(0, tk.END)

    def on_focus_out(_event):
        commit()
        if not str(variable.get() or "").strip():
            variable.set("")
            paint_hint()

    entry.bind("<FocusIn>", on_focus_in)
    entry.bind("<FocusOut>", on_focus_out)
    entry.bind("<KeyRelease>", commit)
    entry.bind("<<Paste>>", lambda _event: entry.after(10, commit))
    variable.trace_add("write", refresh)
    refresh()
    return entry


class Tooltip:
    """Подсказка при наведении на виджет. text — готовый текст или ключ перевода (если is_key=True)."""
    def __init__(self, widget, text, delay_ms=TOOLTIP_DELAY_MS, is_key=False):
        self.widget = widget
        self._text_or_key = text
        self._is_key = is_key
        self.delay_ms = delay_ms
        self._job = None
        self._tw = None
        self.widget.bind("<Enter>", self._on_enter)
        self.widget.bind("<Leave>", self._on_leave)

    def _on_enter(self, event=None):
        self._job = self.widget.after(self.delay_ms, self._show)

    def _on_leave(self, event=None):
        if self._job:
            self.widget.after_cancel(self._job)
            self._job = None
        self._hide()

    def _show(self):
        self._job = None
        text = t(self._text_or_key) if self._is_key else self._text_or_key
        if not text:
            return
        self._tw = tk.Toplevel(self.widget)
        self._tw.wm_overrideredirect(True)
        self._tw.wm_geometry("+0+0")
        label = tk.Label(
            self._tw,
            text=text,
            justify="left",
            background="#ffffc0",
            relief="solid",
            borderwidth=1,
            font=("Segoe UI", 9),
            padx=6,
            pady=4,
            wraplength=420,
        )
        label.pack()
        self._tw.update_idletasks()
        # Позиция: под виджетом, выравнивание по левому краю
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 2
        w = label.winfo_reqwidth()
        h = label.winfo_reqheight()
        self._tw.wm_geometry(f"+{x}+{y}")
        # Не уходить за правый край экрана
        root = self.widget.winfo_toplevel()
        max_x = root.winfo_rootx() + root.winfo_width()
        if x + w > max_x:
            x = max(0, max_x - w - 4)
            self._tw.wm_geometry(f"+{x}+{y}")

    def _hide(self):
        if self._tw:
            try:
                self._tw.destroy()
            except tk.TclError:
                pass
            self._tw = None
