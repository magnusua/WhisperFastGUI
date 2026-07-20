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
