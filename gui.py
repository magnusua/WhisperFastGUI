import os
import re
import sys
import threading
import time
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
from config import HELP_TEXT, APP_VERSION, APP_DATE
from utils import format_timestamp, play_finish_sound, get_audio_duration_seconds
from model_manager import WhisperModelSingleton
from installer import install_dependencies, check_system, check_updates
from input_files import (
    add_multiple_files,
    add_directory,
    process_dropped_files,
    add_files_to_queue_controller
)
from lang_manager import t, set_language, get_language, load_app_settings, save_app_settings
from config import LANG_AUTO_VALUE, SUPPORTED_LANGUAGES, VALID_EXTS

# Расширения, при которых источник считается аудиофайлом (для диалога сохранения MP3)
AUDIO_EXTENSIONS = tuple(e for e in VALID_EXTS if e in ('.mp3', '.wav', '.m4a', '.flac', '.ogg'))

# Попытка импорта Drag & Drop
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_OK = True
except ImportError:
    DND_OK = False

# Базовый класс окна зависит от наличия tkinterdnd2
BaseTk = TkinterDnD.Tk if DND_OK else tk.Tk

# Задержка показа подсказки (мс)
TOOLTIP_DELAY_MS = 1000

# Ширина, под которую спроектирован интерфейс; при меньшей ширине окна масштаб уменьшается
UI_DESIGN_WIDTH = 1050
UI_MIN_SCALE = 0.5
UI_BASE_FONT_SIZE = 9


class Tooltip:
    """Подсказка при наведении на виджет: показ через заданную задержку (по умолчанию 1 с)."""
    def __init__(self, widget, text, delay_ms=TOOLTIP_DELAY_MS):
        self.widget = widget
        self.text = text
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
        if not self.text:
            return
        self._tw = tk.Toplevel(self.widget)
        self._tw.wm_overrideredirect(True)
        self._tw.wm_geometry("+0+0")
        label = tk.Label(
            self._tw,
            text=self.text,
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
    def __init__(self, root):
        self.root = root
        self.root.title(t("app_title"))
        self.root.geometry("1050x950")
        self.root.minsize(400, 400)

        # Кастомная иконка окна и панели задач (favicon.ico)
        _base_dir = os.path.dirname(os.path.abspath(__file__))
        _icon_path = os.path.join(_base_dir, "favicon.ico")
        if os.path.exists(_icon_path):
            try:
                self.root.iconbitmap(_icon_path)
            except Exception:
                pass
        
        # Состояние приложения
        self.queue = []  # Список путей к файлам
        self.cancel_requested = False
        
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
        
        # Загружаем сохранённые налаштування з settings.json
        saved = load_app_settings()
        saved_language = saved.get("language", "EN")
        self.output_dir.set(saved.get("output_dir", "") or "")
        self.watch_dir.set(saved.get("watch_dir", "") or "")
        self.watch_enabled.set(bool(saved.get("watch_enabled", False)))
        self.device_mode.set(saved.get("device_mode", "AUTO"))
        self.play_sound_on_finish.set(bool(saved.get("play_sound_on_finish", False)))
        self.save_audio_mp3.set(bool(saved.get("save_audio_mp3", False)))
        
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
        self.queue_list = tk.Listbox(q_frame, height=8, selectmode="single", font=("Consolas", 10))
        self.queue_list.pack(fill="both", expand=True, padx=2, pady=2)
        
        # Сортировка перетаскиванием внутри списка
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
        self.output_dir_entry.bind("<FocusOut>", self._on_output_dir_commit)
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
        ttk.Frame(log_header).pack(side="left", fill="x", expand=True)
        self.cancel_btn = ttk.Button(log_header, text=t("cancel"), command=self.cancel_action, state="disabled")
        self.cancel_btn.pack(side="right")
        
        self.log_box = scrolledtext.ScrolledText(main, height=18, state="disabled", wrap="word", font=("Consolas", 9))
        self.log_box.pack(fill="both", expand=True, pady=5)

        self._tooltips = []
        self._setup_tooltips()

    def _setup_tooltips(self):
        """Привязка подсказок к переключателям, кнопкам и полям (задержка 1 с)."""
        def tip(widget, key):
            self._tooltips.append(Tooltip(widget, t(key)))
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
        self.queue_list.config(font=("Consolas", max(6, int(10 * scale))))
        self.log_box.config(font=("Consolas", max(6, int(9 * scale))))
        self.progress["length"] = max(200, int(900 * scale))
        self.output_dir_entry.config(width=max(15, int(45 * scale)))

    # --- ЛОГИКА ЗАПУСКА ---

    def _processed_marker(self):
        """Единая строка-маркер обработанного файла в очереди."""
        return t("processed")

    def handle_start_logic(self):
        """Логика выбора режима обработки"""
        if not self.queue:
            messagebox.showerror(t("error"), t("error_empty_queue"))
            return

        sel = self.queue_list.curselection()
        marker = self._processed_marker()
        
        # Если выбран один файл
        if sel:
            idx = sel[0]
            name = self.queue_list.get(idx).replace(marker, "")
            # Диалог только когда в очереди больше одного файла; при одном файле — сразу обрабатываем его
            if len(self.queue) == 1:
                self.start_thread(mode="single", target_idx=idx)
                return
            choice = self._show_file_selection_dialog(name)
            
            if choice == "single":
                self.start_thread(mode="single", target_idx=idx)
                return
            elif choice == "all":
                # Продолжаем обработку для всех файлов
                pass
            else:  # choice == "cancel"
                return

        # Если есть обработанные файлы
        has_processed = any(marker in self.queue_list.get(i) for i in range(len(self.queue)))
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
        
        # Центрируем окно
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() // 2) - (dialog.winfo_width() // 2)
        y = (dialog.winfo_screenheight() // 2) - (dialog.winfo_height() // 2)
        dialog.geometry(f"+{x}+{y}")
        
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
        
        # Обработка закрытия окна
        dialog.protocol("WM_DELETE_WINDOW", choose_cancel)
        
        # Ожидание закрытия диалога
        dialog.wait_window()
        
        return result["choice"]

    def start_thread(self, mode, target_idx=None):
        self.cancel_requested = False
        self.start_btn.config(state="disabled")
        self.cancel_btn.config(state="normal")
        threading.Thread(target=self.process_queue, args=(mode, target_idx), daemon=True).start()

    def process_queue(self, mode, target_idx):
        try:
            model = WhisperModelSingleton.get(self.log, self.device_mode.get())
            
            # Фильтрация очереди
            marker = self._processed_marker()
            if mode == "single":
                indices = [target_idx]
            elif mode == "only_new":
                indices = [i for i, _ in enumerate(self.queue) if marker not in self.queue_list.get(i)]
            else:
                indices = list(range(len(self.queue)))

            done = 0
            to_do = len(indices)

            for idx in indices:
                if self.cancel_requested: break
                
                path = self.queue[idx]
                name = self.queue_list.get(idx).replace(marker, "")
                self.log(f"\n{t('processing', current=done + 1, total=to_do, name=name)}")

                # Решение о сохранении MP3: для видео — по чекбоксу; для аудио — спросить пользователя
                audio = None
                if self.save_audio_mp3.get():
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
                            audio = AudioSegment.from_file(path)
                    else:
                        audio = AudioSegment.from_file(path)

                # Корректная обработка языка для автоопределения
                lang_val = self.lang_mode.get()
                lang_param = None if lang_val == LANG_AUTO_VALUE else lang_val

                duration = get_audio_duration_seconds(path) or 1.0

                # Транскрибация
                segments, _ = model.transcribe(path, language=lang_param, vad_filter=True)

                res = []
                last_progress_update = [0.0]
                last_log_update = [0.0]
                segment_count = [0]
                for s in segments:
                    if self.cancel_requested: break
                    res.append(s)
                    segment_count[0] += 1
                    now = time.time()
                    if now - last_progress_update[0] >= 0.1:
                        self.progress["value"] = min(100, (s.end / duration) * 100)
                        last_progress_update[0] = now
                    if now - last_log_update[0] >= 0.5 or segment_count[0] <= 2:
                        self.log(f"   [{format_timestamp(s.start)}] {s.text.strip()}")
                        last_log_update[0] = now

                if not self.cancel_requested:
                    self.progress["value"] = 100
                    self.save_files(path, res, audio_segment=audio)
                    self.root.after(0, lambda i=idx, n=name: self.mark_done(i, n))
                    done += 1

            if self.cancel_requested:
                self.log(f"\n{t('cancelled', count=to_do - done)}")
            else:
                self.log(f"\n{t('all_tasks_complete')}")
                if self.play_sound_on_finish.get():
                    play_finish_sound()

        except Exception as e:
            self.log(t("error_occurred", error=str(e)))
        finally:
            self.root.after(0, self.reset_ui)

    def save_files(self, path, segments, audio_segment=None):
        out = self._resolve_output_dir(path)
        marker = self._processed_marker()
        base = os.path.splitext(os.path.basename(path))[0].replace(marker, "")
        txt_p = os.path.abspath(os.path.join(out, base + ".txt"))
        srt_p = os.path.abspath(os.path.join(out, base + ".srt"))

        with open(txt_p, "w", encoding="utf-8") as f:
            f.write("\n".join([s.text.strip() for s in segments]))
        
        with open(srt_p, "w", encoding="utf-8") as f:
            for i, s in enumerate(segments, 1):
                timestamp = f"{format_timestamp(s.start).replace(',', '.')} --> {format_timestamp(s.end).replace(',', '.')}"
                f.write(f"{i}\n{timestamp}\n{s.text.strip()}\n\n")

        self.log(t("files_created", name=base))
        self.log(t("txt_file"), None)
        self.log(txt_p, "link")
        self.log(t("srt_file"), None)
        self.log(srt_p, "link")

        # Сохранить аудио в MP3 рядом с источником (out уже как у транскрипции: папка вывода или папка файла)
        if audio_segment is not None:
            mp3_p = os.path.abspath(os.path.join(out, base + "_audio.mp3"))
            try:
                audio_segment.export(mp3_p, format="mp3")
                self.log(t("audio_mp3_file"), None)
                self.log(mp3_p, "link")
            except Exception as e:
                self.log(t("audio_mp3_error", error=str(e)))

    def mark_done(self, idx, name):
        """Обновление статуса в списке"""
        marker = self._processed_marker()
        if marker not in self.queue_list.get(idx):
            self.queue_list.delete(idx)
            self.queue_list.insert(idx, f"{name}{marker}")

    # --- СЕРВИСНЫЕ МЕТОДЫ ---

    def run_updates_check(self):
        def worker():
            updates = check_updates(self.log)
            if updates:
                updates_str = "\n".join([f"{p}: {c}->{l}" for p, c, l in updates])
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
        self.root.after(0, lambda: (
            self.log_box.config(state="normal"),
            self.log_box.insert("end", str(msg) + ("" if str(msg).endswith("\n") else "\n"), tag),
            self.log_box.see("end"),
            self.log_box.config(state="disabled")
        ))

    def clear_log(self):
        self.log_box.config(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.config(state="disabled")

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
        
        # Получаем размер главного окна
        main_width = self.root.winfo_width()
        main_height = self.root.winfo_height()
        main_x = self.root.winfo_x()
        main_y = self.root.winfo_y()
        
        # Устанавливаем размер окна справки (80% от главного окна, но не меньше минимального)
        help_width = max(700, int(main_width * 0.85))
        help_height = max(600, int(main_height * 0.85))
        
        # Центрируем окно относительно главного
        center_x = main_x + (main_width - help_width) // 2
        center_y = main_y + (main_height - help_height) // 2
        help_window.geometry(f"{help_width}x{help_height}+{center_x}+{center_y}")
        
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
        
        # Вставляем текст справки
        text_widget.insert("1.0", HELP_TEXT)
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

    def _on_output_dir_commit(self, event=None):
        """При потере фокуса — поле каталога уже сохранено в StringVar."""
        pass

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

    def _resolve_output_dir(self, path):
        """
        Определяет каталог сохранения для файла path.
        — Пустое поле → рядом с исходным файлом.
        — Полный путь (например D:\\...) → проверка/создание каталога, сохранение туда.
        — Не полный путь (имя подкаталога) → санитизация, создание рядом с исходным, сохранение туда.
        Указанный каталог применяется ко всем файлам в очереди.
        """
        raw = (self.output_dir.get() or "").strip()
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
        """Зупинити слідкування та зберегти налаштування перед закриттям (викликається з main.py)."""
        self._watch_stop.set()
        self._persist_settings()

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
                    self.process_single_file(path)
            except OSError:
                pass
            for _ in range(int(WATCH_POLL_INTERVAL / 0.25)):
                if self._watch_stop.is_set():
                    break
                time.sleep(0.25)

    def process_single_file(self, path):
        """Обработка одного файла (для слежения за каталогом): текущие настройки, без очереди."""
        try:
            self.root.after(0, lambda: (self.start_btn.config(state="disabled"), self.cancel_btn.config(state="normal")))
            model = WhisperModelSingleton.get(self.log, self.device_mode.get())
            name = os.path.basename(path)
            self.log(f"\n{t('processing', current=1, total=1, name=name)}")
            audio = None
            if self.save_audio_mp3.get():
                ext = os.path.splitext(path)[1].lower()
                is_audio_source = ext in AUDIO_EXTENSIONS
                if is_audio_source:
                    choice = [None]
                    def ask_save_mp3():
                        choice[0] = messagebox.askyesno(
                            t("save_audio_mp3"),
                            t("save_mp3_confirm", filename=name)
                        )
                    self.root.after(0, ask_save_mp3)
                    while choice[0] is None and not self.cancel_requested:
                        time.sleep(0.05)
                    if choice[0]:
                        audio = AudioSegment.from_file(path)
                else:
                    audio = AudioSegment.from_file(path)
            lang_val = self.lang_mode.get()
            lang_param = None if lang_val == LANG_AUTO_VALUE else lang_val
            duration = get_audio_duration_seconds(path) or 1.0
            segments, _ = model.transcribe(path, language=lang_param, vad_filter=True)
            res = []
            last_progress_update = [0.0]
            last_log_update = [0.0]
            segment_count = [0]
            for s in segments:
                if self.cancel_requested:
                    break
                res.append(s)
                segment_count[0] += 1
                now = time.time()
                if now - last_progress_update[0] >= 0.1:
                    self.progress["value"] = min(100, (s.end / duration) * 100)
                    last_progress_update[0] = now
                if now - last_log_update[0] >= 0.5 or segment_count[0] <= 2:
                    self.log(f"   [{format_timestamp(s.start)}] {s.text.strip()}")
                    last_log_update[0] = now
            if not self.cancel_requested:
                self.progress["value"] = 100
                self.save_files(path, res, audio_segment=audio)
                if self.play_sound_on_finish.get():
                    play_finish_sound()
        except Exception as e:
            self.log(t("error_occurred", error=str(e)))
        finally:
            self.root.after(0, self.reset_ui)

    def clear_queue(self):
        self.queue.clear()
        self.queue_list.delete(0, "end")

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
        """
        Добавляет список файлов в очередь через централизованный контроллер.
        
        Args:
            file_paths: Список путей к файлам для добавления
        """
        add_files_to_queue_controller(
            file_paths,
            self.queue,
            self.queue_list,
            log_func=self.log
        )

    # --- DRAG & DROP / LISTBOX ---

    def on_drop(self, e):
        """Обработчик события Drag & Drop через централизованный контроллер"""
        # Получаем данные из события Drop
        dropped_data = e.data
        
        # Обрабатываем через контроллер (поддерживает файлы и каталоги)
        # Передаем root для корректной обработки путей с пробелами через splitlist
        file_paths = process_dropped_files(dropped_data, tk_root=self.root)
        
        if file_paths:
            # Добавляем через централизованный контроллер
            add_files_to_queue_controller(
                file_paths,
                self.queue,
                self.queue_list,
                log_func=self.log
            )

    def on_drag_start(self, event):
        self._drag_index = self.queue_list.nearest(event.y)

    def on_drag_motion(self, event):
        idx = self.queue_list.nearest(event.y)
        if idx != self._drag_index and idx >= 0:
            self.queue.insert(idx, self.queue.pop(self._drag_index))
            txt = self.queue_list.get(self._drag_index)
            self.queue_list.delete(self._drag_index)
            self.queue_list.insert(idx, txt)
            self._drag_index = idx

    def setup_log_styles(self):
        """Интерактивные ссылки в логе"""
        self.log_box.tag_config("link", foreground="blue", underline=1)
        self.log_box.tag_bind("link", "<Button-1>", self.on_link_click)
        # Правая кнопка мыши для копирования
        self.log_menu = tk.Menu(self.root, tearoff=0)
        self.log_menu.add_command(label=t("copy"), command=self.copy_log_selection)
        self.log_box.bind("<Button-3>", lambda e: self.log_menu.tk_popup(e.x_root, e.y_root))

    def on_link_click(self, event):
            idx = self.log_box.index(f"@{event.x},{event.y}")
            rng = self.log_box.tag_prevrange("link", idx)
            if rng:
                path = self.log_box.get(*rng).strip()
                if os.path.exists(path):
                    import subprocess
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
        self.output_folder_btn.config(text=t("output_folder"))
        self.watch_folder_check.config(text=t("watch_folder_label"))
        self.clear_log_btn.config(text=t("clear_log"))
        self.cancel_btn.config(text=t("cancel"))
        try:
            self.log_menu.entryconfig(0, label=t("copy"))
        except (tk.TclError, IndexError):
            pass