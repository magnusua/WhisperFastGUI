import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

# Импорт pydub с обработкой ошибок для Python 3.13+
try:
    from pydub import AudioSegment
except ImportError as e:
    if "audioop" in str(e) or "pyaudioop" in str(e):
        # Импортируем lang_manager для перевода (если доступен)
        try:
            from lang_manager import t
            error_msg = (
                f"{t('error')}: Не удалось импортировать pydub.\n\n"
                f"Для Python {sys.version_info.major}.{sys.version_info.minor} требуется pyaudioop.\n\n"
                f"Установите его командой:\n"
                f"pip install pyaudioop\n\n"
                f"Или используйте кнопку [{t('dependencies')}] для автоматической установки."
            )
            error_title = t("error")
        except ImportError:
            from i18n_fallback import t
            error_msg = (
                f"{t('error')}: Не удалось импортировать pydub.\n\n"
                f"Для Python {sys.version_info.major}.{sys.version_info.minor} требуется pyaudioop.\n\n"
                f"Установите его командой:\n pip install pyaudioop\n\n"
                f"Или используйте кнопку [{t('dependencies')}] для автоматической установки."
            )
            error_title = t("error")
        from tkinter import messagebox as mb
        mb.showerror(error_title, error_msg)
        sys.exit(1)
    else:
        raise

# Импорт модулей проекта
from config import (
    APP_VERSION, APP_DATE, BASE_DIR, load_help_text,
    LANG_AUTO_VALUE, SUPPORTED_LANGUAGES, VALID_EXTS,
    AUDIO_EXTENSIONS, DEFAULT_START_TIMESTAMP, DEFAULT_MODEL,
    WHISPER_MODELS, get_whisper_cache_dir, find_whisper_model_cache_path,
    PROGRESS_UPDATE_INTERVAL_S, LOG_UPDATE_INTERVAL_S, FULL_VIDEO_SEGMENT_EPS_S,
)
from utils import (
    format_timestamp, format_timestamp_srt, format_timestamp_filename,
    play_finish_sound, get_audio_duration_seconds, parse_timestamp_to_seconds,
    make_queue_item, normalize_queue_path,
)
from model_manager import WhisperModelSingleton
from installer import install_dependencies, check_system, check_updates
from input_files import (
    add_multiple_files,
    add_directory,
    process_dropped_files,
    add_files_to_queue_controller
)
from i18n import t, set_language, get_language
from lang_manager import load_app_settings, save_app_settings


class _SegmentOffset:
    """Сегмент с полями start, end, text (для смещения времени при обработке куска файла)."""
    __slots__ = ("start", "end", "text")
    def __init__(self, start, end, text):
        self.start = start
        self.end = end
        self.text = text

# Попытка импорта Drag & Drop
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_OK = True
except ImportError:
    DND_OK = False

# Иконка в системном трее (область уведомлений)
try:
    import pystray
    from pystray import MenuItem as TrayMenuItem
    from PIL import Image
    TRAY_OK = True
except ImportError:
    TRAY_OK = False

# Базовый класс окна зависит от наличия tkinterdnd2
BaseTk = TkinterDnD.Tk if DND_OK else tk.Tk

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


class WhisperGUI:
    def __init__(self, root, on_close_request=None, on_close_factory=None):
        self.root = root
        # callback для закрытия из трея или по X; можно задать напрямую или через factory(root, app)
        if on_close_factory is not None:
            self._on_close_request = on_close_factory(root, self)
        else:
            self._on_close_request = on_close_request
        self._tray_icon = None  # pystray Icon, останавливается в prepare_close

        self.root.title(t("app_title"))
        self.root.geometry("1050x950")
        self.root.minsize(400, 400)

        # Кастомная иконка окна и панели задач (favicon.ico); пути через config.BASE_DIR
        self._icon_path = os.path.join(BASE_DIR, "favicon.ico")
        if os.path.exists(self._icon_path):
            try:
                self.root.iconbitmap(self._icon_path)
            except Exception:
                pass

        # Состояние приложения: очередь — список dict (path, start, end_segment_1, end_segment_2, end)
        self.queue = []
        self._request_queue_file = os.path.join(BASE_DIR, "request_queue.json")
        self.cancel_requested = False
        self._process_queue_lock = threading.Lock()  # только одна обработка очереди одновременно
        
        # Переменные интерфейса
        self.device_mode = tk.StringVar(value="AUTO")
        self.lang_mode = tk.StringVar(value=LANG_AUTO_VALUE)  # AUTO для языка транскрипции
        self.output_dir = tk.StringVar()
        self.watch_dir = tk.StringVar()
        self.watch_enabled = tk.BooleanVar(value=False)
        self._watch_stop = threading.Event()
        self._watch_thread = None
        self._watch_seen = set()  # уже учтённые файлы в каталоге слежения
        self.play_sound_on_finish = tk.BooleanVar(value=False)  # По умолчанию снят
        self.save_audio_mp3 = tk.BooleanVar(value=False)  # Сохранять извлечённое аудио в MP3
        self.tray_mode = tk.StringVar(value="panel")  # "panel" | "tray" | "panel_tray"
        self.whisper_model = tk.StringVar(value=DEFAULT_MODEL)
        
        # Загружаем сохранённые налаштування з settings.json
        saved = load_app_settings()
        saved_language = saved.get("language", "EN")
        self.output_dir.set(saved.get("output_dir", "") or "")
        self.watch_dir.set(saved.get("watch_dir", "") or "")
        self.watch_enabled.set(bool(saved.get("watch_enabled", False)))
        self.device_mode.set(saved.get("device_mode", "AUTO"))
        self.play_sound_on_finish.set(bool(saved.get("play_sound_on_finish", False)))
        self.save_audio_mp3.set(bool(saved.get("save_audio_mp3", False)))
        self.tray_mode.set(saved.get("tray_mode", "panel"))
        self.whisper_model.set(saved.get("whisper_model", DEFAULT_MODEL) or DEFAULT_MODEL)
        
        # Загружаем сохраненный язык или используем EN по умолчанию
        self.ui_language = tk.StringVar(value=saved_language)  # Язык интерфейса
        
        # Устанавливаем начальный язык
        set_language(saved_language)
        
        # Привязываем изменение языка к обновлению UI
        self.ui_language.trace("w", lambda *args: self.on_language_change())

        self.build_ui()
        self.setup_log_styles()

        # Центрирование окна по экрану
        self.root.update_idletasks()
        win_w, win_h = 1050, 950
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x = max(0, (sw - win_w) // 2)
        y = max(0, (sh - win_h) // 2)
        self.root.geometry(f"{win_w}x{win_h}+{x}+{y}")

        # Масштабирование при изменении размера окна
        self.root.bind("<Configure>", self._on_configure)
        self._last_scale_width = None
        self._apply_ui_scale(1.0)

        # Закриття вікна обробляється в main.py (on_app_closing); налаштування зберігаються через _persist_settings()

        # Якщо слідкування було увімкнено — запускаємо після побудови UI
        if self.watch_enabled.get():
            watch_path = (self.watch_dir.get() or "").strip()
            if watch_path and os.path.isdir(watch_path):
                self._start_watch(watch_path)

        if not DND_OK:
            self.log(t("warning_dnd"))

        # Загрузка очереди из request_queue.json; при первом запуске создаём пустой файл
        self._load_queue_from_file()
        if not os.path.exists(self._request_queue_file):
            self._save_queue_to_file()

        # Иконка в системном трее (зависит от переключателя Панель / Трей / Панель + Трей)
        self._apply_tray_mode()

    TRAY_MODE_KEYS = ("panel", "tray", "panel_tray")

    def _setup_tray(self):
        """Запуск иконки в системном трее (если доступны pystray и Pillow). Не создаёт трей в режиме «Панель»."""
        if self.tray_mode.get() == "panel":
            return
        if not TRAY_OK:
            self.log(t("warning_tray_unavailable"))
            return
        if self._tray_icon:
            return
        width, height = 64, 64
        img = None
        if os.path.exists(self._icon_path):
            try:
                img = Image.open(self._icon_path)
                if img.mode != "RGBA":
                    img = img.convert("RGBA")
                if img.size != (width, height):
                    img = img.resize((width, height), Image.Resampling.LANCZOS)
            except Exception:
                img = None
        if img is None:
            # Резервна іконка, якщо favicon.ico відсутній — простий сірий квадрат
            img = Image.new("RGBA", (width, height), (80, 80, 80, 255))

        def show_window(icon, item):
            self.root.after(0, self._tray_show_window)

        def quit_app(icon, item):
            self.root.after(0, self._tray_quit)

        menu = pystray.Menu(
            TrayMenuItem(t("tray_show_window"), show_window, default=True),
            TrayMenuItem(t("exit"), quit_app),
        )
        self._tray_icon = pystray.Icon("whisper_fast_gui", img, t("app_title"), menu)
        threading.Thread(target=self._tray_icon.run, daemon=True).start()

    def _apply_tray_mode(self):
        """Применяет выбранный режим: Панель (без трея), Трей (только трей), Панель + Трей."""
        mode = self.tray_mode.get()
        if mode == "panel":
            if self._tray_icon:
                try:
                    self._tray_icon.stop()
                except Exception:
                    pass
                self._tray_icon = None
            self.root.deiconify()
        else:
            # Відкладений запуск трею: на Windows іконка часто не з'являється, якщо створювати її до готовності панелі задач
            def delayed_tray():
                self._setup_tray()
                if mode == "tray" and self._tray_icon:
                    self.root.withdraw()
                else:
                    self.root.deiconify()
            self.root.after(500, delayed_tray)

    def _tray_show_window(self):
        """Показать окно из трея (вызывается в main thread)."""
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _tray_quit(self):
        """Закрытие по пункту «Выход» в трее (вызывается в main thread)."""
        if self._on_close_request:
            self._on_close_request()

    def _load_queue_from_file(self):
        """Загружает очередь из request_queue.json и заполняет таблицу (использует make_queue_item и normalize_queue_path)."""
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
            self._refresh_queue_treeview()
        except (json.JSONDecodeError, OSError):
            pass

    def _save_queue_to_file(self):
        """Сохраняет очередь в request_queue.json."""
        try:
            data = [{"path": q["path"], "start": q["start"], "end_segment_1": q.get("end_segment_1", ""),
                    "end_segment_2": q.get("end_segment_2", ""), "end": q["end"],
                    "processed": q.get("processed", False)} for q in self.queue]
            with open(self._request_queue_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def _refresh_queue_treeview(self):
        """Перестраивает таблицу очереди по self.queue. Статус обработано/необработано — в отдельном столбце."""
        self.queue_list.delete(*self.queue_list.get_children())
        for i, q in enumerate(self.queue):
            name = os.path.basename(q["path"])
            status_text = t("status_processed") if q.get("processed") else t("status_not_processed")
            self.queue_list.insert("", "end", values=(
                i + 1, name, q["start"], q.get("end_segment_1", ""), q.get("end_segment_2", ""), q["end"], status_text
            ))

    def _on_queue_row_double_click(self, event):
        """Редактирование диапазона времени по двойному клику по строке."""
        iid = self.queue_list.identify_row(event.y)
        if not iid:
            return
        try:
            idx = self.queue_list.index(iid)
        except tk.TclError:
            return
        if idx < 0 or idx >= len(self.queue):
            return
        row = self.queue[idx]
        d = tk.Toplevel(self.root)
        d.title(t("edit_row_title"))
        d.transient(self.root)
        d.grab_set()
        ttk.Label(d, text=t("col_start")).grid(row=0, column=0, padx=5, pady=3)
        e_start = ttk.Entry(d, width=14)
        e_start.insert(0, row["start"])
        e_start.grid(row=0, column=1, padx=5, pady=3)
        ttk.Label(d, text=t("col_end_seg1")).grid(row=1, column=0, padx=5, pady=3)
        e_seg1 = ttk.Entry(d, width=14)
        e_seg1.insert(0, row.get("end_segment_1", ""))
        e_seg1.grid(row=1, column=1, padx=5, pady=3)
        ttk.Label(d, text=t("col_end_seg2")).grid(row=2, column=0, padx=5, pady=3)
        e_seg2 = ttk.Entry(d, width=14)
        e_seg2.insert(0, row.get("end_segment_2", ""))
        e_seg2.grid(row=2, column=1, padx=5, pady=3)
        ttk.Label(d, text=t("col_end")).grid(row=3, column=0, padx=5, pady=3)
        e_end = ttk.Entry(d, width=14)
        e_end.insert(0, row["end"])
        e_end.grid(row=3, column=1, padx=5, pady=3)

        def apply_and_close():
            self.queue[idx]["start"] = e_start.get().strip() or DEFAULT_START_TIMESTAMP
            self.queue[idx]["end_segment_1"] = e_seg1.get().strip()
            self.queue[idx]["end_segment_2"] = e_seg2.get().strip()
            self.queue[idx]["end"] = e_end.get().strip() or row["end"]
            self._refresh_queue_treeview()
            self._save_queue_to_file()
            d.destroy()

        ttk.Button(d, text=t("close"), command=d.destroy).grid(row=4, column=0, padx=5, pady=8)
        ttk.Button(d, text=t("ok"), command=apply_and_close).grid(row=4, column=1, padx=5, pady=8)
        self._center_toplevel(d)

    def build_ui(self):
        """Создание интерфейса по блокам 1, 2, 3, 4"""
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill="both", expand=True)

        if DND_OK:
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind("<<Drop>>", self.on_drop)

        # === БЛОК 1: ОЧЕРЕДЬ ФАЙЛОВ ===
        header_f = ttk.Frame(main)
        header_f.pack(fill="x", pady=(0, 5))
        
        self.queue_header_label = ttk.Label(header_f, text=t("queue_header"), font=("Segoe UI", 9, "bold"))
        self.queue_header_label.pack(side="left")
        self.add_files_btn = ttk.Button(header_f, text=t("add_files"), command=self.add_files_action)
        self.add_files_btn.pack(side="left", padx=5)
        self.add_directory_btn = ttk.Button(header_f, text=t("add_directory"), command=self.add_directory_action)
        self.add_directory_btn.pack(side="left", padx=5)
        self.clear_queue_btn = ttk.Button(header_f, text=t("clear_queue"), command=self.clear_queue)
        self.clear_queue_btn.pack(side="left", padx=5)
        # Чекбокс «Оповещение» (звук по завершении очереди)
        self.play_sound_check = ttk.Checkbutton(header_f, text=t("play_sound_finish"),
                       variable=self.play_sound_on_finish)
        self.play_sound_check.pack(side="left", padx=5)
        
        # Кнопка Help самая правая
        self.help_btn = ttk.Button(header_f, text=t("help"), width=10, command=self.show_help)
        self.help_btn.pack(side="right")
        
        # Версия и дата слева от переключателя языка
        self.version_label = ttk.Label(
            header_f,
            text=f"v{APP_VERSION} ({APP_DATE})",
            font=("Segoe UI", 9),
        )
        self.version_label.pack(side="right", padx=(0, 10))
        # Переключатель языка слева от Help
        self.lang_selector_frame = ttk.Frame(header_f)
        self.lang_selector_frame.pack(side="right", padx=5)
        ttk.Label(self.lang_selector_frame, text="🌐").pack(side="left", padx=2)
        for lang_code in SUPPORTED_LANGUAGES:
            ttk.Radiobutton(
                self.lang_selector_frame,
                text=lang_code,
                variable=self.ui_language,
                value=lang_code,
                command=self.on_language_change
            ).pack(side="left", padx=2)

        q_frame = ttk.Frame(main)
        q_frame.pack(fill="both", pady=5)
        cols = ("num", "filename", "start", "end_seg1", "end_seg2", "end", "status")
        self.queue_list = ttk.Treeview(q_frame, columns=cols, show="headings", height=8, selectmode="browse")
        self.queue_list.heading("num", text=t("col_num"))
        self.queue_list.heading("filename", text=t("col_filename"))
        self.queue_list.heading("start", text=t("col_start"))
        self.queue_list.heading("end_seg1", text=t("col_end_seg1"))
        self.queue_list.heading("end_seg2", text=t("col_end_seg2"))
        self.queue_list.heading("end", text=t("col_end"))
        self.queue_list.heading("status", text=t("col_status"))
        _num_w = 38
        self.queue_list.column("num", width=_num_w, minwidth=_num_w)
        self.queue_list.column("filename", width=220)
        self.queue_list.column("start", width=90)
        self.queue_list.column("end_seg1", width=90)
        self.queue_list.column("end_seg2", width=90)
        self.queue_list.column("end", width=90)
        self.queue_list.column("status", width=100)
        scroll_q = ttk.Scrollbar(q_frame, orient="vertical", command=self.queue_list.yview)
        self.queue_list.configure(yscrollcommand=scroll_q.set)
        self.queue_list.pack(side="left", fill="both", expand=True, padx=2, pady=2)
        scroll_q.pack(side="right", fill="y")
        self.queue_list.bind("<Double-1>", self._on_queue_row_double_click)
        self.queue_list.bind("<Button-1>", self.on_drag_start)
        self.queue_list.bind("<B1-Motion>", self.on_drag_motion)

        # === БЛОК 2: Переключатель языка слева + кнопка «Начать транскрибацию» ===
        start_f = ttk.Frame(main)
        start_f.pack(fill="x", pady=10)
        self.lang_f = ttk.LabelFrame(start_f, text=t("language_switcher"))
        self.lang_f.pack(side="left", padx=5)
        for l in ["AUTO", "RU", "UK", "EN"]:
            val = l.lower() if l != "AUTO" else LANG_AUTO_VALUE
            ttk.Radiobutton(self.lang_f, text=l, variable=self.lang_mode, value=val).pack(side="left", padx=5)
        self.start_btn = ttk.Button(start_f, text=t("start_transcription"), command=self.handle_start_logic)
        self.start_btn.pack(side="left", fill="x", expand=True, padx=5, ipady=10)

        # Строка: Сохранить Mp3 + каталог сохранения (по центру)
        tools_row = ttk.Frame(main)
        tools_row.pack(fill="x", pady=10)
        ttk.Frame(tools_row).pack(side="left", fill="x", expand=True)
        tools_center = ttk.Frame(tools_row)
        tools_center.pack(side="left")
        self.save_audio_check = ttk.Checkbutton(tools_center, text=t("save_audio_mp3"),
                       variable=self.save_audio_mp3)
        self.save_audio_check.pack(side="left", padx=5)
        ttk.Label(tools_center, text=" | ").pack(side="left", padx=5)
        self.output_dir_entry = ttk.Entry(tools_center, textvariable=self.output_dir, width=45)
        self.output_dir_entry.pack(side="left", padx=2)
        self.root.bind_all("<Return>", self._on_enter_key)
        self.root.bind_all("<space>", self._on_space_key)
        self.output_folder_btn = ttk.Button(tools_center, text=t("output_folder"), command=self.pick_output_folder)
        self.output_folder_btn.pack(side="left", padx=2)
        ttk.Label(tools_center, text=" | ").pack(side="left", padx=5)
        self.watch_folder_check = ttk.Checkbutton(tools_center, text=t("watch_folder_label"), variable=self.watch_enabled, command=self._on_watch_toggled)
        self.watch_folder_check.pack(side="left", padx=5)
        self.watch_dir_entry = ttk.Entry(tools_center, textvariable=self.watch_dir, width=25)
        self.watch_dir_entry.pack(side="left", padx=2)
        self.watch_dir_entry.bind("<Control-v>", self._paste_into_watch_dir)
        self.watch_dir_entry.bind("<FocusOut>", lambda e: self._persist_settings())
        ttk.Frame(tools_row).pack(side="left", fill="x", expand=True)

        # Прогресс
        self.progress = ttk.Progressbar(main, length=900)
        self.progress.pack(fill="x", pady=(10, 5))
        
        # === БЛОК 4: ЛОГ И КНОПКА ОТМЕНЫ (блок «Очистить лог» | Устройство | кнопки — по центру) ===
        log_header = ttk.Frame(main)
        log_header.pack(fill="x", pady=(5, 0))
        ttk.Frame(log_header).pack(side="left", fill="x", expand=True)
        log_center = ttk.Frame(log_header)
        log_center.pack(side="left")
        self.clear_log_btn = ttk.Button(log_center, text=t("clear_log"), command=self.clear_log)
        self.clear_log_btn.pack(side="left")
        ttk.Label(log_center, text=" | ").pack(side="left", padx=5)
        self.dev_f = ttk.LabelFrame(log_center, text=t("device_label"))
        self.dev_f.pack(side="left", padx=5)
        for device in ["AUTO", "GPU", "CPU"]:
            ttk.Radiobutton(self.dev_f, text=device, variable=self.device_mode, value=device).pack(side="left", padx=5)
        self.system_btn = ttk.Button(log_center, text=t("system_check"), command=lambda: check_system(self.log))
        self.system_btn.pack(side="left", padx=2)
        self.updates_btn = ttk.Button(log_center, text=t("updates"), command=self.run_updates_check)
        self.updates_btn.pack(side="left", padx=2)
        self.dependencies_btn = ttk.Button(log_center, text=t("dependencies"), command=self.run_install)
        self.dependencies_btn.pack(side="left", padx=2)
        ttk.Label(log_center, text=" | ").pack(side="left", padx=5)
        self.model_btn = ttk.Button(log_center, text=self._model_button_label(), width=14, command=self._show_model_dialog)
        self.model_btn.pack(side="left", padx=2)
        ttk.Label(log_center, text=" | ").pack(side="left", padx=5)
        self.tray_mode_combo = ttk.Combobox(log_center, state="readonly", width=14, values=[t("tray_mode_panel"), t("tray_mode_tray"), t("tray_mode_panel_tray")])
        self.tray_mode_combo.pack(side="left", padx=2)
        idx = self.TRAY_MODE_KEYS.index(self.tray_mode.get()) if self.tray_mode.get() in self.TRAY_MODE_KEYS else 0
        self.tray_mode_combo.current(idx)
        self.tray_mode_combo.bind("<<ComboboxSelected>>", self._on_tray_mode_change)
        ttk.Label(log_center, text=" | ").pack(side="left", padx=5)
        self.autostart_btn = ttk.Button(log_center, text=t("autostart"), command=self._run_autostart_script)
        self.autostart_btn.pack(side="left", padx=2)
        ttk.Frame(log_header).pack(side="left", fill="x", expand=True)
        self.cancel_btn = ttk.Button(log_header, text=t("cancel"), command=self.cancel_action, state="disabled")
        self.cancel_btn.pack(side="right")
        
        self.log_box = scrolledtext.ScrolledText(main, height=18, state="disabled", wrap="word", font=("Consolas", 9))
        self.log_box.pack(fill="both", expand=True, pady=5)

        self._tooltips = []
        self._setup_tooltips()

    def _setup_tooltips(self):
        """Привязка подсказок к переключателям, кнопкам и полям (задержка 1 с). Ключ перевода — подсказка обновится при смене языка."""
        def tip(widget, key):
            self._tooltips.append(Tooltip(widget, key, is_key=True))
        tip(self.queue_header_label, "tooltip_queue_header")
        tip(self.add_files_btn, "tooltip_add_files")
        tip(self.add_directory_btn, "tooltip_add_directory")
        tip(self.clear_queue_btn, "tooltip_clear_queue")
        tip(self.play_sound_check, "tooltip_play_sound")
        tip(self.help_btn, "tooltip_help")
        tip(self.lang_selector_frame, "tooltip_ui_language")
        tip(self.start_btn, "tooltip_start")
        tip(self.dev_f, "tooltip_device")
        tip(self.lang_f, "tooltip_language_switcher")
        tip(self.save_audio_check, "tooltip_save_mp3")
        tip(self.system_btn, "tooltip_system")
        tip(self.updates_btn, "tooltip_updates")
        tip(self.dependencies_btn, "tooltip_dependencies")
        self._tooltips.append(Tooltip(self.model_btn, t("tooltip_model_btn", cache_dir=get_whisper_cache_dir()), is_key=False))
        tip(self.tray_mode_combo, "tooltip_tray_mode")
        tip(self.autostart_btn, "tooltip_autostart")
        tip(self.output_dir_entry, "tooltip_output_dir")
        tip(self.output_folder_btn, "tooltip_output_folder")
        tip(self.watch_folder_check, "tooltip_watch_folder")
        tip(self.watch_dir_entry, "tooltip_watch_folder")
        tip(self.clear_log_btn, "tooltip_clear_log")
        tip(self.cancel_btn, "tooltip_cancel")

    def _current_scale(self):
        """Коэффициент масштаба по ширине окна (1.0 при ширине >= UI_DESIGN_WIDTH)."""
        try:
            w = self.root.winfo_width()
        except tk.TclError:
            return 1.0
        if w <= 0:
            return 1.0
        return min(1.0, max(UI_MIN_SCALE, w / UI_DESIGN_WIDTH))

    def _on_configure(self, event=None):
        """При изменении размера окна — пересчёт масштаба и обновление шрифтов/размеров."""
        if event is None or event.widget != self.root:
            return
        w = self.root.winfo_width()
        if self._last_scale_width is not None and abs(w - self._last_scale_width) < 20:
            return
        self._last_scale_width = w
        self._apply_ui_scale(self._current_scale())

    def _apply_ui_scale(self, scale):
        """Применяет масштаб к шрифтам и размерам элементов интерфейса."""
        font_size = max(6, int(UI_BASE_FONT_SIZE * scale))
        font = ("Segoe UI", font_size)
        style = ttk.Style()
        for style_name in ("TButton", "TLabel", "TCheckbutton", "TRadiobutton", "TEntry"):
            try:
                style.configure(style_name, font=font)
            except tk.TclError:
                pass
        try:
            style.configure("TLabelframe.Label", font=font)
        except tk.TclError:
            pass
        self.queue_header_label.config(font=("Segoe UI", font_size, "bold"))
        self.version_label.config(font=("Segoe UI", font_size))
        try:
            style = ttk.Style()
            style.configure("Treeview", font=("Consolas", max(6, int(10 * scale))))
        except tk.TclError:
            pass
        self.log_box.config(font=("Consolas", max(6, int(9 * scale))))
        self.progress["length"] = max(200, int(900 * scale))
        self.output_dir_entry.config(width=max(15, int(45 * scale)))

    # --- ЛОГИКА ЗАПУСКА ---

    def _processed_marker(self):
        """Единая строка-маркер обработанного файла в очереди."""
        return t("processed")

    def handle_start_logic(self):
        """Логика выбора режима обработки. При пустой очереди — открыть диалог «Добавить файлы»."""
        if not self.queue:
            self.add_files_action()
            return

        sel = self.queue_list.selection()
        idx = self.queue_list.index(sel[0]) if sel else None

        if idx is not None and 0 <= idx < len(self.queue):
            name = os.path.basename(self.queue[idx]["path"])
            if len(self.queue) == 1:
                self.start_thread(mode="single", target_idx=idx)
                return
            choice = self._show_file_selection_dialog(name)
            if choice == "single":
                self.start_thread(mode="single", target_idx=idx)
                return
            elif choice == "cancel":
                return

        has_processed = any(self.queue[i].get("processed") for i in range(len(self.queue)))
        all_processed = len(self.queue) > 0 and all(self.queue[i].get("processed") for i in range(len(self.queue)))
        if all_processed:
            choice = messagebox.askquestion(t("queue_dialog"), t("process_again"))
            if choice == "yes":
                self.start_thread(mode="all")
            return
        if has_processed:
            choice = messagebox.askquestion(t("queue_dialog"), t("process_only_new"))
            mode = "only_new" if choice == 'yes' else "all"
            self.start_thread(mode=mode)
        else:
            self.start_thread(mode="all")

    def _show_file_selection_dialog(self, filename):
        """
        Показывает диалог выбора режима обработки при выбранном файле.
        
        Args:
            filename: Имя выбранного файла
        
        Returns:
            "single" - только выбранный файл
            "all" - все файлы в очереди
            "cancel" - отмена
        """
        dialog = tk.Toplevel(self.root)
        dialog.title(t("file_selection_title"))
        dialog.geometry("400x150")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        result = {"choice": "cancel"}
        
        # Текст вопроса
        label = ttk.Label(
            dialog, 
            text=t("file_selected", filename=filename),
            font=("Segoe UI", 10)
        )
        label.pack(pady=10)
        
        # Фрейм для кнопок
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=10)
        
        def choose_single():
            result["choice"] = "single"
            dialog.destroy()
        
        def choose_all():
            result["choice"] = "all"
            dialog.destroy()
        
        def choose_cancel():
            result["choice"] = "cancel"
            dialog.destroy()
        
        # Кнопки
        ttk.Button(
            btn_frame, 
            text=t("only_selected"), 
            command=choose_single,
            width=20
        ).pack(side="left", padx=5)
        
        ttk.Button(
            btn_frame, 
            text=t("all_files"), 
            command=choose_all,
            width=20
        ).pack(side="left", padx=5)
        
        ttk.Button(
            btn_frame, 
            text=t("cancel_btn"), 
            command=choose_cancel,
            width=15
        ).pack(side="left", padx=5)
        
        dialog.protocol("WM_DELETE_WINDOW", choose_cancel)
        self._center_toplevel(dialog)
        dialog.wait_window()
        
        return result["choice"]

    def auto_start_queue(self):
        """Запускает обробку всієї черги, якщо вона не порожня (для --transcribe з main.py)."""
        if self.queue:
            self.start_thread(mode="all")

    def start_thread(self, mode, target_idx=None):
        if not self._process_queue_lock.acquire(blocking=False):
            self.log("⚠ " + t("already_processing"))
            return
        self.cancel_requested = False
        self.start_btn.config(state="disabled")
        self.cancel_btn.config(state="normal")
        # Читаем Tk-переменные только в главном потоке и передаём в воркер
        options = {
            "device_mode": self.device_mode.get(),
            "whisper_model": self.whisper_model.get(),
            "lang_mode": self.lang_mode.get(),
            "save_audio_mp3": self.save_audio_mp3.get(),
            "play_sound_on_finish": self.play_sound_on_finish.get(),
            "output_dir": (self.output_dir.get() or "").strip(),
        }

        def run_and_release():
            try:
                self.process_queue(mode, target_idx, options)
            finally:
                self._process_queue_lock.release()
        threading.Thread(target=run_and_release, daemon=True).start()

    def process_queue(self, mode, target_idx, options=None):
        opts = options or {}
        try:
            model = WhisperModelSingleton.get(self.log, opts.get("device_mode", "AUTO"), opts.get("whisper_model", DEFAULT_MODEL))
            # Снимок очереди, чтобы индексы не выходили за границы при изменении очереди в GUI
            queue_snapshot = list(self.queue)
            if mode == "single":
                indices = [target_idx]
            elif mode == "only_new":
                indices = [i for i in range(len(queue_snapshot)) if not queue_snapshot[i].get("processed")]
            else:
                indices = list(range(len(queue_snapshot)))

            done = 0
            to_do = len(indices)
            skipped_paths = []

            for idx in indices:
                if self.cancel_requested:
                    break
                if idx < 0 or idx >= len(queue_snapshot):
                    continue
                row = queue_snapshot[idx]
                path = normalize_queue_path(row.get("path"))
                if not path:
                    continue
                name = os.path.basename(path)
                if not os.path.isfile(path):
                    self.log(f"\n{t('processing', current=done + 1, total=to_do, name=name)}")
                    self.log(t("file_skipped", name=name))
                    skipped_paths.append(path)
                    continue
                self.log(f"\n{t('processing', current=done + 1, total=to_do, name=name)}")

                try:
                    start_sec = parse_timestamp_to_seconds(row.get("start")) or 0.0
                    duration = get_audio_duration_seconds(path) or 1.0
                    end_sec = parse_timestamp_to_seconds(row.get("end")) or duration
                    end_sec = min(end_sec, duration)
                    segment_duration = end_sec - start_sec if end_sec > start_sec else duration

                    audio = None
                    if opts.get("save_audio_mp3"):
                        ext = os.path.splitext(path)[1].lower()
                        is_audio_source = ext in AUDIO_EXTENSIONS
                        if is_audio_source:
                            choice = [None]
                            def ask_save_mp3():
                                choice[0] = messagebox.askyesno(
                                    t("save_audio_mp3"),
                                    t("save_mp3_confirm", filename=os.path.basename(path))
                                )
                            self.root.after(0, ask_save_mp3)
                            while choice[0] is None and not self.cancel_requested:
                                time.sleep(0.05)
                            if choice[0]:
                                full = AudioSegment.from_file(path)
                                audio = full[int(start_sec * 1000):int(end_sec * 1000)]
                        else:
                            full = AudioSegment.from_file(path)
                            audio = full[int(start_sec * 1000):int(end_sec * 1000)]
                    else:
                        full = None

                    lang_val = opts.get("lang_mode", LANG_AUTO_VALUE)
                    lang_param = None if lang_val == LANG_AUTO_VALUE else lang_val

                    if start_sec > 0 or end_sec < duration:
                        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                            tmp_path = tmp.name
                        try:
                            seg_audio = AudioSegment.from_file(path)[int(start_sec * 1000):int(end_sec * 1000)]
                            seg_audio.export(tmp_path, format="wav")
                            segments_iter, _ = model.transcribe(tmp_path, language=lang_param, vad_filter=True)
                        finally:
                            try:
                                os.unlink(tmp_path)
                            except OSError:
                                pass
                    else:
                        segments_iter, _ = model.transcribe(path, language=lang_param, vad_filter=True)

                    res = []
                    last_progress_update = [0.0]
                    last_log_update = [0.0]
                    segment_count = [0]
                    for s in segments_iter:
                        if self.cancel_requested:
                            break
                        res.append(s)
                        segment_count[0] += 1
                        now = time.time()
                        if now - last_progress_update[0] >= PROGRESS_UPDATE_INTERVAL_S:
                            val = min(100, (s.end / segment_duration) * 100) if (segment_duration and segment_duration > 0) else 100
                            self.root.after(0, lambda v=val: self._set_progress_value(v))
                            last_progress_update[0] = now
                        if now - last_log_update[0] >= LOG_UPDATE_INTERVAL_S or segment_count[0] <= 2:
                            seg_text = (s.text or "").strip()
                            self.log(f"   [{format_timestamp(s.start)}] {seg_text}")
                            last_log_update[0] = now

                    if not self.cancel_requested:
                        self.root.after(0, lambda: self._set_progress_value(100))
                        if start_sec > 0 or end_sec < duration:
                            res = [_SegmentOffset(s.start + start_sec, s.end + start_sec, s.text or "") for s in res]
                        is_segment = start_sec >= FULL_VIDEO_SEGMENT_EPS_S or (duration - end_sec) >= FULL_VIDEO_SEGMENT_EPS_S
                        self.save_files(path, res, audio_segment=audio, segment_start_sec=start_sec if is_segment else None, segment_end_sec=end_sec if is_segment else None, output_dir_raw=opts.get("output_dir"))
                        self.root.after(0, lambda p=path: self._mark_done_by_path(p))
                        done += 1
                except OSError:
                    self.log(t("file_skipped", name=name))
                    skipped_paths.append(path)

            if skipped_paths:
                paths_copy = list(skipped_paths)
                self.root.after(0, lambda: self._report_skipped_and_offer_remove(paths_copy))
            if self.cancel_requested:
                self.log(f"\n{t('cancelled', count=to_do - done)}")
            else:
                self.log(f"\n{t('all_tasks_complete')}")
                if opts.get("play_sound_on_finish"):
                    play_finish_sound()

        except (OSError, IndexError, RuntimeError) as e:
            err_msg = str(e)
            self.log(t("error_occurred", error=err_msg))
            if isinstance(e, IndexError) or "list index out of range" in err_msg.lower():
                self.log(t("error_no_audio_hint"))
            if os.environ.get("DEBUG"):
                self.log(traceback.format_exc())
        except Exception as e:
            err_msg = str(e)
            self.log(t("error_occurred", error=err_msg))
            if "list index out of range" in err_msg.lower():
                self.log(t("error_no_audio_hint"))
            if os.environ.get("DEBUG"):
                self.log(traceback.format_exc())
        finally:
            self.root.after(0, self.reset_ui)

    def _segment_file_suffix(self, start_sec, end_sec):
        """Суфікс для імен файлів сегмента: HH-MM-SS_HH-MM-SS (через format_timestamp_filename)."""
        return "_" + format_timestamp_filename(start_sec) + "_" + format_timestamp_filename(end_sec)

    def save_files(self, path, segments, audio_segment=None, segment_start_sec=None, segment_end_sec=None, output_dir_raw=None):
        out = self._resolve_output_dir(path, output_dir_raw)
        marker = self._processed_marker()
        base = os.path.splitext(os.path.basename(path))[0].replace(marker, "")
        if segment_start_sec is not None and segment_end_sec is not None:
            base = base + self._segment_file_suffix(segment_start_sec, segment_end_sec)
        txt_p = os.path.abspath(os.path.join(out, base + ".txt"))
        srt_p = os.path.abspath(os.path.join(out, base + ".srt"))

        with open(txt_p, "w", encoding="utf-8") as f:
            f.write("\n".join([(s.text or "").strip() for s in segments]))

        with open(srt_p, "w", encoding="utf-8") as f:
            for i, s in enumerate(segments, 1):
                timestamp = f"{format_timestamp_srt(s.start)} --> {format_timestamp_srt(s.end)}"
                f.write(f"{i}\n{timestamp}\n{(s.text or '').strip()}\n\n")

        self.log(t("files_created", name=base))
        self.log(t("txt_file"), None)
        self.log(txt_p, "link")
        self.log(t("srt_file"), None)
        self.log(srt_p, "link")

        if audio_segment is not None:
            mp3_p = os.path.abspath(os.path.join(out, base + "_audio.mp3"))
            try:
                audio_segment.export(mp3_p, format="mp3")
                self.log(t("audio_mp3_file"), None)
                self.log(mp3_p, "link")
            except Exception as e:
                self.log(t("audio_mp3_error", error=str(e)))

    def mark_done(self, idx, name):
        """Отмечает файл как обработанный в очереди и сохраняет очередь в request_queue.json."""
        if 0 <= idx < len(self.queue):
            self.queue[idx]["processed"] = True
            self._refresh_queue_treeview()
            self._save_queue_to_file()

    def _mark_done_by_path(self, path):
        """Отмечает файл как обработанный по пути (безопасно при изменении очереди)."""
        for q in self.queue:
            if q.get("path") == path:
                q["processed"] = True
                break
        self._refresh_queue_treeview()
        self._save_queue_to_file()

    def _report_skipped_and_offer_remove(self, skipped_paths):
        """Показывает отчёт о пропущенных файлах и предлагает удалить их из очереди."""
        if not skipped_paths:
            return
        files_list = "\n".join(os.path.basename(p) for p in skipped_paths)
        msg = t("skipped_report_message", files=files_list)
        if messagebox.askyesno(t("skipped_report_title"), msg):
            skipped_set = set(skipped_paths)
            self.queue[:] = [q for q in self.queue if q["path"] not in skipped_set]
            self._refresh_queue_treeview()
            self._save_queue_to_file()

    # --- СЕРВИСНЫЕ МЕТОДЫ ---

    def run_updates_check(self):
        def worker():
            updates = check_updates(self.log)
            if updates:
                updates_str = "\n".join([f"{p}: {c or 'not installed'} -> {l}" for p, c, l in updates])
                msg = t("updates_available", updates=updates_str)
                if messagebox.askyesno(t("update"), msg):
                    install_dependencies(log_func=self.log, packages_to_update=updates, include_nvidia=True)
            else:
                self.log(t("all_components_up_to_date"))
        threading.Thread(target=worker, daemon=True).start()

    def run_install(self):
        choice = messagebox.askyesnocancel(t("installation"), t("force_reinstall"))
        if choice is None: return
        threading.Thread(
            target=install_dependencies,
            kwargs={"force": choice, "log_func": self.log, "include_nvidia": True},
            daemon=True
        ).start()

    def log(self, msg, tag=None):
        def _do_log():
            self.log_box.config(state="normal")
            self.log_box.insert("end", str(msg) + ("" if str(msg).endswith("\n") else "\n"), tag or None)
            # Ограничение размера лога: удаляем старые строки сверху
            try:
                index_str = self.log_box.index("end-1c")
                parts = index_str.split(".")
                line_count = int(parts[0]) if parts else 0
            except (ValueError, tk.TclError, IndexError):
                line_count = 0
            if line_count > LOG_MAX_LINES:
                self.log_box.delete("1.0", f"{line_count - LOG_MAX_LINES}.0")
            self.log_box.see("end")
            self.log_box.config(state="disabled")
        self.root.after(0, _do_log)

    def clear_log(self):
        self.log_box.config(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.config(state="disabled")

    def _set_progress_value(self, value):
        """Установка значения прогресс-бара (вызывать из главного потока)."""
        try:
            self.progress["value"] = value
        except (tk.TclError, Exception):
            pass

    def reset_ui(self):
        self.start_btn.config(state="normal")
        self.cancel_btn.config(state="disabled")
        self.progress["value"] = 0

    def cancel_action(self):
        self.cancel_requested = True
        self.log(t("waiting_segment"))

    def show_help(self):
        """Показывает окно справки с прокруткой и адаптивным размером"""
        help_window = tk.Toplevel(self.root)
        help_window.title(t("help_title"))
        help_window.transient(self.root)
        
        # Обновляем главное окно для получения актуальных размеров
        self.root.update_idletasks()
        
        main_width = self.root.winfo_width()
        main_height = self.root.winfo_height()
        help_width = max(700, int(main_width * 0.85))
        help_height = max(600, int(main_height * 0.85))
        help_window.geometry(f"{help_width}x{help_height}")
        self._center_toplevel(help_window)
        
        # Создаем фрейм с прокруткой
        main_frame = ttk.Frame(help_window, padding=10)
        main_frame.pack(fill="both", expand=True)
        
        # Создаем ScrolledText для прокрутки
        text_widget = scrolledtext.ScrolledText(
            main_frame,
            wrap="word",
            font=("Segoe UI", 10),
            padx=15,
            pady=15,
            state="normal",
            relief="flat",
            borderwidth=1
        )
        text_widget.pack(fill="both", expand=True)
        
        # Вставляем текст справки (ленивая загрузка при первом открытии)
        text_widget.insert("1.0", load_help_text())
        text_widget.config(state="disabled")  # Делаем только для чтения
        
        # Прокрутка в начало
        text_widget.see("1.0")
        
        # Кнопка закрытия
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill="x", pady=(10, 0))
        ttk.Button(btn_frame, text=t("close"), command=help_window.destroy, width=15).pack(side="right")
        
        # Обработка закрытия окна
        help_window.protocol("WM_DELETE_WINDOW", help_window.destroy)
        
        # Фокус на текстовое поле для прокрутки колесиком
        text_widget.focus_set()
        
        # Привязываем прокрутку колесиком мыши (на случай, если фокус потерян)
        def on_mousewheel(event):
            text_widget.yview_scroll(int(-1 * (event.delta / 120)), "units")
        
        text_widget.bind("<MouseWheel>", on_mousewheel)

    def _on_enter_key(self, event=None):
        """Глобальный Enter: при пустой очереди — добавить файлы, иначе — начать транскрибацию."""
        if not self.queue:
            self.add_files_action()
        else:
            self.handle_start_logic()

    def _on_space_key(self, event=None):
        """Пробел по умолчанию переключает «Сохранить Mp3» везде, кроме поля ввода пути (Entry)."""
        w = self.root.focus_get()
        if w is not None:
            cls = w.winfo_class()
            if cls in ("Entry", "TEntry"):
                return
        self.save_audio_mp3.set(not self.save_audio_mp3.get())
        if event:
            return "break"

    @staticmethod
    def _sanitize_folder_name(name):
        """Заменяет символы, недопустимые в имени каталога Windows, на _."""
        s = re.sub(r'[\\/:*?"<>|]', "_", name)
        s = s.strip().rstrip(". ")
        return s if s else "_"

    def _resolve_output_dir(self, path, output_dir_raw=None):
        """
        Определяет каталог сохранения для файла path.
        output_dir_raw — значение из главного потока (опции); если None, читается self.output_dir.get().
        """
        raw = (output_dir_raw if output_dir_raw is not None else (self.output_dir.get() or "")).strip()
        if not raw:
            return os.path.dirname(path)
        if os.path.isabs(raw):
            out = os.path.normpath(raw)
            try:
                os.makedirs(out, exist_ok=True)
            except OSError:
                return os.path.dirname(path)
            return out
        safe_name = self._sanitize_folder_name(raw)
        out = os.path.join(os.path.dirname(path), safe_name)
        try:
            os.makedirs(out, exist_ok=True)
        except OSError:
            return os.path.dirname(path)
        return out

    def pick_output_folder(self):
        d = filedialog.askdirectory()
        if d:
            self.output_dir.set(d)

    def _paste_into_watch_dir(self, event=None):
        """Вставка из буфера обмена в поле каталога слежения (Ctrl+V)."""
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            return
        if text:
            self.watch_dir_entry.insert(tk.INSERT, text)
        return "break"

    def _start_watch(self, watch_path):
        """Запуск потоку слідкування за каталогом (без діалогів)."""
        self._watch_stop.clear()
        try:
            self._watch_seen = {os.path.normpath(os.path.join(watch_path, f)) for f in os.listdir(watch_path)
                               if os.path.isfile(os.path.join(watch_path, f)) and f.lower().endswith(VALID_EXTS)}
        except OSError:
            self._watch_seen = set()
        self._watch_thread = threading.Thread(target=self._watch_loop, daemon=True)
        self._watch_thread.start()
        self.log(t("watch_started", path=watch_path))

    def _on_watch_toggled(self):
        """Включение/выключение слежения за каталогом."""
        if self.watch_enabled.get():
            watch_path = (self.watch_dir.get() or "").strip()
            if not watch_path:
                d = filedialog.askdirectory()
                if not d:
                    self.watch_enabled.set(False)
                    return
                self.watch_dir.set(d)
                watch_path = d
            if not os.path.isdir(watch_path):
                self.watch_enabled.set(False)
                messagebox.showerror(t("error"), t("watch_folder_empty_error"))
                return
            self._start_watch(watch_path)
        else:
            self._watch_stop.set()
            self.log(t("watch_stopped"))
        self._persist_settings()

    def prepare_close(self):
        """Зупинити слідкування, трей та зберегти налаштування перед закриттям (викликається з main.py)."""
        self._watch_stop.set()
        if self._tray_icon:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        self._persist_settings()

    def _model_button_label(self):
        """Текст кнопки выбора модели: текущая модель (короткое имя)."""
        return self.whisper_model.get() or DEFAULT_MODEL

    def _folder_size_mb(self, path):
        """Примерный размер каталога в МБ (сумма размеров файлов)."""
        if not path or not os.path.isdir(path):
            return 0
        total = 0
        try:
            for _dir, _subdirs, files in os.walk(path):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(_dir, f))
                    except OSError:
                        pass
        except OSError:
            return 0
        return round(total / (1024 * 1024))

    def _show_model_dialog(self):
        """Открывает окно выбора модели Whisper: список моделей, отметка загруженных и размер."""
        cache_root = get_whisper_cache_dir()
        current = self.whisper_model.get() or DEFAULT_MODEL

        win = tk.Toplevel(self.root)
        win.title(t("model_dialog_title"))
        win.transient(self.root)
        win.grab_set()
        win.geometry("420x380")
        win.minsize(360, 300)
        main_f = ttk.Frame(win, padding=10)
        main_f.pack(fill="both", expand=True)
        ttk.Label(main_f, text=t("model_dialog_cache", cache_dir=cache_root), wraplength=380).pack(anchor="w")
        ttk.Label(main_f, text="").pack(anchor="w")

        frame = ttk.Frame(main_f)
        frame.pack(fill="both", expand=True)
        lb = tk.Listbox(frame, height=12, selectmode="single", font=("Segoe UI", 9))
        scroll = ttk.Scrollbar(frame)
        lb.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        lb.config(yscrollcommand=scroll.set)
        scroll.config(command=lb.yview)

        lines = []
        for name in WHISPER_MODELS:
            full_path = find_whisper_model_cache_path(cache_root, name)
            if full_path:
                size_mb = self._folder_size_mb(full_path)
                lines.append(f"{name}  —  {t('model_dialog_downloaded')}  ~{size_mb} MB")
            else:
                lines.append(f"{name}  —  {t('model_dialog_not_downloaded')}")
        lb.delete(0, "end")
        for line in lines:
            lb.insert("end", line)
        try:
            idx = WHISPER_MODELS.index(current)
            lb.selection_set(idx)
            lb.see(idx)
        except ValueError:
            pass

        def on_load():
            sel = lb.curselection()
            if not sel:
                return
            chosen = WHISPER_MODELS[sel[0]]
            self.whisper_model.set(chosen)
            WhisperModelSingleton.reset()
            try:
                WhisperModelSingleton.get(self.log, self.device_mode.get(), chosen)
            except Exception:
                pass
            self.model_btn.config(text=self._model_button_label())
            self._persist_settings()
            self.log(t("model_loaded", model=chosen))

        def on_ok():
            sel = lb.curselection()
            if sel:
                chosen = WHISPER_MODELS[sel[0]]
                self.whisper_model.set(chosen)
                self.model_btn.config(text=self._model_button_label())
                WhisperModelSingleton.reset()
                self._persist_settings()
                self.log(t("model_selected", model=chosen))
            win.destroy()

        def on_cancel():
            win.destroy()

        btn_f = ttk.Frame(main_f)
        btn_f.pack(fill="x", pady=(10, 0))
        ttk.Button(btn_f, text=t("model_load_btn"), command=on_load).pack(side="left", padx=2)
        ttk.Button(btn_f, text=t("ok"), command=on_ok).pack(side="left", padx=2)
        ttk.Button(btn_f, text=t("cancel"), command=on_cancel).pack(side="left", padx=2)
        win.protocol("WM_DELETE_WINDOW", on_cancel)
        self._center_toplevel(win)
        win.focus_set()

    def _on_tray_mode_change(self, event=None):
        """Обробник зміни перемикача Панель / Трей / Панель + Трей."""
        idx = self.tray_mode_combo.current()
        if 0 <= idx < len(self.TRAY_MODE_KEYS):
            self.tray_mode.set(self.TRAY_MODE_KEYS[idx])
            self._apply_tray_mode()
            self._persist_settings()

    def _run_autostart_script(self):
        """Запускає autorun_delayed.bat у папці програми (додає ярлик у автозавантаження)."""
        bat_path = os.path.join(BASE_DIR, "autorun_delayed.bat")
        if not os.path.isfile(bat_path):
            messagebox.showerror(t("error"), t("autostart_bat_not_found", path=bat_path))
            return
        try:
            if sys.platform == "win32":
                subprocess.Popen(
                    [bat_path],
                    cwd=BASE_DIR,
                    creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
                )
            else:
                subprocess.Popen([bat_path], cwd=BASE_DIR)
        except OSError as e:
            messagebox.showerror(t("error"), f"{t('autostart_run_error')}: {e}")

    def on_window_close(self):
        """Вызывается при нажатии X на окне: в режиме «Трей» — свернуть в трей, иначе — диалог закрытия."""
        if self.tray_mode.get() == "tray":
            self.root.withdraw()
        else:
            if self._on_close_request:
                self._on_close_request()

    def _center_toplevel(self, win, parent=None):
        """Размещает Toplevel по центру родительского окна (или экрана). Не выносит за границы экрана."""
        parent = parent or self.root
        win.update_idletasks()
        w = win.winfo_width()
        h = win.winfo_height()
        if w <= 1:
            w = 400
        if h <= 1:
            h = 300
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        if pw <= 1:
            pw = w
        if ph <= 1:
            ph = h
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        x = max(0, min(x, sw - w))
        y = max(0, min(y, sh - h))
        win.geometry(f"+{x}+{y}")

    def _persist_settings(self):
        """Зберігає поточні налаштування в settings.json (викликається при закритті та при зміні слідкування)."""
        save_app_settings({
            "language": self.ui_language.get(),
            "output_dir": (self.output_dir.get() or "").strip(),
            "watch_dir": (self.watch_dir.get() or "").strip(),
            "watch_enabled": self.watch_enabled.get(),
            "device_mode": self.device_mode.get(),
            "play_sound_on_finish": self.play_sound_on_finish.get(),
            "save_audio_mp3": self.save_audio_mp3.get(),
            "tray_mode": self.tray_mode.get(),
            "whisper_model": self.whisper_model.get(),
        })

    def _watch_loop(self):
        """Фоновый цикл: опрос каталога, при появлении нового файла — обработка, затем снова ожидание."""
        WATCH_POLL_INTERVAL = 2.0
        FILE_STABLE_DELAY = 1.0
        while not self._watch_stop.is_set():
            watch_path = (self.watch_dir.get() or "").strip()
            if not watch_path or not os.path.isdir(watch_path):
                self._watch_stop.set()
                break
            try:
                current = set()
                for f in os.listdir(watch_path):
                    full = os.path.normpath(os.path.join(watch_path, f))
                    if os.path.isfile(full) and f.lower().endswith(VALID_EXTS):
                        current.add(full)
                new_files = current - self._watch_seen
                if new_files:
                    path = next(iter(new_files))
                    self._watch_seen.add(path)
                    time.sleep(FILE_STABLE_DELAY)
                    if self._watch_stop.is_set():
                        break
                    self.log(t("watch_new_file", name=os.path.basename(path)))
                    self.root.after(0, lambda p=path: self._add_watch_file_to_queue(p))
            except OSError:
                pass
            for _ in range(int(WATCH_POLL_INTERVAL / 0.25)):
                if self._watch_stop.is_set():
                    break
                time.sleep(0.25)

    def _add_watch_file_to_queue(self, path):
        """Додає знайдений при слідкуванні файл у чергу, зберігає request_queue.json і запускає обробку цього файлу."""
        if not os.path.isfile(path):
            return
        add_files_to_queue_controller(
            [path],
            self.queue,
            self.queue_list,
            log_func=self.log
        )
        self._save_queue_to_file()
        idx = len(self.queue) - 1
        if idx >= 0:
            self.start_thread(mode="single", target_idx=idx)

    def clear_queue(self):
        self.queue.clear()
        self.queue_list.delete(*self.queue_list.get_children())
        self._save_queue_to_file()

    def add_files_action(self):
        """Обработчик кнопки 'Добавить файлы'"""
        files = add_multiple_files()
        if files:
            self.add_files_to_queue(files)

    def add_directory_action(self):
        """Обработчик кнопки 'Добавить каталог'"""
        files = add_directory(recursive=True)
        if files:
            self.add_files_to_queue(files)

    def add_files_to_queue(self, file_paths):
        """Добавляет список файлов в очередь через контроллер и сохраняет в request_queue.json."""
        add_files_to_queue_controller(
            file_paths,
            self.queue,
            self.queue_list,
            log_func=self.log
        )
        self._save_queue_to_file()

    # --- DRAG & DROP / LISTBOX ---

    def on_drop(self, e):
        """Обработчик события Drag & Drop через централизованный контроллер"""
        # Получаем данные из события Drop
        dropped_data = e.data
        
        # Обрабатываем через контроллер (поддерживает файлы и каталоги)
        # Передаем root для корректной обработки путей с пробелами через splitlist
        file_paths = process_dropped_files(dropped_data, tk_root=self.root)
        
        if file_paths:
            add_files_to_queue_controller(
                file_paths,
                self.queue,
                self.queue_list,
                log_func=self.log
            )
            self._save_queue_to_file()

    def on_drag_start(self, event):
        iid = self.queue_list.identify_row(event.y)
        self._drag_iid = iid
        try:
            self._drag_index = self.queue_list.index(iid) if iid else -1
        except tk.TclError:
            self._drag_index = -1

    def on_drag_motion(self, event):
        iid = self.queue_list.identify_row(event.y)
        if not iid or self._drag_index < 0:
            return
        try:
            idx = self.queue_list.index(iid)
        except tk.TclError:
            return
        if idx != self._drag_index and 0 <= idx < len(self.queue):
            item = self.queue.pop(self._drag_index)
            self.queue.insert(idx, item)
            self._refresh_queue_treeview()
            self._save_queue_to_file()
            self._drag_index = idx

    def setup_log_styles(self):
        """Интерактивные ссылки в логе"""
        self.log_box.tag_config("link", foreground="blue", underline=1)
        self.log_box.tag_bind("link", "<Button-1>", self.on_link_click)
        # Правая кнопка мыши для копирования
        self.log_menu = tk.Menu(self.root, tearoff=0)
        self.log_menu.add_command(label=t("copy"), command=self.copy_log_selection)
        self.log_box.bind("<Button-3>", lambda e: self.log_menu.tk_popup(e.x_root, e.y_root))
        # Ctrl+C для копирования. <<Copy>> срабатывает при Ctrl+C при любой раскладке (Windows).
        # Привязка <Control-с> с кириллической "с" убрана — на части систем даёт "bad event type or keysym".
        self.log_box.bind("<Control-c>", self._copy_log_event)
        self.log_box.bind("<<Copy>>", self._copy_log_event)

    def on_link_click(self, event):
            idx = self.log_box.index(f"@{event.x},{event.y}")
            rng = self.log_box.tag_prevrange("link", idx)
            if rng:
                path = self.log_box.get(*rng).strip()
                if os.path.exists(path):
                    # Shift — открыть папку и выделить файл
                    if event.state & 0x0001:
                        if sys.platform == "win32":
                            subprocess.run(
                                ['explorer', '/select,', os.path.normpath(path)],
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                            )
                        elif sys.platform == "darwin":
                            subprocess.run(['open', '-R', path], check=False)
                        else:
                            # Linux: открыть родительскую папку в файловом менеджере
                            subprocess.run(['xdg-open', os.path.dirname(path)], check=False)
                    else:
                        # Обычное открытие файла программой по умолчанию
                        if sys.platform == "win32":
                            os.startfile(path)
                        elif sys.platform == "darwin":
                            subprocess.run(['open', path], check=False)
                        else:
                            subprocess.run(['xdg-open', path], check=False)

    def _copy_log_event(self, event=None):
        """Обработчик Ctrl+C в логе — работает при любой раскладке (en/uk/ru)."""
        self.copy_log_selection()
        return "break"

    def copy_log_selection(self):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.log_box.selection_get())
        except tk.TclError:
            pass
    
    def on_language_change(self):
        """Обработчик изменения языка интерфейса"""
        lang_code = self.ui_language.get()
        set_language(lang_code)
        self.update_ui_language()
    
    def update_ui_language(self):
        """Обновляет все тексты интерфейса при смене языка"""
        # Обновляем заголовок окна
        self.root.title(t("app_title"))
        
        # Обновляем элементы интерфейса
        self.queue_header_label.config(text=t("queue_header"))
        self.add_files_btn.config(text=t("add_files"))
        self.add_directory_btn.config(text=t("add_directory"))
        self.clear_queue_btn.config(text=t("clear_queue"))
        self.help_btn.config(text=t("help"))
        self.start_btn.config(text=t("start_transcription"))
        self.dev_f.config(text=t("device_label"))
        self.lang_f.config(text=t("language_switcher"))
        self.play_sound_check.config(text=t("play_sound_finish"))
        self.save_audio_check.config(text=t("save_audio_mp3"))
        self.system_btn.config(text=t("system_check"))
        self.updates_btn.config(text=t("updates"))
        self.dependencies_btn.config(text=t("dependencies"))
        self.model_btn.config(text=self._model_button_label())
        self.output_folder_btn.config(text=t("output_folder"))
        self.watch_folder_check.config(text=t("watch_folder_label"))
        self.clear_log_btn.config(text=t("clear_log"))
        self.cancel_btn.config(text=t("cancel"))
        self.queue_list.heading("num", text=t("col_num"))
        self.queue_list.heading("filename", text=t("col_filename"))
        self.queue_list.heading("start", text=t("col_start"))
        self.queue_list.heading("end_seg1", text=t("col_end_seg1"))
        self.queue_list.heading("end_seg2", text=t("col_end_seg2"))
        self.queue_list.heading("end", text=t("col_end"))
        self.queue_list.heading("status", text=t("col_status"))
        self.tray_mode_combo["values"] = [t("tray_mode_panel"), t("tray_mode_tray"), t("tray_mode_panel_tray")]
        self.autostart_btn.config(text=t("autostart"))
        try:
            idx = self.TRAY_MODE_KEYS.index(self.tray_mode.get()) if self.tray_mode.get() in self.TRAY_MODE_KEYS else 0
            self.tray_mode_combo.current(idx)
        except tk.TclError:
            pass
        try:
            self.log_menu.entryconfig(0, label=t("copy"))
        except (tk.TclError, IndexError):
            pass