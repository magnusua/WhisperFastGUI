import os
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

# Импорт pydub с обработкой ошибок для Python 3.13+
try:
    from pydub import AudioSegment
except ImportError as e:
    if "audioop" in str(e) or "pyaudioop" in str(e):
        try:
            from whisperfast.i18n import t
        except ImportError:
            from whisperfast.i18n.fallback import t
        error_title = t("error")
        error_msg = t(
            "pydub_import_error",
            error_label=error_title,
            major=sys.version_info.major,
            minor=sys.version_info.minor,
            deps=t("dependencies"),
        )
        from tkinter import messagebox as mb
        mb.showerror(error_title, error_msg)
        sys.exit(1)
    else:
        raise

# На Windows pydub запускает ffmpeg/ffprobe через subprocess.Popen.
# Підміняємо лише посилання всередині pydub, не глобальний subprocess.Popen.
if sys.platform == "win32":
    try:
        import types
        import pydub.audio_segment as _pydub_audio_segment
        if not getattr(_pydub_audio_segment, "_wf_no_window_patch", False):
            _orig_pydub_popen = subprocess.Popen

            def _pydub_popen_no_window(*args, **kwargs):
                kwargs["creationflags"] = kwargs.get("creationflags", 0) | getattr(
                    subprocess, "CREATE_NO_WINDOW", 0
                )
                return _orig_pydub_popen(*args, **kwargs)

            _shim = types.SimpleNamespace()
            for _name in dir(subprocess):
                if not _name.startswith("_"):
                    setattr(_shim, _name, getattr(subprocess, _name))
            _shim.Popen = _pydub_popen_no_window
            _pydub_audio_segment.subprocess = _shim
            _pydub_audio_segment._wf_no_window_patch = True
    except Exception:
        pass

# Импорт модулей проекта
from whisperfast.config import (
    APP_VERSION, APP_DATE, BASE_DIR, RESOURCES_DIR,
    LANG_AUTO_VALUE, SUPPORTED_LANGUAGES,
    DEFAULT_START_TIMESTAMP, DEFAULT_MODEL,
    get_whisper_cache_dir,
)
from whisperfast.utils import (
    normalize_queue_path, normalize_display_path,
)
from whisperfast.core.model_manager import WhisperModelSingleton
from whisperfast.core.transcription import run_queue, save_files as save_transcription_files
from whisperfast.setup.installer import install_dependencies, check_system, check_updates
from whisperfast.updates.app_updates import apply_app_update
from whisperfast.updates.model_updates import apply_whisper_model_updates
from whisperfast.setup.gpu_info import refresh_gpu_settings
from whisperfast.core.input_files import (
    add_multiple_files,
    add_directory,
    process_dropped_files,
)
from whisperfast.core.queue_manager import (
    QueueController,
    open_watch_dirs_dialog,
    parse_watch_dirs,
    serialize_watch_dirs,
    valid_watch_dirs,
)
from whisperfast.postprocess.cursor_postprocess import ensure_redactor_file
from whisperfast.postprocess.providers import PROVIDER_CURSOR, normalize_provider_id
from whisperfast.i18n import t, set_language
from whisperfast.settings import load_app_settings, save_app_settings
from whisperfast.open_path import open_file_location
from whisperfast.platform_util import win_no_window_kwargs
from whisperfast.ui.widgets import (
    Tooltip,
    UI_DESIGN_WIDTH,
    UI_MIN_SCALE,
    UI_BASE_FONT_SIZE,
)
from whisperfast.ui import tray as tray_ui
from whisperfast.ui import dialogs as ui_dialogs
from whisperfast.ui.log_panel import LogPanel
from whisperfast.ui.ai_jobs import AiJobQueue



# Попытка импорта Drag & Drop
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_OK = True
except ImportError:
    DND_OK = False


# Базовый класс окна зависит от наличия tkinterdnd2
BaseTk = TkinterDnD.Tk if DND_OK else tk.Tk



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
        self._icon_path = os.path.join(RESOURCES_DIR, "favicon.ico")
        if os.path.exists(self._icon_path):
            try:
                self.root.iconbitmap(self._icon_path)
            except Exception:
                pass

        self.log_panel = LogPanel(self.root)
        self.ai_jobs = AiJobQueue(self)

        # Состояние приложения: очередь — QueueController (request_queue.json + слідкування)
        self.queue_ctrl = QueueController(
            request_queue_file=os.path.join(BASE_DIR, "request_queue.json"),
            log_func=self.log,
            root_after=lambda ms, fn: self.root.after(ms, fn),
        )
        self.queue = self.queue_ctrl.queue  # сумісність: той самий list
        self.cancel_requested = False
        self._process_queue_lock = threading.Lock()  # только одна обработка очереди одновременно
        
        # Переменные интерфейса
        self.device_mode = tk.StringVar(value="AUTO")
        self.lang_mode = tk.StringVar(value=LANG_AUTO_VALUE)  # AUTO для языка транскрипции
        self.output_dir = tk.StringVar()
        self.output_mode = tk.StringVar(value="beside")  # beside | custom | named_folder
        self.output_named_folder = tk.StringVar(value="{basename}")
        self.mp3_output_mode = tk.StringVar(value="inherit")  # inherit | beside | custom
        self.mp3_output_dir = tk.StringVar()
        self.watch_dir = tk.StringVar()  # каталоги через кому (settings.json)
        self.watch_enabled = tk.BooleanVar(value=False)
        self.play_sound_on_finish = tk.BooleanVar(value=False)  # По умолчанию снят
        self.save_audio_mp3 = tk.BooleanVar(value=False)  # Сохранять извлечённое аудио в MP3
        self.send_txt_to_ai = tk.BooleanVar(value=False)
        self.send_txt_to_cursor = self.send_txt_to_ai  # alias для сумісності
        self.export_md_to_docx = tk.BooleanVar(value=False)
        self.ai_provider = tk.StringVar(value=PROVIDER_CURSOR)
        self.cursor_api_key = tk.StringVar(value="")
        self.gemini_api_key = tk.StringVar(value="")
        self.gemini_model = tk.StringVar(value="gemini-2.0-flash")
        self.azure_openai_endpoint = tk.StringVar(value="")
        self.azure_openai_api_key = tk.StringVar(value="")
        self.azure_openai_deployment = tk.StringVar(value="")
        self.azure_openai_api_version = tk.StringVar(value="2024-08-01-preview")
        self.tray_mode = tk.StringVar(value="panel")  # "panel" | "tray" | "panel_tray"
        self.whisper_model = tk.StringVar(value=DEFAULT_MODEL)
        
        # Загружаем сохранённые налаштування з settings.json
        saved = load_app_settings()
        saved_language = saved.get("language", "EN")
        self._load_output_settings_from_saved(saved)
        # watch_dir: один або кілька каталогів через кому
        self.watch_dir.set(serialize_watch_dirs(parse_watch_dirs(saved.get("watch_dir", "") or "")))
        self.watch_enabled.set(bool(saved.get("watch_enabled", False)))
        self.device_mode.set(saved.get("device_mode", "AUTO"))
        self.play_sound_on_finish.set(bool(saved.get("play_sound_on_finish", False)))
        self.save_audio_mp3.set(bool(saved.get("save_audio_mp3", False)))
        send_ai = saved.get("send_txt_to_ai")
        if send_ai is None:
            send_ai = saved.get("send_txt_to_cursor", False)
        self.send_txt_to_ai.set(bool(send_ai))
        self.export_md_to_docx.set(bool(saved.get("export_md_to_docx", False)))
        self.ai_provider.set(normalize_provider_id(saved.get("ai_provider") or PROVIDER_CURSOR))
        self.cursor_api_key.set((saved.get("cursor_api_key") or "").strip())
        self.gemini_api_key.set((saved.get("gemini_api_key") or "").strip())
        self.gemini_model.set(
            (saved.get("gemini_model") or "").strip() or "gemini-2.0-flash"
        )
        self.azure_openai_endpoint.set((saved.get("azure_openai_endpoint") or "").strip())
        self.azure_openai_api_key.set((saved.get("azure_openai_api_key") or "").strip())
        self.azure_openai_deployment.set(
            (saved.get("azure_openai_deployment") or "").strip()
        )
        self.azure_openai_api_version.set(
            (saved.get("azure_openai_api_version") or "").strip()
            or "2024-08-01-preview"
        )
        self.tray_mode.set(saved.get("tray_mode", "panel"))
        self.whisper_model.set(saved.get("whisper_model", DEFAULT_MODEL) or DEFAULT_MODEL)
        self.has_nvidia = bool(saved.get("has_nvidia", False))
        self.gpu_model = (saved.get("gpu_model") or "").strip()
        has_nvidia, gpu_name = refresh_gpu_settings()
        self.has_nvidia = has_nvidia
        self.gpu_model = (gpu_name or "").strip()

        # Загружаем сохраненный язык или используем EN по умолчанию
        self.ui_language = tk.StringVar(value=saved_language)  # Язык интерфейса
        
        # Устанавливаем начальный язык
        set_language(saved_language)
        
        # Привязываем изменение языка к обновлению UI
        self.ui_language.trace("w", lambda *args: self.on_language_change())

        self.build_ui()
        self.log_panel.setup_styles()
        self.log_panel.reload_from_store()

        self.queue_ctrl.configure(
            get_watch_dirs=lambda: parse_watch_dirs(self.watch_dir.get()),
            start_processing=lambda mode, target_idx=None, from_watch=False: self.start_thread(
                mode=mode, target_idx=target_idx, from_watch=from_watch
            ),
            is_processing=lambda: self._process_queue_lock.locked(),
            log_func=self.log,
            root_after=lambda ms, fn: self.root.after(ms, fn),
        )
        self.queue_ctrl.bind_treeview(self.queue_list)

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

        # Загрузка очереди из request_queue.json; при первом запуске создаём пустой файл
        self.queue_ctrl.load_from_file()
        self.queue_ctrl.ensure_file_exists()

        # Якщо слідкування було увімкнено — запускаємо після побудови UI
        if self.watch_enabled.get() and valid_watch_dirs(self.watch_dir.get()):
            self.queue_ctrl.start_watch()

        if not DND_OK:
            self.log(t("warning_dnd"))

        # Иконка в системном трее (зависит от переключателя Панель / Трей / Панель + Трей)
        self._apply_tray_mode()


    def _setup_tray(self):
        tray_ui.setup_tray(self)

    def _apply_tray_mode(self):
        tray_ui.apply_tray_mode(self)

    def _tray_show_window(self):
        tray_ui.tray_show_window(self)

    def _tray_quit(self):
        tray_ui.tray_quit(self)

    TRAY_MODE_KEYS = ("panel", "tray", "panel_tray")

    def _load_queue_from_file(self):
        self.queue_ctrl.load_from_file()

    def _save_queue_to_file(self):
        self.queue_ctrl.save_to_file()

    def _refresh_queue_treeview(self):
        self.queue_ctrl.refresh_treeview()

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
            self.queue_ctrl.update_row(
                idx,
                start=e_start.get().strip() or DEFAULT_START_TIMESTAMP,
                end_segment_1=e_seg1.get().strip(),
                end_segment_2=e_seg2.get().strip(),
                end=e_end.get().strip() or row["end"],
            )
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
        self.queue_list = ttk.Treeview(q_frame, columns=cols, show="headings", height=8, selectmode="extended")
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
        self.queue_list.bind("<Shift-Button-1>", self._on_queue_shift_click)
        self.queue_list.bind("<B1-Motion>", self.on_drag_motion)
        self.queue_list.bind("<Delete>", self.delete_selected_queue_items)
        self.queue_list.bind("<Button-3>", self._on_queue_context_menu)
        self.queue_menu = tk.Menu(self.root, tearoff=0)
        self.queue_menu.add_command(label=t("delete_from_queue"), command=self.delete_selected_queue_items)

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

        # Одна компактна строка: збереження, MP3, слідкування і Cursor
        tools_row = ttk.Frame(main)
        tools_row.pack(fill="x", pady=10)
        ttk.Frame(tools_row).pack(side="left", fill="x", expand=True)
        tools_center = ttk.Frame(tools_row)
        tools_center.pack(side="left")

        self.root.bind_all("<Return>", self._on_enter_key)
        self.root.bind_all("<space>", self._on_space_key)
        self.output_folder_btn = ttk.Button(
            tools_center, text=t("output_folder"), command=self._show_output_settings_dialog
        )
        self.output_folder_btn.pack(side="left", padx=2)

        ttk.Label(tools_center, text=" | ").pack(side="left", padx=5)
        mp3_frame = ttk.Frame(tools_center)
        mp3_frame.pack(side="left", padx=5)
        self.save_audio_check = ttk.Checkbutton(
            mp3_frame,
            text="",
            variable=self.save_audio_mp3,
            command=self._persist_settings,
            width=2,
        )
        self.save_audio_check.pack(side="left")
        self.mp3_settings_btn = ttk.Button(
            mp3_frame, text=t("save_audio_mp3"), command=self._show_mp3_settings_dialog
        )
        self.mp3_settings_btn.pack(side="left", padx=(0, 0))

        ttk.Label(tools_center, text=" | ").pack(side="left", padx=5)
        watch_frame = ttk.Frame(tools_center)
        watch_frame.pack(side="left", padx=5)
        self.watch_folder_check = ttk.Checkbutton(
            watch_frame,
            text="",
            variable=self.watch_enabled,
            command=self._on_watch_toggled,
            width=2,
        )
        self.watch_folder_check.pack(side="left")
        self.watch_dirs_btn = ttk.Button(
            watch_frame, text=t("watch_folder_label"), command=self._open_watch_dirs_dialog
        )
        self.watch_dirs_btn.pack(side="left", padx=(0, 0))

        ttk.Label(tools_center, text=" | ").pack(side="left", padx=5)
        cursor_frame = ttk.Frame(tools_center)
        cursor_frame.pack(side="left", padx=5)
        self.send_txt_cursor_check = ttk.Checkbutton(
            cursor_frame,
            text="",
            variable=self.send_txt_to_ai,
            command=self._on_send_txt_to_ai_toggled,
            width=2,
        )
        self.send_txt_cursor_check.pack(side="left")
        self.edit_redactor_btn = ttk.Button(
            cursor_frame, text=t("send_txt_to_ai"), command=self.ai_jobs.edit_redactor_file
        )
        self.edit_redactor_btn.pack(side="left", padx=(0, 0))
        self.cursor_api_key_btn = ttk.Button(
            tools_center,
            text=t("ai_api_keys_button"),
            command=self._show_ai_api_keys_dialog,
        )
        self.cursor_api_key_btn.pack(side="left", padx=2)

        ttk.Label(tools_center, text=" | ").pack(side="left", padx=5)
        docx_frame = ttk.Frame(tools_center)
        docx_frame.pack(side="left", padx=5)
        self.export_md_docx_check = ttk.Checkbutton(
            docx_frame,
            text="",
            variable=self.export_md_to_docx,
            command=self._on_export_md_to_docx_toggled,
            width=2,
        )
        self.export_md_docx_check.pack(side="left")
        self.export_md_docx_label = ttk.Label(docx_frame, text=t("export_md_to_docx"))
        self.export_md_docx_label.pack(side="left", padx=(0, 0))

        ttk.Frame(tools_row).pack(side="left", fill="x", expand=True)
        ensure_redactor_file()

        # Прогресс
        self.progress = ttk.Progressbar(main, length=900)
        self.progress.pack(fill="x", pady=(10, 5))
        
        # === БЛОК 4: ЛОГ И КНОПКА ОТМЕНЫ (блок «Очистить лог» | Устройство | кнопки — по центру) ===
        log_header = ttk.Frame(main)
        log_header.pack(fill="x", pady=(5, 0))
        ttk.Frame(log_header).pack(side="left", fill="x", expand=True)
        log_center = ttk.Frame(log_header)
        log_center.pack(side="left")
        self.clear_log_btn = ttk.Button(log_center, text=t("clear_log"), command=self.log_panel.clear)
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
        self.log_panel.bind_widget(self.log_box)

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
        tip(self.mp3_settings_btn, "tooltip_mp3_settings")
        tip(self.send_txt_cursor_check, "tooltip_send_txt_to_ai")
        tip(self.edit_redactor_btn, "tooltip_edit_redactor")
        tip(self.cursor_api_key_btn, "tooltip_ai_api_keys")
        tip(self.export_md_docx_check, "tooltip_export_md_to_docx")
        tip(self.export_md_docx_label, "tooltip_export_md_to_docx")
        tip(self.system_btn, "tooltip_system")
        tip(self.updates_btn, "tooltip_updates")
        tip(self.dependencies_btn, "tooltip_dependencies")
        self._tooltips.append(Tooltip(self.model_btn, t("tooltip_model_btn", cache_dir=get_whisper_cache_dir()), is_key=False))
        tip(self.tray_mode_combo, "tooltip_tray_mode")
        tip(self.autostart_btn, "tooltip_autostart")
        tip(self.output_folder_btn, "tooltip_output_folder")
        tip(self.watch_folder_check, "tooltip_watch_folder")
        tip(self.watch_dirs_btn, "tooltip_watch_dirs")
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
            unprocessed_count = sum(1 for q in self.queue if not q.get("processed"))
            choice = messagebox.askquestion(
                t("queue_dialog"),
                t("process_only_new", count=unprocessed_count),
            )
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

    def start_thread(self, mode, target_idx=None, from_watch=False):
        if not self._process_queue_lock.acquire(blocking=False):
            self.log(t("already_processing"))
            if from_watch:
                self.queue_ctrl.watch_pending_continue = True
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
            "output_mode": self.output_mode.get() or "beside",
            "output_dir": (self.output_dir.get() or "").strip(),
            "output_named_folder": (self.output_named_folder.get() or "").strip() or "{basename}",
            "mp3_output_mode": self.mp3_output_mode.get() or "inherit",
            "mp3_output_dir": (self.mp3_output_dir.get() or "").strip(),
            "send_txt_to_cursor": self.send_txt_to_ai.get(),
            "send_txt_to_ai": self.send_txt_to_ai.get(),
            "export_md_to_docx": self.export_md_to_docx.get(),
            "ai_provider": normalize_provider_id(self.ai_provider.get()),
            "cursor_api_key": (self.cursor_api_key.get() or "").strip(),
            "gemini_api_key": (self.gemini_api_key.get() or "").strip(),
            "gemini_model": (self.gemini_model.get() or "").strip() or "gemini-2.0-flash",
            "azure_openai_endpoint": (self.azure_openai_endpoint.get() or "").strip(),
            "azure_openai_api_key": (self.azure_openai_api_key.get() or "").strip(),
            "azure_openai_deployment": (self.azure_openai_deployment.get() or "").strip(),
            "azure_openai_api_version": (
                (self.azure_openai_api_version.get() or "").strip()
                or "2024-08-01-preview"
            ),
        }

        def run_and_release():
            try:
                self.process_queue(mode, target_idx, options)
            finally:
                self._process_queue_lock.release()
                self.root.after(0, self._continue_watch_queue)
        threading.Thread(target=run_and_release, daemon=True).start()

    def _continue_watch_queue(self):
        """Після завершення обробки запускає наступні файли, додані слідкуванням під час зайнятості."""
        self.queue_ctrl.continue_after_processing(cancel_requested=self.cancel_requested)


    def process_queue(self, mode, target_idx, options=None):
        self._all_complete_deferred = False
        self._finish_sound_deferred = False
        run_queue(self, mode, target_idx, options)

    def save_files(
        self,
        path,
        segments,
        audio_segment=None,
        segment_start_sec=None,
        segment_end_sec=None,
        output_opts=None,
        send_txt_to_cursor=False,
        cursor_api_key="",
        log_file_id=None,
    ):
        save_transcription_files(
            self,
            path,
            segments,
            audio_segment=audio_segment,
            segment_start_sec=segment_start_sec,
            segment_end_sec=segment_end_sec,
            output_opts=output_opts,
            send_txt_to_cursor=send_txt_to_cursor,
            cursor_api_key=cursor_api_key,
            log_file_id=log_file_id,
        )

    def mark_done(self, idx, name):
        """Отмечает файл как обработанный в очереди и сохраняет очередь в request_queue.json."""
        self.queue_ctrl.mark_done(idx)

    def _mark_done_by_path(self, path):
        """Отмечает файл как обработанный по пути (безопасно при изменении очереди)."""
        self.queue_ctrl.mark_done_by_path(path)

    def _report_skipped_and_offer_remove(self, skipped_paths):
        """Показывает отчёт о пропущенных файлах и предлагает удалить их из очереди."""
        if not skipped_paths:
            return
        files_list = "\n".join(os.path.basename(p) for p in skipped_paths)
        msg = t("skipped_report_message", files=files_list)
        if messagebox.askyesno(t("skipped_report_title"), msg):
            self.queue_ctrl.remove_paths(skipped_paths)

    # --- СЕРВИСНЫЕ МЕТОДЫ ---

    def run_updates_check(self):
        def worker():
            from whisperfast.setup.external_tools import log_external_tool_howto

            result = check_updates(self.log)
            packages = result.get("packages", []) if isinstance(result, dict) else result
            models = result.get("models", []) if isinstance(result, dict) else []
            app_info = result.get("app", {}) if isinstance(result, dict) else {}
            external = result.get("external", []) if isinstance(result, dict) else []
            lines = [
                t(
                    "package_check_line",
                    package=p,
                    current=c or t("not_installed_short"),
                    latest=l,
                )
                for p, c, l in packages
            ]
            for name, cur, lat in models:
                lines.append(t("model_update_line", model=name, current=cur, latest=lat))
            if app_info.get("needs_update"):
                lines.append(
                    t("app_update_line", current=app_info.get("current", ""), latest=app_info.get("remote", ""))
                )
            for tool in external:
                display = tool.get("display") or tool.get("name") or "?"
                current = tool.get("current") or t("external_tool_not_installed")
                latest = tool.get("latest") or "?"
                lines.append(
                    t(
                        "external_tool_update_line",
                        tool=display,
                        current=current,
                        latest=latest,
                    )
                )
            if lines:
                msg = t("updates_available", updates="\n".join(lines))
                if external:
                    msg += "\n\n" + t("external_tool_manual_hint")
                if messagebox.askyesno(t("update"), msg):
                    if packages:
                        install_dependencies(
                            log_func=self.log,
                            packages_to_update=packages,
                            include_nvidia=True,
                        )
                    if models:
                        apply_whisper_model_updates([m[0] for m in models], log_func=self.log)
                        WhisperModelSingleton.reset()
                    if app_info.get("needs_update"):
                        app_result = apply_app_update(log_func=self.log)
                        if app_result.get("success") and app_result.get("needs_restart"):
                            def ask_restart():
                                if messagebox.askyesno(
                                    t("app_update_restart_title"),
                                    t("app_update_restart_msg"),
                                ):
                                    self._restart_after_app_update(app_result.get("restart_script"))
                            self.root.after(0, ask_restart)
                    if external:
                        self.log(t("external_tool_manual_howto_header"))
                        for tool in external:
                            log_external_tool_howto(tool.get("howto") or tool.get("name") or "", self.log)
            else:
                self.log(t("all_components_up_to_date"))
        threading.Thread(target=worker, daemon=True).start()

    def _restart_after_app_update(self, restart_script=None):
        """Закриває програму та перезапускає після оновлення файлів."""
        try:
            self.prepare_close()
        except Exception:
            pass
        WhisperModelSingleton.unload()
        kwargs = {}
        if sys.platform == "win32":
            kwargs.update(win_no_window_kwargs())
        if restart_script and os.path.isfile(restart_script):
            subprocess.Popen([restart_script], cwd=BASE_DIR, **kwargs)
        else:
            vbs = os.path.join(BASE_DIR, "run_whisper.vbs")
            if sys.platform == "win32" and os.path.isfile(vbs):
                subprocess.Popen(["wscript.exe", vbs], cwd=BASE_DIR, **kwargs)
            else:
                subprocess.Popen([sys.executable, os.path.join(BASE_DIR, "main.py")], cwd=BASE_DIR, **kwargs)
        self.root.destroy()

    def run_install(self):
        choice = messagebox.askyesnocancel(t("installation"), t("force_reinstall"))
        if choice is None: return
        threading.Thread(
            target=install_dependencies,
            kwargs={"force": choice, "log_func": self.log, "include_nvidia": True},
            daemon=True
        ).start()

    def log(self, msg, tag=None):
        self.log_panel.log(msg, tag)

    def log_action(self, msg, callback):
        self.log_panel.log_action(msg, callback)

    def begin_file_log(self, source, name=None, current=None, total=None):
        return self.log_panel.begin_file(source, name=name, current=current, total=total)

    def log_file_event(self, msg, tag=None, file_id=None, callback=None):
        self.log_panel.log_file_event(msg, tag=tag, file_id=file_id, callback=callback)

    def log_file_segment(self, t_str, text, count=None, file_id=None):
        self.log_panel.log_file_segment(t_str, text, count=count, file_id=file_id)

    def add_file_output(self, role, path, label=None, file_id=None):
        self.log_panel.add_file_output(role, path, label=label, file_id=file_id)

    def end_file_log(self, status="done", error=None, file_id=None):
        self.log_panel.end_file(status=status, error=error, file_id=file_id)

    def find_file_log_id(self, path):
        return self.log_panel.find_file_id_for_path(path)

    def make_file_logger(self, file_id):
        return self.log_panel.make_file_logger(file_id)

    def clear_log(self):
        self.log_panel.clear()

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
        ui_dialogs.show_help(self)

    def _show_mp3_settings_dialog(self):
        ui_dialogs.show_mp3_settings_dialog(self)

    def _show_output_settings_dialog(self):
        ui_dialogs.show_output_settings_dialog(self)

    def _show_model_dialog(self):
        ui_dialogs.show_model_dialog(self)

    def _show_cursor_api_key_dialog(self):
        ui_dialogs.show_ai_api_keys_dialog(self)

    def _show_ai_api_keys_dialog(self):
        ui_dialogs.show_ai_api_keys_dialog(self)

    def _center_toplevel(self, win, parent=None):
        ui_dialogs.center_toplevel(self, win, parent)

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
        self._persist_settings()
        if event:
            return "break"

    @staticmethod
    def _sanitize_folder_name(name):
        """Заменяет символы, недопустимые в имени каталога Windows, на _."""
        s = re.sub(r'[\\/:*?"<>|]', "_", name)
        s = s.strip().rstrip(". ")
        return s if s else "_"

    def _load_output_settings_from_saved(self, saved):
        """Завантажує режими збереження; сумісність зі старим output_dir (порожньо / abs / relative)."""
        mode = (saved.get("output_mode") or "").strip()
        named = (saved.get("output_named_folder") or "").strip()
        out_dir = normalize_display_path(saved.get("output_dir", "") or "")
        if mode not in ("beside", "custom", "named_folder"):
            if not out_dir:
                mode = "beside"
            elif os.path.isabs(out_dir):
                mode = "custom"
            else:
                mode = "named_folder"
                named = named or out_dir
                out_dir = ""
        if mode == "named_folder" and not named:
            named = "{basename}"
        self.output_mode.set(mode or "beside")
        self.output_dir.set(out_dir if (mode == "custom" or os.path.isabs(out_dir)) else "")
        self.output_named_folder.set(named or "{basename}")

        mp3_mode = (saved.get("mp3_output_mode") or "").strip()
        mp3_dir = normalize_display_path(saved.get("mp3_output_dir", "") or "")
        if mp3_mode not in ("inherit", "beside", "custom"):
            mp3_mode = "custom" if mp3_dir and os.path.isabs(mp3_dir) else "inherit"
        self.mp3_output_mode.set(mp3_mode)
        self.mp3_output_dir.set(mp3_dir if (mp3_mode == "custom" or os.path.isabs(mp3_dir)) else "")

    def _ensure_dir(self, out, fallback):
        try:
            os.makedirs(out, exist_ok=True)
            return out
        except OSError:
            return fallback

    def _resolve_output_dir(self, path, opts=None):
        """Каталог для TXT/SRT/Cursor та інших результатів (окрім окремого MP3)."""
        opts = opts or {}
        mode = (opts.get("output_mode") if opts.get("output_mode") is not None else self.output_mode.get()) or "beside"
        source_dir = os.path.dirname(os.path.abspath(path))
        if mode == "custom":
            raw = (opts.get("output_dir") if "output_dir" in opts else (self.output_dir.get() or "")).strip()
            raw = normalize_display_path(raw)
            if raw and os.path.isabs(raw):
                return self._ensure_dir(os.path.normpath(raw), source_dir)
            return source_dir
        if mode == "named_folder":
            template = (
                opts.get("output_named_folder")
                if opts.get("output_named_folder") is not None
                else (self.output_named_folder.get() or "")
            ).strip() or "{basename}"
            basename = os.path.splitext(os.path.basename(path))[0]
            folder = template.replace("{basename}", basename).replace("{name}", basename)
            safe_name = self._sanitize_folder_name(folder)
            return self._ensure_dir(os.path.join(source_dir, safe_name), source_dir)
        return source_dir

    def _resolve_mp3_output_dir(self, path, opts=None):
        """Каталог для *_audio.mp3: inherit (з «Сохранение») | beside | custom."""
        opts = opts or {}
        mode = (
            opts.get("mp3_output_mode")
            if opts.get("mp3_output_mode") is not None
            else self.mp3_output_mode.get()
        ) or "inherit"
        source_dir = os.path.dirname(os.path.abspath(path))
        if mode == "inherit":
            return self._resolve_output_dir(path, opts)
        if mode == "custom":
            raw = (
                opts.get("mp3_output_dir")
                if "mp3_output_dir" in opts
                else (self.mp3_output_dir.get() or "")
            ).strip()
            raw = normalize_display_path(raw)
            if raw and os.path.isabs(raw):
                return self._ensure_dir(os.path.normpath(raw), source_dir)
            return source_dir
        return source_dir

    def _open_watch_dirs_dialog(self):
        """Вікно списку каталогів слідкування (Зберегти / закриття = скасування)."""
        current = parse_watch_dirs(self.watch_dir.get())

        def on_save(dirs):
            self.watch_dir.set(serialize_watch_dirs(dirs))
            self._persist_settings()
            if self.watch_enabled.get():
                if valid_watch_dirs(dirs):
                    self.queue_ctrl.start_watch()
                else:
                    self.queue_ctrl.stop_watch()
                    self.watch_enabled.set(False)
                    messagebox.showerror(t("error"), t("watch_folder_empty_error"))

        open_watch_dirs_dialog(
            self.root,
            current,
            on_save=on_save,
            center_fn=self._center_toplevel,
        )

    def _on_watch_toggled(self):
        """Включение/выключение слежения за каталогом."""
        if self.watch_enabled.get():
            dirs = valid_watch_dirs(self.watch_dir.get())
            if not dirs:
                open_watch_dirs_dialog(
                    self.root,
                    parse_watch_dirs(self.watch_dir.get()),
                    on_save=lambda chosen: self.watch_dir.set(serialize_watch_dirs(chosen)),
                    center_fn=self._center_toplevel,
                )
                dirs = valid_watch_dirs(self.watch_dir.get())
                if not dirs:
                    self.watch_enabled.set(False)
                    messagebox.showerror(t("error"), t("watch_folder_empty_error"))
                    self._persist_settings()
                    return
            self.queue_ctrl.start_watch()
        else:
            self.queue_ctrl.stop_watch()
            self.log(t("watch_stopped"))
        self._persist_settings()

    def prepare_close(self):
        """Зупинити слідкування, трей та зберегти налаштування перед закриттям (викликається з main.py)."""
        self.queue_ctrl.stop_watch()
        if self._tray_icon:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        try:
            self.log_panel.flush()
        except Exception:
            pass
        self._persist_settings()

    def _model_button_label(self):
        """Текст кнопки выбора модели: текущая модель (короткое имя)."""
        return self.whisper_model.get() or DEFAULT_MODEL

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
                # Run via cmd /c with quoted path so paths with spaces and special chars work.
                # CREATE_NO_WINDOW prevents flashing cmd windows on Windows.
                subprocess.Popen(
                    ["cmd", "/c", f'"{bat_path}"'],
                    cwd=BASE_DIR,
                    **win_no_window_kwargs(),
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

    def _persist_settings(self):
        """Зберігає поточні налаштування в settings.json (викликається при закритті та при зміні слідкування)."""
        save_app_settings({
            "language": self.ui_language.get(),
            "output_mode": self.output_mode.get() or "beside",
            "output_dir": normalize_display_path((self.output_dir.get() or "").strip()),
            "output_named_folder": (self.output_named_folder.get() or "").strip() or "{basename}",
            "mp3_output_mode": self.mp3_output_mode.get() or "inherit",
            "mp3_output_dir": normalize_display_path((self.mp3_output_dir.get() or "").strip()),
            "watch_dir": serialize_watch_dirs(parse_watch_dirs(self.watch_dir.get())),
            "watch_enabled": self.watch_enabled.get(),
            "device_mode": self.device_mode.get(),
            "play_sound_on_finish": self.play_sound_on_finish.get(),
            "save_audio_mp3": self.save_audio_mp3.get(),
            "send_txt_to_ai": self.send_txt_to_ai.get(),
            "send_txt_to_cursor": self.send_txt_to_ai.get(),
            "export_md_to_docx": self.export_md_to_docx.get(),
            "ai_provider": normalize_provider_id(self.ai_provider.get()),
            "cursor_api_key": (self.cursor_api_key.get() or "").strip(),
            "gemini_api_key": (self.gemini_api_key.get() or "").strip(),
            "gemini_model": (self.gemini_model.get() or "").strip() or "gemini-2.0-flash",
            "azure_openai_endpoint": (self.azure_openai_endpoint.get() or "").strip(),
            "azure_openai_api_key": (self.azure_openai_api_key.get() or "").strip(),
            "azure_openai_deployment": (self.azure_openai_deployment.get() or "").strip(),
            "azure_openai_api_version": (
                (self.azure_openai_api_version.get() or "").strip()
                or "2024-08-01-preview"
            ),
            "tray_mode": self.tray_mode.get(),
            "whisper_model": self.whisper_model.get(),
            "has_nvidia": self.has_nvidia,
            "gpu_model": self.gpu_model,
        })

    def _on_send_txt_to_ai_toggled(self):
        self._persist_settings()

    def _on_send_txt_to_cursor_toggled(self):
        self._on_send_txt_to_ai_toggled()

    def _on_export_md_to_docx_toggled(self):
        self._persist_settings()
        if not self.export_md_to_docx.get():
            return
        from whisperfast.core.pandoc_export import is_pandoc_available
        from whisperfast.setup.external_tools import log_pandoc_install_howto, pandoc_missing_dialog_text

        if not is_pandoc_available():
            self.log(t("pandoc_not_found"))
            log_pandoc_install_howto(self.log)
            messagebox.showwarning(t("export_md_to_docx"), pandoc_missing_dialog_text())

    def resolve_output_paths(self, paths):
        """Якщо файл(и) вже існують — запитати перезапис або зберегти з суфіксом _HHMM."""
        from whisperfast.core.output_conflict import ask_overwrite_via_tk, resolve_output_paths

        return resolve_output_paths(
            paths,
            ask_overwrite=lambda p, alt: ask_overwrite_via_tk(self, p, alt),
        )

    def resolve_output_path(self, path):
        return self.resolve_output_paths([path])[0]

    def _maybe_log_all_complete(self, send_txt_to_cursor, will_continue):
        self.ai_jobs.maybe_log_all_complete(send_txt_to_cursor, will_continue)

    def _export_markdown_to_docx(self, md_path, log_file_id=None):
        """MD → DOCX через Pandoc."""
        from whisperfast.core.pandoc_export import convert_markdown_with_pandoc, office_output_path

        out = self.resolve_output_path(office_output_path(md_path, "docx"))
        try:
            created_path = convert_markdown_with_pandoc(md_path, output_path=out, fmt="docx")
        except Exception as e:
            if log_file_id:
                self.log_file_event(
                    t("pandoc_export_error", fmt="docx", error=str(e)),
                    file_id=log_file_id,
                )
            else:
                self.log(t("pandoc_export_error", fmt="docx", error=str(e)))
            return []
        self.queue_ctrl.register_output_paths([created_path])
        fid = log_file_id or self.find_file_log_id(md_path)
        if fid:
            self.add_file_output("docx", created_path, file_id=fid)
        else:
            self.log(t("pandoc_docx_created", name=os.path.basename(created_path)))
            self.log(created_path, "link")
        return [created_path]

    def _edit_redactor_file(self):
        self.ai_jobs.edit_redactor_file()

    def _cursor_job_begin(self):
        self.ai_jobs._job_begin()

    def _cursor_job_end(self):
        self.ai_jobs._job_end()

    def _maybe_play_finish_sound(self, play_requested, send_txt_to_cursor, will_continue):
        self.ai_jobs.maybe_play_finish_sound(play_requested, send_txt_to_cursor, will_continue)

    def _register_ai_job(self, txt_path, export_md_to_docx=None, log_file_id=None):
        return self.ai_jobs.register_job(
            txt_path, export_md_to_docx=export_md_to_docx, log_file_id=log_file_id
        )

    def _log_ai_select_prompt_action(self, msg, job_id):
        self.ai_jobs.log_select_prompt_action(msg, job_id)

    def _schedule_cursor_postprocess(
        self, txt_path, cursor_api_key="", export_md_to_docx=None, job_id=None, log_file_id=None
    ):
        self.ai_jobs.schedule_postprocess(
            txt_path,
            cursor_api_key=cursor_api_key,
            export_md_to_docx=export_md_to_docx,
            job_id=job_id,
            log_file_id=log_file_id,
        )

    def _pump_cursor_prompt_queue(self):
        self.ai_jobs.pump_prompt_queue()

    def _open_cursor_prompt_dialog(self, job_id):
        self.ai_jobs.open_prompt_dialog(job_id)

    def _ai_credentials(self):
        return self.ai_jobs.credentials()

    def _start_ai_after_prompt_choice(self, job, prompts, provider_id):
        self.ai_jobs.start_after_prompt_choice(job, prompts, provider_id)

    def _start_cursor_after_prompt_choice(self, job, prompts):
        self.ai_jobs.start_cursor_after_prompt_choice(job, prompts)

    def clear_queue(self):
        self.queue_ctrl.clear()

    def delete_selected_queue_items(self, event=None):
        """Удаляет выделенные строки из очереди и сохраняет изменения."""
        selected = self.queue_list.selection()
        if not selected:
            return "break" if event is not None else None

        indices = []
        for iid in selected:
            try:
                indices.append(self.queue_list.index(iid))
            except tk.TclError:
                continue
        if not indices:
            return "break" if event is not None else None

        next_index = min(indices)
        self.queue_ctrl.delete_indices(indices)

        remaining = self.queue_list.get_children()
        if remaining:
            iid = remaining[min(next_index, len(remaining) - 1)]
            self.queue_list.selection_set(iid)
            self.queue_list.focus(iid)
            self.queue_list.see(iid)
        return "break" if event is not None else None

    def _on_queue_context_menu(self, event):
        """Показывает меню строки, сохраняя групповое выделение."""
        iid = self.queue_list.identify_row(event.y)
        if not iid:
            return "break"
        if iid not in self.queue_list.selection():
            self.queue_list.selection_set(iid)
            self.queue_list.focus(iid)
        self.queue_list.focus_set()
        try:
            self.queue_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.queue_menu.grab_release()
        return "break"

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
        self.queue_ctrl.add_files(file_paths)

    # --- DRAG & DROP / LISTBOX ---

    def on_drop(self, e):
        """Обработчик события Drag & Drop через централизованный контроллер"""
        dropped_data = e.data
        file_paths = process_dropped_files(dropped_data, tk_root=self.root)
        if file_paths:
            self.queue_ctrl.add_files(file_paths)

    def on_drag_start(self, event):
        iid = self.queue_list.identify_row(event.y)
        if iid and (event.state & 0x0001):
            return self._on_queue_shift_click(event)
        self._drag_iid = iid
        try:
            self._drag_index = self.queue_list.index(iid) if iid else -1
        except tk.TclError:
            self._drag_index = -1

    def _on_queue_shift_click(self, event):
        """Shift+клік по рядку черги — відкрити розташування файлу в Провіднику."""
        iid = self.queue_list.identify_row(event.y)
        if not iid:
            return "break"
        try:
            idx = self.queue_list.index(iid)
        except tk.TclError:
            return "break"
        if 0 <= idx < len(self.queue):
            open_file_location(self.queue[idx]["path"])
        return "break"

    def on_drag_motion(self, event):
        iid = self.queue_list.identify_row(event.y)
        if not iid or self._drag_index < 0:
            return
        try:
            idx = self.queue_list.index(iid)
        except tk.TclError:
            return
        if idx != self._drag_index and 0 <= idx < len(self.queue):
            self.queue_ctrl.reorder(self._drag_index, idx)
            self._drag_index = idx

    def setup_log_styles(self):
        self.log_panel.setup_styles()

    def on_day_header_click(self, event):
        self.log_panel.on_day_header_click(event)

    def on_link_click(self, event):
        return self.log_panel.on_link_click(event)

    def on_action_click(self, event):
        self.log_panel.on_action_click(event)

    def _copy_log_event(self, event=None):
        return self.log_panel._copy_event(event)

    def copy_log_selection(self):
        self.log_panel.copy_selection()

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
        self.mp3_settings_btn.config(text=t("save_audio_mp3"))
        self.edit_redactor_btn.config(text=t("send_txt_to_ai"))
        self.cursor_api_key_btn.config(text=t("ai_api_keys_button"))
        self.export_md_docx_label.config(text=t("export_md_to_docx"))
        self.system_btn.config(text=t("system_check"))
        self.updates_btn.config(text=t("updates"))
        self.dependencies_btn.config(text=t("dependencies"))
        self.model_btn.config(text=self._model_button_label())
        self.output_folder_btn.config(text=t("output_folder"))
        self.watch_dirs_btn.config(text=t("watch_folder_label"))
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
            self.log_panel.update_copy_menu_label()
        except (tk.TclError, IndexError):
            pass
        try:
            self.queue_menu.entryconfig(0, label=t("delete_from_queue"))
        except (tk.TclError, IndexError):
            pass