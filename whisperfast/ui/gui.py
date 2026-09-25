import os
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

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
from whisperfast.utils import normalize_display_path, normalize_queue_note
from whisperfast.core.model_manager import WhisperModelSingleton
from whisperfast.core.transcription import run_queue, save_files as save_transcription_files
from whisperfast.setup.installer import install_dependencies, check_system, check_updates
from whisperfast.updates.app_updates import apply_app_update, check_app_update
from whisperfast.updates.model_updates import apply_whisper_model_updates
from whisperfast.setup.gpu_info import refresh_gpu_settings
from whisperfast.core.input_files import process_dropped_files
from whisperfast.core.source_relocate import named_folder_output_dir
from whisperfast.core.queue_manager import (
    QueueController,
    parse_watch_dirs,
    serialize_watch_dirs,
    valid_watch_dirs,
)
from whisperfast.postprocess.cursor_postprocess import ensure_redactor_file
from whisperfast.postprocess.providers import PROVIDER_CURSOR, normalize_provider_id
from whisperfast.i18n import t, set_language
from whisperfast.library import get_library
from whisperfast.settings import (
    format_chat_ids,
    format_chat_names,
    load_app_settings,
    normalize_chat_ids,
    normalize_chat_names,
    normalize_default_prompt_nums,
    parse_chat_id_text,
    save_app_settings,
)
from whisperfast.postprocess.prompt_rules import normalize_prompt_rules
from whisperfast.open_path import open_file_location
from whisperfast.platform_util import win_no_window_kwargs
from whisperfast import autostart as win_autostart
from whisperfast.ui.widgets import (
    Tooltip,
    placeholder_entry,
    UI_DESIGN_WIDTH,
    UI_MIN_SCALE,
    UI_BASE_FONT_SIZE,
)
from whisperfast.ui import tray as tray_ui
from whisperfast.ui import dialogs as ui_dialogs
from whisperfast.ui import capture_ui
from whisperfast.ui import toolbar_icons
from whisperfast.ui.capture_settings import show_capture_settings_dialog
from whisperfast.ui.log_panel import LogPanel
from whisperfast.ui.ai_jobs import AiJobQueue
from whisperfast.ui.archive import show_archive_window
from whisperfast.core.capture_prefs import snapshot_capture_settings
from whisperfast.core.capture_names import format_clip_seconds



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

        try:
            from whisperfast.setup.external_tools import bind_runtime_tool_paths

            bind_runtime_tool_paths()
        except Exception:
            pass

        # Кастомная иконка окна и панели задач (favicon.ico); пути через config.BASE_DIR
        self._icon_path = os.path.join(RESOURCES_DIR, "favicon.ico")
        if os.path.exists(self._icon_path):
            try:
                self.root.iconbitmap(self._icon_path)
            except Exception:
                pass

        self.log_panel = LogPanel(self.root)
        self.ai_jobs = AiJobQueue(self)
        self.library = get_library()
        self._i18n_windows = []  # відкриті Toplevel з refresh при зміні мови
        self._help_window = None
        self._release_notes_window = None
        self._archive_window = None

        # Состояние приложения: очередь — QueueController (request_queue.json + слідкування)
        self.queue_ctrl = QueueController(
            request_queue_file=os.path.join(BASE_DIR, "request_queue.json"),
            log_func=self.log,
            root_after=lambda ms, fn: self.root.after(ms, fn),
        )
        self.queue = self.queue_ctrl.queue  # сумісність: той самий list
        self.log_panel.on_note_commit = self._on_log_note_commit
        self.log_panel.on_select_prompts = self.ai_jobs.start_prompts_for_log_file
        self.cancel_requested = False
        self._process_queue_lock = threading.Lock()  # только одна обработка очереди одновременно
        
        # Переменные интерфейса
        self.device_mode = tk.StringVar(value="AUTO")
        self.keep_gpu_awake = tk.BooleanVar(value=False)
        self.lang_mode = tk.StringVar(value=LANG_AUTO_VALUE)  # AUTO для языка транскрипции
        self.output_dir = tk.StringVar()
        self.output_mode = tk.StringVar(value="beside")  # beside | custom | named_folder | custom_named
        self.output_named_folder = tk.StringVar(value="{basename}")
        self.mp3_output_mode = tk.StringVar(value="inherit")  # inherit | beside | custom
        self.mp3_output_dir = tk.StringVar()
        self.watch_dir = tk.StringVar()  # каталоги через кому (settings.json)
        self.watch_enabled = tk.BooleanVar(value=False)
        self.autostart_enabled = tk.BooleanVar(value=win_autostart.is_enabled())
        self.play_sound_on_finish = tk.BooleanVar(value=False)  # По умолчанию снят
        self.save_audio_mp3 = tk.BooleanVar(value=False)  # Сохранять извлечённое аудио в MP3
        self.send_txt_to_ai = tk.BooleanVar(value=False)
        self.send_txt_to_cursor = self.send_txt_to_ai  # alias для сумісності
        self.export_md_to_docx = tk.BooleanVar(value=False)
        self.ai_provider = tk.StringVar(value=PROVIDER_CURSOR)
        self.cursor_api_key = tk.StringVar(value="")
        self.gemini_api_key = tk.StringVar(value="")
        self.gemini_model = tk.StringVar(value="gemini-2.0-flash")
        self.gemini_oauth_refresh_token = tk.StringVar(value="")
        self.gemini_oauth_email = tk.StringVar(value="")
        self.google_oauth_client_id = tk.StringVar(value="")
        self.google_oauth_client_secret = tk.StringVar(value="")
        self.google_cloud_project_id = tk.StringVar(value="")
        self.anthropic_api_key = tk.StringVar(value="")
        self.claude_model = tk.StringVar(value="claude-sonnet-4-5")
        self.azure_openai_endpoint = tk.StringVar(value="")
        self.azure_openai_api_key = tk.StringVar(value="")
        self.azure_openai_deployment = tk.StringVar(value="")
        self.azure_openai_api_version = tk.StringVar(value="2024-08-01-preview")
        self.ollama_base_url = tk.StringVar(value="http://127.0.0.1:11434")
        self.ollama_model = tk.StringVar(value="llama3.2")
        self.openai_compatible_base_url = tk.StringVar(value="")
        self.openai_compatible_api_key = tk.StringVar(value="")
        self.openai_compatible_model = tk.StringVar(value="")
        self.telegram_bot_token = tk.StringVar(value="")
        self.telegram_api_id = tk.StringVar(value="")
        self.telegram_api_hash = tk.StringVar(value="")
        self.telegram_bot_api_exe = tk.StringVar(value="")
        self.telegram_api_base = tk.StringVar(value="http://127.0.0.1:8081")
        self.telegram_allowed_chat_ids_text = tk.StringVar(value="")
        self.telegram_work_dir = tk.StringVar(value="")
        self.telegram_social_to_queue = tk.BooleanVar(value=False)
        self.telegram_learn_mode = tk.BooleanVar(value=False)
        self.telegram_social_quality = tk.StringVar(value="best")
        self.telegram_listener_on = tk.BooleanVar(value=False)
        self._telegram_listener_wanted = False
        self.telegram_mode = tk.StringVar(value="bot")
        self.telegram_phone = tk.StringVar(value="")
        self.telegram_self_chat_names_text = tk.StringVar(value="")
        self.telegram_ignored_chat_names_text = tk.StringVar(value="")
        self.telegram_ignored_chat_ids = []
        self.export_json = tk.BooleanVar(value=False)
        self.export_vtt = tk.BooleanVar(value=False)
        self.word_timestamps = tk.BooleanVar(value=False)
        self.diarization_enabled = tk.BooleanVar(value=False)
        self.capture_consent_shown = tk.BooleanVar(value=False)
        self.ai_month_budget = tk.DoubleVar(value=0.0)
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
        if "keep_gpu_awake" in saved:
            self.keep_gpu_awake.set(bool(saved.get("keep_gpu_awake")))
        else:
            self.keep_gpu_awake.set(self.device_mode.get() == "GPU")
        self.play_sound_on_finish.set(bool(saved.get("play_sound_on_finish", False)))
        self.save_audio_mp3.set(bool(saved.get("save_audio_mp3", False)))
        send_ai = saved.get("send_txt_to_ai")
        if send_ai is None:
            send_ai = saved.get("send_txt_to_cursor", False)
        self.send_txt_to_ai.set(bool(send_ai))
        self.ai_default_prompt_nums = normalize_default_prompt_nums(
            saved.get("ai_default_prompt_nums")
        )
        self.ai_auto_process = tk.BooleanVar(value=bool(saved.get("ai_auto_process", False)))
        self.export_md_to_docx.set(bool(saved.get("export_md_to_docx", False)))
        self.ai_provider.set(normalize_provider_id(saved.get("ai_provider") or PROVIDER_CURSOR))
        self.cursor_api_key.set((saved.get("cursor_api_key") or "").strip())
        self.gemini_api_key.set((saved.get("gemini_api_key") or "").strip())
        self.gemini_model.set(
            (saved.get("gemini_model") or "").strip() or "gemini-2.0-flash"
        )
        self.gemini_oauth_refresh_token.set((saved.get("gemini_oauth_refresh_token") or "").strip())
        self.gemini_oauth_email.set((saved.get("gemini_oauth_email") or "").strip())
        self.google_oauth_client_secret.set((saved.get("google_oauth_client_secret") or "").strip())
        self.google_cloud_project_id.set((saved.get("google_cloud_project_id") or "").strip())
        self.anthropic_api_key.set((saved.get("anthropic_api_key") or "").strip())
        self.claude_model.set(
            (saved.get("claude_model") or "").strip() or "claude-sonnet-4-5"
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
        self.ollama_base_url.set(
            (saved.get("ollama_base_url") or "").strip() or "http://127.0.0.1:11434"
        )
        self.ollama_model.set((saved.get("ollama_model") or "").strip() or "llama3.2")
        self.openai_compatible_base_url.set(
            (saved.get("openai_compatible_base_url") or "").strip()
        )
        self.openai_compatible_api_key.set(
            (saved.get("openai_compatible_api_key") or "").strip()
        )
        self.openai_compatible_model.set(
            (saved.get("openai_compatible_model") or "").strip()
        )
        self.telegram_bot_token.set((saved.get("telegram_bot_token") or "").strip())
        self.telegram_api_id.set(str(saved.get("telegram_api_id") or "").strip())
        self.telegram_api_hash.set((saved.get("telegram_api_hash") or "").strip())
        self.telegram_bot_api_exe.set((saved.get("telegram_bot_api_exe") or "").strip())
        self.telegram_api_base.set(
            (saved.get("telegram_api_base") or "").strip() or "http://127.0.0.1:8081"
        )
        self.telegram_allowed_chat_ids_text.set(format_chat_ids(saved.get("telegram_allowed_chat_ids")))
        self.telegram_work_dir.set((saved.get("telegram_work_dir") or "").strip())
        self.telegram_social_to_queue.set(bool(saved.get("telegram_social_to_queue", False)))
        self.telegram_learn_mode.set(bool(saved.get("telegram_learn_mode", False)))
        from whisperfast.telegram.links import normalize_social_quality

        self.telegram_social_quality.set(normalize_social_quality(saved.get("telegram_social_quality")))
        self._telegram_listener_wanted = bool(saved.get("telegram_listener_enabled", False))
        mode = str(saved.get("telegram_mode") or "bot").strip().lower()
        self.telegram_mode.set(mode if mode in ("bot", "account") else "bot")
        self.telegram_phone.set((saved.get("telegram_phone") or "").strip())
        self.telegram_self_chat_names_text.set(format_chat_names(saved.get("telegram_self_chat_names")))
        self.telegram_ignored_chat_names_text.set(
            format_chat_names(saved.get("telegram_ignored_chat_names"))
        )
        self.telegram_ignored_chat_ids = normalize_chat_ids(saved.get("telegram_ignored_chat_ids"))
        self.export_json.set(bool(saved.get("export_json", False)))
        self.export_vtt.set(bool(saved.get("export_vtt", False)))
        self.word_timestamps.set(bool(saved.get("word_timestamps", False)))
        self.diarization_enabled.set(bool(saved.get("diarization_enabled", False)))
        self.capture_consent_shown.set(bool(saved.get("capture_consent_shown", False)))
        self.capture_cfg = snapshot_capture_settings(saved)
        self.google_oauth_client_id.set(
            (saved.get("google_oauth_client_id") or "").strip()
            or str((self.capture_cfg or {}).get("google_calendar_client_id") or "").strip()
        )
        self.capture_clip_var = tk.StringVar(
            value=format_clip_seconds(int(self.capture_cfg.get("capture_clip_seconds") or 122))
        )
        try:
            self.ai_month_budget.set(float(saved.get("ai_month_budget") or 0.0))
        except (TypeError, ValueError, tk.TclError):
            self.ai_month_budget.set(0.0)
        self.ai_prompt_rules = normalize_prompt_rules(saved.get("ai_prompt_rules"))
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
        capture_ui.bind_capture_hotkey(self)
        self.root.after(400, lambda: capture_ui.recover_interrupted_captures(self))
        capture_ui.start_background_polls(self)

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
        self._restore_telegram_listener()

        if not DND_OK:
            self.log(t("warning_dnd"))
        removed_shortcuts = win_autostart.cleanup_extra_autostart()
        if removed_shortcuts:
            self.log(t("autostart_shortcuts_removed", names=", ".join(removed_shortcuts)))

        # Иконка в системном трее (зависит от переключателя Панель / Трей / Панель + Трей)
        self._apply_tray_mode()

        # Перевірка нової версії на GitHub (фоном, після показу вікна)
        self.root.after(2000, self._schedule_startup_app_update_check)


    def _setup_tray(self):
        tray_ui.setup_tray(self)

    def _apply_tray_mode(self):
        tray_ui.apply_tray_mode(self)

    def _tray_show_window(self):
        tray_ui.tray_show_window(self)

    def _tray_quit(self):
        tray_ui.tray_quit(self)

    TRAY_MODE_KEYS = ("panel", "tray", "panel_tray")
    RECOG_LANG_LABELS = ("AUTO", "RU", "UK", "EN")

    def _recog_lang_value(self, label):
        return LANG_AUTO_VALUE if label == "AUTO" else (label or "").lower()

    def _recog_lang_label(self, value):
        if not value or value == LANG_AUTO_VALUE:
            return "AUTO"
        return str(value).upper()

    def _show_recog_lang_dialog(self):
        """Іконка мови розпізнавання: окреме вікно AUTO, RU, UK, EN."""
        dialog = tk.Toplevel(self.root)
        dialog.title(t("language_switcher"))
        dialog.transient(self.root)
        dialog.resizable(False, False)
        current = self._recog_lang_label(self.lang_mode.get())
        if current not in self.RECOG_LANG_LABELS:
            current = "AUTO"
        chosen = tk.StringVar(value=current)
        box = ttk.Frame(dialog, padding=16)
        box.pack()
        names = {
            "AUTO": t("recog_lang_auto"),
            "RU": t("recog_lang_ru"),
            "UK": t("recog_lang_uk"),
            "EN": t("recog_lang_en"),
        }
        for label in self.RECOG_LANG_LABELS:
            ttk.Radiobutton(box, text=names[label], value=label, variable=chosen).pack(anchor="w", pady=2)

        def apply_choice():
            self.lang_mode.set(self._recog_lang_value(chosen.get()))
            toolbar_icons.apply_recog_state(self)
            self._persist_settings()
            dialog.destroy()

        ttk.Button(box, text=t("save"), command=apply_choice).pack(anchor="e", pady=(12, 0))
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.bind("<Escape>", lambda event: dialog.destroy())
        self._center_toplevel(dialog)

    def _on_ui_lang_combo(self, event=None):
        val = (self.ui_lang_combo.get() or "").strip()
        if val in SUPPORTED_LANGUAGES and val != self.ui_language.get():
            self.ui_language.set(val)

    def _load_queue_from_file(self):
        self.queue_ctrl.load_from_file()

    def _save_queue_to_file(self):
        self.queue_ctrl.save_to_file()

    def _set_tg_column_heading(self):
        photo = (getattr(self, "_toolbar_photos", None) or {}).get("telegram")
        try:
            if photo is not None:
                self.queue_list.heading("tg", text="", image=photo, anchor="center")
            else:
                self.queue_list.heading("tg", text="TG", anchor="center")
        except tk.TclError:
            pass

    def _set_remove_column_heading(self):
        """Trash icon in the header of the per-row delete column."""
        photo = (getattr(self, "_toolbar_photos", None) or {}).get("clear_log")
        try:
            if photo is not None:
                self.queue_list.heading("remove", text="", image=photo, anchor="center")
            else:
                self.queue_list.heading("remove", text="×", anchor="center")
        except tk.TclError:
            pass

    def _refresh_queue_treeview(self):
        self.queue_ctrl.refresh_treeview()

    def _on_queue_row_double_click(self, event):
        """Редактирование диапазона времени по двойному клику; колонка «Кратко» — прямо в ячейке."""
        iid = self.queue_list.identify_row(event.y)
        if not iid:
            return
        try:
            idx = self.queue_list.index(iid)
        except tk.TclError:
            return
        if idx < 0 or idx >= len(self.queue):
            return
        if self._queue_column_name(event) in ("remove", "tg"):
            return
        if self._queue_column_name(event) == "note":
            self._edit_queue_note_cell(iid, idx)
            return
        if self._queue_column_name(event) == "ai":
            return
        row = self.queue[idx]
        d = tk.Toplevel(self.root)
        d.title(t("edit_row_title"))
        d.transient(self.root)
        d.grab_set()
        note_var = tk.StringVar(value=row.get("note") or "")
        start_var = tk.StringVar(value=row["start"])
        seg1_var = tk.StringVar(value=row.get("end_segment_1", ""))
        seg2_var = tk.StringVar(value=row.get("end_segment_2", ""))
        end_var = tk.StringVar(value=row["end"])
        ttk.Label(d, text=t("col_note")).grid(row=0, column=0, padx=5, pady=3, sticky="w")
        e_note = placeholder_entry(d, note_var, "ph_note", width=42)
        e_note.grid(row=0, column=1, padx=5, pady=3, sticky="we")
        ttk.Label(d, text=t("col_start")).grid(row=1, column=0, padx=5, pady=3, sticky="w")
        placeholder_entry(d, start_var, "ph_time", width=14).grid(row=1, column=1, padx=5, pady=3, sticky="w")
        ttk.Label(d, text=t("col_end_seg1")).grid(row=2, column=0, padx=5, pady=3, sticky="w")
        placeholder_entry(d, seg1_var, "ph_time", width=14).grid(row=2, column=1, padx=5, pady=3, sticky="w")
        ttk.Label(d, text=t("col_end_seg2")).grid(row=3, column=0, padx=5, pady=3, sticky="w")
        placeholder_entry(d, seg2_var, "ph_time", width=14).grid(row=3, column=1, padx=5, pady=3, sticky="w")
        ttk.Label(d, text=t("col_end")).grid(row=4, column=0, padx=5, pady=3, sticky="w")
        placeholder_entry(d, end_var, "ph_time", width=14).grid(row=4, column=1, padx=5, pady=3, sticky="w")
        d.grid_columnconfigure(1, weight=1)

        def apply_and_close():
            note = normalize_queue_note(note_var.get())
            self.queue_ctrl.update_row(
                idx,
                note=note,
                start=(start_var.get() or "").strip() or DEFAULT_START_TIMESTAMP,
                end_segment_1=(seg1_var.get() or "").strip(),
                end_segment_2=(seg2_var.get() or "").strip(),
                end=(end_var.get() or "").strip() or row["end"],
            )
            self._sync_queue_note(idx, note)
            d.destroy()

        ttk.Button(d, text=t("close"), command=d.destroy).grid(row=5, column=0, padx=5, pady=8)
        ttk.Button(d, text=t("ok"), command=apply_and_close).grid(row=5, column=1, padx=5, pady=8, sticky="e")
        self._center_toplevel(d)
        e_note.focus_set()

    def _edit_queue_note_cell(self, iid, idx):
        prev = getattr(self, "_queue_note_editor", None)
        if prev is not None:
            try:
                prev.destroy()
            except tk.TclError:
                pass
            self._queue_note_editor = None
        bbox = self.queue_list.bbox(iid, "note")
        if not bbox:
            return
        x, y, w, h = bbox
        row = self.queue[idx]
        editor = ttk.Entry(self.queue_list)
        editor.place(x=x, y=y, width=max(w, 80), height=h)
        editor.insert(0, row.get("note") or "")
        editor.select_range(0, tk.END)
        editor.focus_set()
        self._queue_note_editor = editor
        committed = {"done": False}

        def commit(event=None):
            del event
            if committed["done"]:
                return
            committed["done"] = True
            text = normalize_queue_note(editor.get())
            try:
                editor.destroy()
            except tk.TclError:
                pass
            if getattr(self, "_queue_note_editor", None) is editor:
                self._queue_note_editor = None
            self.queue_ctrl.update_row(idx, note=text)
            self._sync_queue_note(idx, text)

        def cancel(event=None):
            del event
            committed["done"] = True
            try:
                editor.destroy()
            except tk.TclError:
                pass
            if getattr(self, "_queue_note_editor", None) is editor:
                self._queue_note_editor = None

        editor.bind("<Return>", commit)
        editor.bind("<FocusOut>", commit)
        editor.bind("<Escape>", cancel)

    def _sync_queue_note(self, idx, note):
        if not (0 <= idx < len(self.queue)):
            return
        path = self.queue[idx].get("path")
        file_id = self.find_file_log_id(path) if path else None
        if file_id:
            self.apply_file_note(file_id, note, log_event=False)
        self._refresh_archive_window()

    def _refresh_archive_window(self):
        win = getattr(self, "_archive_window", None)
        if win is None:
            return
        refresh = getattr(win, "_wf_refresh", None)
        if not callable(refresh):
            return
        try:
            refresh()
        except Exception:
            pass

    def apply_file_note(self, file_id, note, log_event=True):
        """Put the queue brief into the work log header and archive «Кратко»."""
        text = normalize_queue_note(note)
        if not file_id:
            return
        try:
            self.log_panel.set_file_note(file_id, text)
        except Exception:
            pass
        if log_event and text:
            try:
                self.log_panel.log_file_event(t("log_file_note", note=text), file_id=file_id)
            except Exception:
                pass
        if text:
            try:
                self.library.set_meta(file_id, summary=text)
            except Exception as e:
                self.log(f"[library] set_meta: {e}")

    def _on_log_note_commit(self, file_id, note):
        """User typed Brief in the log cell — copy to archive and queue."""
        text = normalize_queue_note(note)
        if not file_id:
            return
        try:
            self.library.set_meta(file_id, summary=text)
        except Exception as e:
            self.log(f"[library] set_meta: {e}")
        paths = []
        try:
            job = self.library.get_job(file_id)
            if job:
                if job.get("source"):
                    paths.append(job["source"])
                if job.get("txt_path"):
                    paths.append(job["txt_path"])
        except Exception:
            pass
        try:
            entry = self.log_panel._store.get_file(file_id)
            if entry and entry.get("source"):
                paths.append(entry["source"])
        except Exception:
            pass
        seen = set()
        for path in paths:
            if not path:
                continue
            try:
                key = os.path.normcase(os.path.normpath(path))
            except OSError:
                key = str(path)
            if key in seen:
                continue
            seen.add(key)
            try:
                self.queue_ctrl.set_note_for_path(path, text, only_if_empty=False)
            except Exception:
                pass
        win = getattr(self, "_archive_window", None)
        if win is not None:
            refresh = getattr(win, "_wf_refresh", None)
            if callable(refresh):
                try:
                    refresh()
                except Exception:
                    pass

    def build_ui(self):
        """Создание интерфейса по блокам 1, 2, 3, 4"""
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill="both", expand=True)

        if DND_OK:
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind("<<Drop>>", self.on_drop)

        # === БЛОК 1: ОЧЕРЕДЬ ФАЙЛОВ ===
        header_f = ttk.Frame(main)
        header_f.pack(fill="x", pady=(0, 2))
        
        self.queue_header_label = ttk.Label(header_f, text=t("queue_header"), font=("Segoe UI", 9, "bold"))
        self.queue_header_label.pack(side="left")
        self._toolbar_photos = toolbar_icons.load(self.root)
        self.add_files_btn = ttk.Button(header_f, command=self.add_files_action)
        self.add_files_btn.pack(side="left", padx=(8, 2))
        self.add_directory_btn = ttk.Button(header_f, command=self.add_directory_action)
        self.add_directory_btn.pack(side="left", padx=2)
        self.clear_queue_btn = ttk.Button(header_f, command=self.clear_queue)
        self.clear_queue_btn.pack(side="left", padx=2)
        self.archive_sep = ttk.Label(header_f, text="|")
        self.archive_sep.pack(side="left", padx=(4, 4))
        self.archive_btn = ttk.Button(header_f, command=self._open_archive)
        self.archive_btn.pack(side="left", padx=2)
        self.capture_sep = ttk.Label(header_f, text="|")
        self.capture_sep.pack(side="left", padx=(4, 4))
        self.capture_settings_btn = ttk.Button(header_f, command=self._open_capture_settings)
        self.capture_settings_btn.pack(side="left", padx=(0, 4))
        self.capture_btn = ttk.Button(header_f, command=self._toggle_capture)
        self.capture_btn.pack(side="left", padx=2)
        self.capture_pause_btn = ttk.Button(
            header_f,
            command=self._toggle_capture_pause,
            state="disabled",
        )
        self.capture_pause_btn.pack(side="left", padx=2)
        self.capture_clip_btn = ttk.Button(
            header_f,
            command=self._save_capture_clip,
            state="disabled",
        )
        self.capture_clip_btn.pack(side="left", padx=(2, 2))
        self.capture_clip_entry = placeholder_entry(header_f, self.capture_clip_var, "ph_clip", width=6)
        self.capture_clip_entry.pack(side="left", padx=(0, 2))
        self.capture_clip_entry.bind("<Return>", lambda e: capture_ui.normalize_clip_entry(self))
        self.capture_clip_entry.bind("<FocusOut>", lambda e: capture_ui.normalize_clip_entry(self))
        self.notify_sep = ttk.Label(header_f, text="|")
        self.notify_sep.pack(side="left", padx=(4, 4))
        self.play_sound_btn = ttk.Button(header_f, command=self._toggle_play_sound)
        self.play_sound_btn.pack(side="left", padx=2)
        self.watch_dirs_btn = ttk.Button(header_f)
        self.watch_dirs_btn.pack(side="left", padx=2)
        self.watch_dirs_btn.bind("<Button-1>", self._on_watch_click)
        ttk.Label(header_f, text="|").pack(side="left", padx=(4, 4))
        toolbar_icons.apply_static(self)
        capture_ui.refresh_capture_buttons(self)
        
        # Кнопка Help самая правая
        self.help_btn = ttk.Button(header_f, text=t("help"), width=10, command=self.show_help)
        self.help_btn.pack(side="right")
        
        # Версія/дата — кнопка реліз-нотів (ліворуч від перемикача мови)
        self.version_btn = ttk.Button(
            header_f,
            text=f"v{APP_VERSION} ({APP_DATE})",
            command=self.show_release_notes,
        )
        self.version_btn.pack(side="right", padx=(0, 10))
        # Выбор языка интерфейса слева от Help
        self.lang_selector_frame = ttk.Frame(header_f)
        self.lang_selector_frame.pack(side="right", padx=5)
        ttk.Label(self.lang_selector_frame, text="🌐").pack(side="left", padx=(0, 2))
        self.ui_lang_combo = ttk.Combobox(
            self.lang_selector_frame,
            state="readonly",
            width=5,
            values=list(SUPPORTED_LANGUAGES),
        )
        ui = self.ui_language.get()
        try:
            self.ui_lang_combo.current(
                SUPPORTED_LANGUAGES.index(ui) if ui in SUPPORTED_LANGUAGES else 0
            )
        except (tk.TclError, ValueError):
            self.ui_lang_combo.current(0)
        self.ui_lang_combo.pack(side="left")
        self.ui_lang_combo.bind("<<ComboboxSelected>>", self._on_ui_lang_combo)
        # Системні кнопки — ліворуч від мови інтерфейсу. side=right кладе кожен
        # наступний віджет лівіше, тож пакуємо з правого краю групи.
        self.header_tools_sep = ttk.Label(header_f, text="|")
        self.header_tools_sep.pack(side="right", padx=(4, 4))
        self.autostart_btn = ttk.Button(header_f, command=self._toggle_autostart)
        self.autostart_btn.pack(side="right", padx=2)
        ttk.Label(header_f, text="|").pack(side="right", padx=(4, 4))
        self.tray_mode_btn = ttk.Button(header_f, command=self._show_tray_mode_menu)
        self.tray_mode_btn.pack(side="right", padx=2)
        ttk.Label(header_f, text="|").pack(side="right", padx=(4, 4))
        self.model_btn = ttk.Button(header_f, command=self._show_model_dialog)
        self.model_btn.pack(side="right", padx=2)
        self.device_btn = ttk.Button(header_f, command=self._show_device_dialog)
        self.device_btn.pack(side="right", padx=2)
        ttk.Label(header_f, text="|").pack(side="right", padx=(4, 4))
        self.dependencies_btn = ttk.Button(header_f, command=self._show_environment_menu)
        self.dependencies_btn.pack(side="right", padx=2)

        q_frame = ttk.Frame(main)
        q_frame.pack(fill="both", expand=True, pady=(2, 2))
        cols = ("num", "remove", "filename", "note", "ai", "tg", "start", "end_seg1", "end_seg2", "end", "status")
        self.queue_list = ttk.Treeview(q_frame, columns=cols, show="headings", height=8, selectmode="extended")
        self.queue_list.heading("num", text=t("col_num"))
        self._set_remove_column_heading()
        self.queue_list.heading("filename", text=t("col_filename"))
        self.queue_list.heading("note", text=t("col_note"))
        self.queue_list.heading("ai", text=t("col_ai"))
        self._set_tg_column_heading()
        self.queue_list.heading("start", text=t("col_start"))
        self.queue_list.heading("end_seg1", text=t("col_end_seg1"))
        self.queue_list.heading("end_seg2", text=t("col_end_seg2"))
        self.queue_list.heading("end", text=t("col_end"))
        self.queue_list.heading("status", text=t("col_status"))
        self.queue_list.column("num", width=28, minwidth=28, stretch=False, anchor="center")
        self.queue_list.column("remove", width=26, minwidth=26, stretch=False, anchor="center")
        self.queue_list.column("filename", width=200)
        self.queue_list.column("note", width=180, minwidth=80)
        self.queue_list.column("ai", width=28, minwidth=28, stretch=False, anchor="center")
        self.queue_list.column("tg", width=28, minwidth=28, stretch=False, anchor="center")
        self.queue_list.column("start", width=90)
        self.queue_list.column("end_seg1", width=90)
        self.queue_list.column("end_seg2", width=90)
        self.queue_list.column("end", width=90)
        self.queue_list.column("status", width=88, minwidth=72, stretch=False)
        scroll_q = ttk.Scrollbar(q_frame, orient="vertical", command=self.queue_list.yview)
        self.queue_list.configure(yscrollcommand=scroll_q.set)
        self.queue_list.pack(side="left", fill="both", expand=True, padx=2, pady=2)
        scroll_q.pack(side="right", fill="y")
        self.queue_list.bind("<Double-1>", self._on_queue_row_double_click)
        self.queue_list.bind("<Button-1>", self.on_drag_start)
        self.queue_list.bind("<ButtonRelease-1>", self._on_queue_button_release)
        self.queue_list.bind("<Shift-Button-1>", self._on_queue_shift_click)
        self.queue_list.bind("<B1-Motion>", self.on_drag_motion)
        self.queue_list.bind("<Delete>", self.delete_selected_queue_items)
        self.queue_list.bind("<Button-3>", self._on_queue_context_menu)
        self.queue_menu = tk.Menu(self.root, tearoff=0)
        self.queue_menu.add_command(label=t("delete_from_queue"), command=self.delete_selected_queue_items)

        # Мова, старт, лог і решта кнопок — один горизонтальний ряд
        tools_row = ttk.Frame(main)
        tools_row.pack(fill="x", pady=(2, 2))
        tools_row.columnconfigure(0, weight=1)
        tools_row.columnconfigure(2, weight=1)

        log_side = ttk.Frame(tools_row)
        log_side.grid(row=0, column=0, sticky="w")
        self.log_header_label = ttk.Label(log_side, text=t("log_header"), font=("Segoe UI", 9, "bold"))
        self.log_header_label.pack(side="left")
        self.clear_log_btn = ttk.Button(log_side, command=self.log_panel.clear)
        self.clear_log_btn.pack(side="left", padx=(8, 2))

        tools_center = ttk.Frame(tools_row)
        tools_center.grid(row=0, column=1)

        self.recog_lang_btn = ttk.Button(tools_center, command=self._show_recog_lang_dialog)
        self.recog_lang_btn.pack(side="left", padx=2)
        self.start_btn = ttk.Button(tools_center, command=self.handle_start_logic)
        self.start_btn.pack(side="left", padx=2)
        ttk.Label(tools_center, text="|").pack(side="left", padx=(4, 4))

        self.root.bind_all("<Return>", self._on_enter_key)
        self.root.bind_all("<space>", self._on_space_key)
        self.output_folder_btn = ttk.Button(
            tools_center, command=self._show_output_settings_dialog
        )
        self.output_folder_btn.pack(side="left", padx=2)

        ttk.Label(tools_center, text="|").pack(side="left", padx=(4, 4))
        self.mp3_settings_btn = ttk.Button(tools_center)
        self.mp3_settings_btn.pack(side="left", padx=2)
        self.mp3_settings_btn.bind("<Button-1>", self._on_mp3_click)

        ttk.Label(tools_center, text="|").pack(side="left", padx=(4, 4))
        self.edit_redactor_btn = ttk.Button(tools_center)
        self.edit_redactor_btn.pack(side="left", padx=2)
        self.edit_redactor_btn.bind("<Button-1>", self._on_prompts_click)

        ttk.Label(tools_center, text="|").pack(side="left", padx=(4, 4))
        self.telegram_btn = ttk.Button(tools_center)
        self.telegram_btn.pack(side="left", padx=2)
        self.telegram_btn.bind("<Button-1>", self._on_telegram_click)

        ttk.Label(tools_center, text="|").pack(side="left", padx=(4, 4))
        self.export_md_docx_btn = ttk.Button(tools_center)
        self.export_md_docx_btn.pack(side="left", padx=2)
        self.export_md_docx_btn.bind("<Button-1>", self._on_docx_click)

        cancel_side = ttk.Frame(tools_row)
        cancel_side.grid(row=0, column=2, sticky="e")
        self.cancel_btn = ttk.Button(cancel_side, command=self.cancel_action, state="disabled")
        self.cancel_btn.pack(side="right")
        ensure_redactor_file()
        self._apply_gpu_hold()
        toolbar_icons.apply_static(self)

        self.progress = ttk.Progressbar(main, length=900, maximum=100, mode="determinate")
        self.progress.pack(fill="x", pady=(4, 2))
        self.log_box = scrolledtext.ScrolledText(main, height=18, state="disabled", wrap="word", font=("Consolas", 9))
        self.log_box.pack(fill="both", expand=True, pady=(2, 0))
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
        tip(self.archive_btn, "tooltip_archive")
        tip(self.capture_btn, "tooltip_capture")
        tip(self.capture_pause_btn, "tooltip_capture_pause")
        tip(self.capture_clip_btn, "tooltip_capture_clip")
        tip(self.capture_clip_entry, "tooltip_capture_clip")
        tip(self.capture_settings_btn, "tooltip_capture_settings")
        tip(self.play_sound_btn, "tooltip_play_sound")
        tip(self.help_btn, "tooltip_help")
        tip(self.version_btn, "tooltip_version")
        tip(self.lang_selector_frame, "tooltip_ui_language")
        tip(self.ui_lang_combo, "tooltip_ui_language")
        tip(self.start_btn, "tooltip_start")
        tip(self.device_btn, "tooltip_device")
        tip(self.recog_lang_btn, "tooltip_language_switcher")
        tip(self.mp3_settings_btn, "tooltip_mp3_settings")
        tip(self.edit_redactor_btn, "tooltip_edit_redactor")
        tip(self.telegram_btn, "tooltip_telegram")
        tip(self.export_md_docx_btn, "tooltip_export_md_to_docx")
        tip(self.dependencies_btn, "tooltip_environment")
        self._model_tip = Tooltip(self.model_btn, self._model_tooltip_text(), is_key=False)
        self._tooltips.append(self._model_tip)
        tip(self.tray_mode_btn, "tooltip_tray_mode")
        tip(self.autostart_btn, "tooltip_autostart")
        tip(self.output_folder_btn, "tooltip_output_folder")
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
        for style_name in ("TButton", "TLabel", "TCheckbutton", "TRadiobutton", "TEntry", "TCombobox"):
            try:
                style.configure(style_name, font=font)
            except tk.TclError:
                pass
        try:
            style.configure("TLabelframe.Label", font=font)
        except tk.TclError:
            pass
        self.queue_header_label.config(font=("Segoe UI", font_size, "bold"))
        self.log_header_label.config(font=("Segoe UI", font_size, "bold"))
        # ttk.Button: шрифт уже через style.configure("TButton", ...)
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
        try:
            from whisperfast.core.live_preview import stop_preview

            stop_preview()
        except Exception:
            pass
        self.start_btn.config(state="disabled")
        self.cancel_btn.config(state="normal")
        self._show_progress()
        # Читаем Tk-переменные только в главном потоке и передаём в воркер
        options = {
            "device_mode": self.device_mode.get(),
            "keep_gpu_awake": bool(self.keep_gpu_awake.get()),
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
            "gemini_oauth_refresh_token": (self.gemini_oauth_refresh_token.get() or "").strip(),
            "gemini_oauth_email": (self.gemini_oauth_email.get() or "").strip(),
            "google_oauth_client_id": (self.google_oauth_client_id.get() or "").strip(),
            "google_oauth_client_secret": (self.google_oauth_client_secret.get() or "").strip(),
            "google_cloud_project_id": (self.google_cloud_project_id.get() or "").strip(),
            "anthropic_api_key": (self.anthropic_api_key.get() or "").strip(),
            "claude_model": (self.claude_model.get() or "").strip() or "claude-sonnet-4-5",
            "azure_openai_endpoint": (self.azure_openai_endpoint.get() or "").strip(),
            "azure_openai_api_key": (self.azure_openai_api_key.get() or "").strip(),
            "azure_openai_deployment": (self.azure_openai_deployment.get() or "").strip(),
            "azure_openai_api_version": (
                (self.azure_openai_api_version.get() or "").strip()
                or "2024-08-01-preview"
            ),
            "ollama_base_url": (self.ollama_base_url.get() or "").strip(),
            "ollama_model": (self.ollama_model.get() or "").strip() or "llama3.2",
            "openai_compatible_base_url": (
                (self.openai_compatible_base_url.get() or "").strip()
            ),
            "openai_compatible_api_key": (
                (self.openai_compatible_api_key.get() or "").strip()
            ),
            "openai_compatible_model": (
                (self.openai_compatible_model.get() or "").strip()
            ),
            "export_json": bool(self.export_json.get()),
            "export_vtt": bool(self.export_vtt.get()),
            "word_timestamps": bool(self.word_timestamps.get()),
            "diarization_enabled": bool(self.diarization_enabled.get()),
            "user_name": str((getattr(self, "capture_cfg", None) or {}).get("user_name") or "You"),
            "them_name": str((getattr(self, "capture_cfg", None) or {}).get("them_name") or "Them"),
            "keep_audio": bool((getattr(self, "capture_cfg", None) or {}).get("keep_audio", True)),
            "watch_dirs": parse_watch_dirs(self.watch_dir.get()),
            "_from_watch": bool(from_watch),
        }

        def run_and_release():
            try:
                self.process_queue(mode, target_idx, options)
            finally:
                self._process_queue_lock.release()
                # Whisper далі дренить чергу навіть якщо відкрите вікно «Промты»
                self.root.after(
                    0,
                    lambda: self._continue_watch_queue(from_watch=from_watch),
                )
        threading.Thread(target=run_and_release, daemon=True).start()

    def _continue_watch_queue(self, from_watch=False):
        """Після Whisper — наступні необроблені (не чекає відповіді на промти AI)."""
        self.queue_ctrl.continue_after_processing(
            cancel_requested=self.cancel_requested,
            from_watch=from_watch,
        )

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
        return save_transcription_files(
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

    def _schedule_startup_app_update_check(self):
        """Фонова перевірка GitHub; при новій версії — окремий діалог."""

        def worker():
            try:
                info = check_app_update(log_func=None)
            except Exception:
                return
            if not info.get("needs_update"):
                return
            remote = (info.get("remote") or "").strip()
            if not remote:
                return
            skipped = (load_app_settings().get("skip_app_update_version") or "").strip()
            if skipped and skipped == remote:
                return
            self.root.after(0, lambda i=info: self._show_startup_app_update_dialog(i))

        threading.Thread(target=worker, daemon=True).start()

    def _show_startup_app_update_dialog(self, info):
        current = info.get("current") or ""
        latest = info.get("remote") or ""
        remote_date = info.get("remote_date") or ""

        def on_result(choice):
            if choice == "skip":
                save_app_settings({"skip_app_update_version": latest})
                self.log(t("startup_update_skipped_log", version=latest))
                return
            if choice == "later":
                self.log(t("startup_update_later_log", version=latest))
                return
            if choice == "update":
                # Скинути skip для цієї гілки — користувач явно оновлює
                save_app_settings({"skip_app_update_version": ""})
                self._apply_app_update_interactive()

        ui_dialogs.show_startup_app_update_dialog(
            self,
            current=current,
            latest=latest,
            remote_date=remote_date,
            on_result=on_result,
        )

    def _apply_app_update_interactive(self):
        """Запуск оновлення програми з логуванням і пропозицією перезапуску."""
        if capture_ui.capture_blocks_shutdown():
            messagebox.showinfo(t("capture_title"), t("capture_block_update"), parent=self.root)
            return

        def worker():
            app_result = apply_app_update(log_func=self.log)
            if app_result.get("success") and app_result.get("needs_restart"):
                def ask_restart():
                    if messagebox.askyesno(
                        t("app_update_restart_title"),
                        t("app_update_restart_msg"),
                    ):
                        self._restart_after_app_update(app_result.get("restart_script"))
                self.root.after(0, ask_restart)

        threading.Thread(target=worker, daemon=True).start()

    def run_updates_check(self):
        if capture_ui.capture_blocks_shutdown():
            messagebox.showinfo(t("capture_title"), t("capture_block_update"), parent=self.root)
            return

        def worker():
            from whisperfast.setup.external_tools import install_external_tools

            try:
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
                            if self._process_queue_lock.locked():
                                self.log(t("model_change_while_busy"))
                            else:
                                apply_whisper_model_updates([m[0] for m in models], log_func=self.log)
                                WhisperModelSingleton.reset()
                        if app_info.get("needs_update"):
                            save_app_settings({"skip_app_update_version": ""})
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
                            names = [tool.get("name") for tool in external if tool.get("name")]
                            install_external_tools(
                                self.log,
                                names=names,
                                missing_only=False,
                                upgrade=True,
                            )
            finally:
                self.log(t("updates_ready_to_use"))

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
        if choice is None:
            return

        def worker():
            try:
                install_dependencies(force=choice, log_func=self.log, include_nvidia=True)
            finally:
                self.log(t("dependencies_ready_to_use"))

        threading.Thread(target=worker, daemon=True).start()

    def log(self, msg, tag=None):
        self.log_panel.log(msg, tag)

    def log_action(self, msg, callback):
        self.log_panel.log_action(msg, callback)

    def begin_file_log(self, source, name=None, current=None, total=None):
        file_id = self.log_panel.begin_file(source, name=name, current=current, total=total)
        try:
            from datetime import datetime

            self.library.upsert_job(
                {
                    "id": file_id,
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "source": source,
                    "name": name or os.path.basename(source or ""),
                    "status": "running",
                    "model": (self.whisper_model.get() if hasattr(self, "whisper_model") else "")
                    or "",
                    "language": (self.lang_mode.get() if hasattr(self, "lang_mode") else "") or "",
                },
                # No txt output exists yet, so an FTS reindex here would only
                # index an empty transcript; end_file_log() reindexes once
                # after all outputs for this job are written.
                reindex=False,
            )
        except Exception as e:
            self.log(f"[library] upsert_job: {e}")
        return file_id

    def add_file_output(self, role, path, label=None, file_id=None, reindex=True):
        self.log_panel.add_file_output(role, path, label=label, file_id=file_id)
        if file_id:
            try:
                self.library.add_output(file_id, role, path, label=label, reindex=reindex)
            except Exception as e:
                self.log(f"[library] add_output: {e}")

    def end_file_log(self, status="done", error=None, file_id=None):
        self.log_panel.end_file(status=status, error=error, file_id=file_id)
        if status == "failed" and file_id:
            try:
                entry = self.log_panel._store.get_file(file_id)
                source = str((entry or {}).get("source") or "")
                if source:
                    self.queue_ctrl.set_result(source, error=str(error or t("status_error")))
            except Exception:
                pass
        if file_id:
            try:
                self.library.set_meta(file_id, status=status)
            except Exception as e:
                self.log(f"[library] set_meta: {e}")
            try:
                from whisperfast.telegram.gui_bridge import maybe_deliver_telegram

                maybe_deliver_telegram(self, file_id=file_id)
            except Exception:
                pass

    def set_file_source(self, file_id, path, reindex=True):
        self.log_panel.set_file_source(path, file_id=file_id)
        if file_id:
            try:
                self.library.add_output(file_id, "source", path, reindex=reindex)
            except Exception as e:
                self.log(f"[library] add_output(source): {e}")

    def log_file_event(self, msg, tag=None, file_id=None, callback=None):
        self.log_panel.log_file_event(msg, tag=tag, file_id=file_id, callback=callback)

    def set_file_prompt_callback(self, file_id, callback):
        self.log_panel.set_file_prompt_callback(file_id, callback)

    def set_file_retry_callback(self, file_id, callback):
        self.log_panel.set_file_retry_callback(file_id, callback)

    def log_file_segment(self, t_str, text, count=None, file_id=None):
        self.log_panel.log_file_segment(t_str, text, count=count, file_id=file_id)

    def find_file_log_id(self, path):
        return self.log_panel.find_file_id_for_path(path)

    def make_file_logger(self, file_id):
        return self.log_panel.make_file_logger(file_id)

    def clear_log(self):
        self.log_panel.clear()

    def _on_ui_thread(self, fn):
        if threading.current_thread() is threading.main_thread():
            fn()
            return
        try:
            self.root.after(0, fn)
        except tk.TclError:
            pass

    def _show_progress(self):
        """Смуга вже стоїть над логом. На старті обробки лише скидаємо значення."""
        def apply():
            try:
                self.progress["value"] = 0
            except tk.TclError:
                pass

        self._on_ui_thread(apply)

    def _set_progress_value(self, value):
        """Установка значения прогресс-бара. С фонового потока уходит в главный."""
        def apply(v=value):
            try:
                self.progress["value"] = v
            except tk.TclError:
                pass

        self._on_ui_thread(apply)

    def ask_save_mp3_confirm(self, filename):
        """
        Питає підтвердження «зберегти MP3?» з фонового потоку обробки черги
        (core/transcription.py) через модальний Tk-діалог. Раніше цей виклик
        (import tkinter + messagebox.askyesno) жив прямо в core/transcription.py —
        перенесено сюди, щоб core лишався незалежним від Tkinter (див.
        docs/INTERNAL-ARCHITECTURE.uk.md і docs/CODE-REVIEW.md, розділ 1).
        Очікування відповіді — threading.Event з коротким timeout, щоб
        не блокувати перевірку cancel_requested і відкритого вікна «Промты»
        (той самий контракт, що ask_overwrite_via_tk).
        """
        from whisperfast.core.output_conflict import ai_prompts_dialog_is_open

        if ai_prompts_dialog_is_open(self):
            return False

        key = os.path.basename(str(filename or "")).casefold()
        declined = getattr(self, "_mp3_declined_names", None)
        if declined is None:
            declined = set()
            self._mp3_declined_names = declined
        if key and key in declined:
            return False

        choice = [None]
        answered = [False]
        done = threading.Event()

        def ask():
            try:
                if ai_prompts_dialog_is_open(self):
                    choice[0] = False
                    return
                choice[0] = messagebox.askyesno(
                    t("save_audio_mp3"),
                    t("save_mp3_confirm", filename=filename),
                )
                answered[0] = True
            except Exception:
                choice[0] = False
            finally:
                done.set()

        try:
            self.root.after(0, ask)
        except Exception:
            return False
        while not done.is_set():
            if self.cancel_requested:
                return False
            if ai_prompts_dialog_is_open(self):
                return False
            done.wait(timeout=0.05)
        if answered[0] and choice[0] is False and key:
            declined.add(key)
        return bool(choice[0])

    def reset_ui(self):
        self.start_btn.config(state="normal")
        self.cancel_btn.config(state="disabled")
        capture_ui.sync_log_cancel_button(self)

    def cancel_action(self):
        capture_ui.handle_log_cancel(self)


    def show_help(self):
        ui_dialogs.show_help(self)

    def show_release_notes(self):
        ui_dialogs.show_release_notes(self)

    def _show_mp3_settings_dialog(self):
        ui_dialogs.show_mp3_settings_dialog(self)

    def _show_output_settings_dialog(self):
        ui_dialogs.show_output_settings_dialog(self)

    def _show_model_dialog(self):
        ui_dialogs.show_model_dialog(self)

    def _show_ai_api_keys_dialog(self):
        ui_dialogs.show_ai_api_keys_dialog(self)

    def _telegram_settings_snapshot(self):
        mode = (self.telegram_mode.get() or "bot").strip().lower()
        return {
            "telegram_mode": mode if mode in ("bot", "account") else "bot",
            "telegram_api_id": (self.telegram_api_id.get() or "").strip(),
            "telegram_api_hash": (self.telegram_api_hash.get() or "").strip(),
            "telegram_phone": (self.telegram_phone.get() or "").strip(),
            "telegram_bot_token": (self.telegram_bot_token.get() or "").strip(),
        }

    def sync_telegram_listener_check(self):
        """Match the checkbox to the listener thread without starting or stopping it."""
        from whisperfast.telegram.service import is_running

        var = getattr(self, "telegram_listener_on", None)
        if var is None:
            return
        try:
            var.set(is_running())
        except tk.TclError:
            pass
        toolbar_icons.apply_feature_states(self)

    def _telegram_log(self, msg, tag=None):
        """Forward a listener line, including a file path tagged as a document link."""
        self.root.after(0, lambda m=msg, tg=tag: self.log(m, tg))

    def offer_link_retry(self, retry):
        """Clickable log line that runs the same social download again."""
        def go():
            threading.Thread(target=retry, name="ftw-social-retry", daemon=True).start()

        self.root.after(0, lambda: self.log_action(t("telegram_link_retry"), go))

    def _add_telegram_auto_chat(self, name, chat_id):
        """Remember a chat so the next message from it is processed without asking."""
        names = normalize_chat_names(self.telegram_self_chat_names_text.get())
        title = str(name or "").strip()
        if title and title.casefold() not in {item.casefold() for item in names}:
            names.append(title)
            self.telegram_self_chat_names_text.set(", ".join(names))
        ids = parse_chat_id_text(self.telegram_allowed_chat_ids_text.get())
        mode = (self.telegram_mode.get() or "bot").strip().lower()
        try:
            number = int(chat_id)
        except (TypeError, ValueError):
            number = 0
        if mode == "bot" or ids:
            if number and number not in ids:
                ids.append(number)
                self.telegram_allowed_chat_ids_text.set(format_chat_ids(ids))
        ignored = [
            item for item in normalize_chat_names(self.telegram_ignored_chat_names_text.get())
            if not title or item.casefold() != title.casefold()
        ]
        self.telegram_ignored_chat_names_text.set(", ".join(ignored))
        if number:
            self.telegram_ignored_chat_ids = [
                item for item in normalize_chat_ids(getattr(self, "telegram_ignored_chat_ids", []))
                if item != number
            ]
        self._persist_settings()

    def _add_telegram_ignored_chat(self, name, chat_id):
        """One refusal keeps this group or private chat out of processing."""
        title = str(name or "").strip()
        names = normalize_chat_names(self.telegram_ignored_chat_names_text.get())
        if title and title.casefold() not in {item.casefold() for item in names}:
            names.append(title)
            self.telegram_ignored_chat_names_text.set(", ".join(names))
        kept = [
            item for item in normalize_chat_names(self.telegram_self_chat_names_text.get())
            if not title or item.casefold() != title.casefold()
        ]
        if len(kept) != len(normalize_chat_names(self.telegram_self_chat_names_text.get())):
            self.telegram_self_chat_names_text.set(", ".join(kept))
        ids = list(getattr(self, "telegram_ignored_chat_ids", []) or [])
        try:
            number = int(chat_id)
        except (TypeError, ValueError):
            number = 0
        if number and number not in ids:
            ids.append(number)
            self.telegram_ignored_chat_ids = ids
        if number:
            allowed = parse_chat_id_text(self.telegram_allowed_chat_ids_text.get())
            if number in allowed:
                allowed.remove(number)
                self.telegram_allowed_chat_ids_text.set(format_chat_ids(allowed))
        self._persist_settings()

    def _drop_telegram_ignored_chat(self, name, chat_id):
        """A later Yes takes the chat back out of the skip list."""
        title = str(name or "").strip()
        ignored = [
            item for item in normalize_chat_names(self.telegram_ignored_chat_names_text.get())
            if not title or item.casefold() != title.casefold()
        ]
        self.telegram_ignored_chat_names_text.set(", ".join(ignored))
        try:
            number = int(chat_id)
        except (TypeError, ValueError):
            number = 0
        if number:
            self.telegram_ignored_chat_ids = [
                item for item in normalize_chat_ids(getattr(self, "telegram_ignored_chat_ids", []))
                if item != number
            ]
        self._persist_settings()

    def ask_telegram_learn(self, chat, material, settle, chat_id, outgoing=False, replay=None):
        """Show the question. The log line opens it again after an answer or a closed window."""
        state = {"settled": False, "ran": False}

        def finish(decision):
            from whisperfast.telegram.learn import remember_learn_chat

            if decision == "always":
                self._add_telegram_auto_chat(chat, chat_id)
                remember_learn_chat("always", chat, chat_id)
            elif decision == "no":
                self._add_telegram_ignored_chat(chat, chat_id)
                remember_learn_chat("never", chat, chat_id)
            elif decision == "once":
                remember_learn_chat("ask", chat, chat_id)
                self._drop_telegram_ignored_chat(chat, chat_id)
            process = decision in ("once", "always")
            if not state["settled"]:
                state["settled"] = True
                state["ran"] = process
                try:
                    settle(decision)
                except Exception:
                    pass
                return
            if not process or state["ran"]:
                return
            state["ran"] = True
            if callable(replay):
                replay()
                return
            try:
                settle(decision)
            except Exception:
                pass

        def reopen():
            ui_dialogs.show_telegram_learn_prompt(self, chat, material, finish, outgoing=outgoing)

        question_key = "telegram_learn_question_own" if outgoing else "telegram_learn_question"

        def show():
            self.log_action(t(question_key, chat=chat, material=material), reopen)
            try:
                if self.root.grab_current():
                    return
            except tk.TclError:
                return
            reopen()

        try:
            self.root.after(0, show)
        except tk.TclError:
            finish("no")

    def _remember_telegram_listener(self, wanted):
        """Remember whether the listener should come back after the next launch."""
        self._telegram_listener_wanted = bool(wanted)
        self._persist_settings()

    def _restore_telegram_listener(self):
        """Start the listener when it was left on before the window closed."""
        if not self._telegram_listener_wanted:
            return
        from whisperfast.telegram.service import listener_kind, start as start_listener

        kind = listener_kind(self._telegram_settings_snapshot())
        if not kind:
            return

        def on_done(code):
            def ui():
                from whisperfast.telegram.service import is_running

                if is_running():
                    return
                try:
                    self.telegram_listener_on.set(False)
                except tk.TclError:
                    return
                toolbar_icons.apply_feature_states(self)

            try:
                self.root.after(0, ui)
            except tk.TclError:
                pass

        if start_listener(log=self._telegram_log, on_done=on_done, ask=self.ask_telegram_learn):
            self.telegram_listener_on.set(True)
            toolbar_icons.apply_feature_states(self)
            self.log(
                t("telegram_listener_started_account" if kind == "account" else "telegram_listener_started_bot")
            )

    def _on_telegram_listener_toggled(self):
        from whisperfast.telegram.service import listener_kind, start as start_listener, stop as stop_listener

        if not self.telegram_listener_on.get():
            self._remember_telegram_listener(False)
            stop_listener()
            return
        kind = listener_kind(self._telegram_settings_snapshot())
        if not kind:
            self.telegram_listener_on.set(False)
            messagebox.showerror(t("telegram_settings_title"), t("telegram_not_configured"), parent=self.root)
            return
        self._remember_telegram_listener(True)

        def on_done(code):
            def ui():
                from whisperfast.telegram.service import is_running

                if is_running():
                    return
                try:
                    self.telegram_listener_on.set(False)
                except tk.TclError:
                    return
                toolbar_icons.apply_feature_states(self)
                if code:
                    messagebox.showerror(
                        t("telegram_settings_title"), t("telegram_listener_off"), parent=self.root
                    )

            try:
                self.root.after(0, ui)
            except tk.TclError:
                pass

        start_listener(log=self._telegram_log, on_done=on_done, ask=self.ask_telegram_learn)
        messagebox.showinfo(
            t("telegram_settings_title"),
            t("telegram_listener_started_account" if kind == "account" else "telegram_listener_started_bot"),
            parent=self.root,
        )

    def _show_telegram_settings_dialog(self):
        ui_dialogs.show_telegram_settings_dialog(self)

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
        toolbar_icons.apply_feature_states(self)
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
        if mode not in ("beside", "custom", "named_folder", "custom_named"):
            if not out_dir:
                mode = "beside"
            elif os.path.isabs(out_dir):
                mode = "custom"
            else:
                mode = "named_folder"
                named = named or out_dir
                out_dir = ""
        if mode in ("named_folder", "custom_named") and not named:
            named = "{basename}"
        self.output_mode.set(mode or "beside")
        self.output_dir.set(
            out_dir
            if (mode in ("custom", "custom_named") or os.path.isabs(out_dir))
            else ""
        )
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
        if mode in ("named_folder", "custom_named"):
            template = (
                opts.get("output_named_folder")
                if opts.get("output_named_folder") is not None
                else (self.output_named_folder.get() or "")
            ).strip() or "{basename}"
            base_dir = None
            if mode == "custom_named":
                raw = (
                    opts.get("output_dir") if "output_dir" in opts else (self.output_dir.get() or "")
                ).strip()
                raw = normalize_display_path(raw)
                if raw and os.path.isabs(raw):
                    base_dir = os.path.normpath(raw)
                else:
                    return source_dir
            out = named_folder_output_dir(
                path, template, self._sanitize_folder_name, base_dir=base_dir
            )
            if os.path.normcase(os.path.abspath(out)) == os.path.normcase(source_dir):
                return source_dir
            return self._ensure_dir(out, source_dir)
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

    def _user_downloads_dir(self):
        """Каталог «Завантаження» поточного користувача."""
        if os.name == "nt":
            try:
                import winreg

                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
                ) as key:
                    val, _ = winreg.QueryValueEx(
                        key, "{374DE290-123F-4565-9164-39C4925E467B}"
                    )
                val = os.path.expandvars(str(val or "")).strip()
                if val:
                    os.makedirs(val, exist_ok=True)
                    if os.path.isdir(val):
                        return os.path.normpath(val)
            except (OSError, ValueError):
                pass
        path = os.path.join(os.path.expanduser("~"), "Downloads")
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            pass
        return os.path.normpath(path)

    def _use_downloads_for_watch(self):
        """Немає каталогів — сказати і взяти «Завантаження»."""
        path = self._user_downloads_dir()
        messagebox.showinfo(
            t("watch_dirs_dialog_title"),
            t("watch_downloads_default", path=path),
            parent=self.root,
        )
        self.watch_dir.set(serialize_watch_dirs([path]))
        self._persist_settings()
        return valid_watch_dirs(self.watch_dir.get())

    def _on_watch_click(self, event):
        """Клік — каталоги. Shift+клік — увімкнути або вимкнути."""
        if event is not None and (getattr(event, "state", 0) & 0x0001):
            try:
                self.watch_enabled.set(not bool(self.watch_enabled.get()))
            except tk.TclError:
                return "break"
            self._on_watch_toggled()
        else:
            self._open_watch_dirs_dialog()
        return "break"

    def _on_mp3_click(self, event):
        """Клік — місце MP3. Shift+клік — збереження MP3."""
        if event is not None and (getattr(event, "state", 0) & 0x0001):
            try:
                self.save_audio_mp3.set(not bool(self.save_audio_mp3.get()))
            except tk.TclError:
                return "break"
            self._persist_settings()
            toolbar_icons.apply_feature_states(self)
        else:
            self._show_mp3_settings_dialog()
        return "break"

    def _on_prompts_click(self, event):
        """Клік — промпти. Shift+клік — надсилання тексту в AI."""
        if event is not None and (getattr(event, "state", 0) & 0x0001):
            try:
                self.send_txt_to_ai.set(not bool(self.send_txt_to_ai.get()))
            except tk.TclError:
                return "break"
            self._on_send_txt_to_ai_toggled()
            toolbar_icons.apply_feature_states(self)
        else:
            self.ai_jobs.show_prompts_overview()
        return "break"

    def _on_telegram_click(self, event):
        """Клік — налаштування Telegram. Shift+клік — слухач."""
        if event is not None and (getattr(event, "state", 0) & 0x0001):
            try:
                self.telegram_listener_on.set(not bool(self.telegram_listener_on.get()))
            except tk.TclError:
                return "break"
            self._on_telegram_listener_toggled()
            toolbar_icons.apply_feature_states(self)
        else:
            self._show_telegram_settings_dialog()
        return "break"

    def _on_docx_click(self, event):
        """Клік — довідка про експорт. Shift+клік — увімкнути або вимкнути."""
        if event is not None and (getattr(event, "state", 0) & 0x0001):
            self._toggle_export_md_to_docx()
            toolbar_icons.apply_feature_states(self)
        else:
            self._show_docx_settings()
        return "break"

    def _show_docx_settings(self):
        from whisperfast.core.pandoc_export import is_pandoc_available, pandoc_version

        if is_pandoc_available():
            status = t("pandoc_found", version=pandoc_version() or "pandoc")
        else:
            status = t("pandoc_not_found")
        messagebox.showinfo(
            t("export_md_to_docx"),
            t("export_md_docx_about") + "\n\n" + status,
            parent=self.root,
        )

    def _open_watch_dirs_dialog(self):
        """Вікно списку каталогів слідкування (Зберегти / закриття = скасування)."""
        current = parse_watch_dirs(self.watch_dir.get())
        if not valid_watch_dirs(current):
            current = self._use_downloads_for_watch()

        def on_save(dirs):
            chosen = valid_watch_dirs(dirs)
            if not chosen:
                chosen = self._use_downloads_for_watch()
            else:
                self.watch_dir.set(serialize_watch_dirs(chosen))
                self._persist_settings()
            if self.watch_enabled.get():
                if valid_watch_dirs(self.watch_dir.get()):
                    self.queue_ctrl.start_watch()
                else:
                    self.queue_ctrl.stop_watch()
                    self.watch_enabled.set(False)
            toolbar_icons.apply_watch_state(self)

        ui_dialogs.open_watch_dirs_dialog(
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
                dirs = self._use_downloads_for_watch()
            if not dirs:
                self.watch_enabled.set(False)
                self._persist_settings()
                toolbar_icons.apply_watch_state(self)
                return
            self.queue_ctrl.start_watch()
        else:
            self.queue_ctrl.stop_watch()
            self.log(t("watch_stopped"))
        self._persist_settings()
        toolbar_icons.apply_watch_state(self)

    def prepare_close(self):
        """Зупинити слідкування, трей та зберегти налаштування перед закриттям (викликається з main.py)."""
        self.queue_ctrl.stop_watch()
        # Flush any debounced request_queue.json write (schedule_save()) so the
        # last queue mutation isn't lost if the app closes before it fires.
        self.queue_ctrl.save_to_file()
        if self._tray_icon:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        try:
            self.log_panel.flush()
        except Exception as e:
            # log_panel itself failed to flush, so fall back to stderr instead
            # of silently losing the last log/session record on exit.
            try:
                print(f"[FTW] log_panel.flush() failed on exit: {e}", file=sys.stderr)
            except Exception:
                pass
        try:
            from whisperfast.single_instance import release_lock_if_owned
            release_lock_if_owned()
        except Exception:
            pass
        self._persist_settings()

    def _model_button_label(self):
        """Коротка назва поточної моделі для підказки."""
        return self.whisper_model.get() or DEFAULT_MODEL

    def _model_tooltip_text(self):
        return t(
            "tooltip_model_btn",
            model=self._model_button_label(),
            cache_dir=get_whisper_cache_dir(),
        )

    def _refresh_model_tooltip(self):
        tip = getattr(self, "_model_tip", None)
        if tip is not None:
            tip._text_or_key = self._model_tooltip_text()

    def _show_environment_menu(self):
        """Одна іконка: перевірка системи, оновлення або встановлення залежностей."""
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label=t("env_menu_system"), command=lambda: check_system(self.log))
        menu.add_command(label=t("env_menu_updates"), command=self.run_updates_check)
        menu.add_command(label=t("env_menu_install"), command=self.run_install)
        btn = self.dependencies_btn
        x = btn.winfo_rootx()
        y = btn.winfo_rooty() + btn.winfo_height()
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _show_tray_mode_menu(self):
        """Іконка трею: меню Панель / Трей / Панель + Трей."""
        menu = tk.Menu(self.root, tearoff=0)
        labels = ("tray_mode_panel", "tray_mode_tray", "tray_mode_panel_tray")
        for key, label_key in zip(self.TRAY_MODE_KEYS, labels):
            menu.add_radiobutton(
                label=t(label_key),
                value=key,
                variable=self.tray_mode,
                command=self._choose_tray_mode,
            )
        x = self.tray_mode_btn.winfo_rootx()
        y = self.tray_mode_btn.winfo_rooty() + self.tray_mode_btn.winfo_height()
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _choose_tray_mode(self):
        self._apply_tray_mode()
        self._persist_settings()

    def _toggle_autostart(self):
        try:
            self.autostart_enabled.set(not bool(self.autostart_enabled.get()))
        except tk.TclError:
            return
        self._on_autostart_toggled()

    def _on_autostart_toggled(self):
        want = False
        try:
            want = bool(self.autostart_enabled.get())
        except tk.TclError:
            return
        try:
            if want:
                removed = win_autostart.enable()
            else:
                removed = win_autostart.disable()
        except FileNotFoundError as e:
            self.autostart_enabled.set(win_autostart.is_enabled())
            toolbar_icons.apply_autostart_state(self)
            messagebox.showerror(
                t("error"),
                t("autostart_bat_not_found", path=e.filename or str(e)),
            )
            return
        except OSError as e:
            self.autostart_enabled.set(win_autostart.is_enabled())
            toolbar_icons.apply_autostart_state(self)
            messagebox.showerror(t("error"), f"{t('autostart_run_error')}: {e}")
            return
        self.autostart_enabled.set(win_autostart.is_enabled())
        toolbar_icons.apply_autostart_state(self)
        self.log(t("autostart_enabled_log" if want else "autostart_disabled_log"))
        if removed:
            self.log(t("autostart_registry_cleared", names=", ".join(removed)))

    def _show_device_dialog(self):
        """Вікно вибору AUTO, GPU або CPU. Поточний режим лишається на іконці."""
        dialog = tk.Toplevel(self.root)
        dialog.title(t("device_label"))
        dialog.transient(self.root)
        dialog.resizable(False, False)
        chosen = tk.StringVar(value=self.device_mode.get() if self.device_mode.get() in ("AUTO", "GPU", "CPU") else "AUTO")
        box = ttk.Frame(dialog, padding=16)
        box.pack()
        for device in ("AUTO", "GPU", "CPU"):
            ttk.Radiobutton(box, text=device, value=device, variable=chosen).pack(anchor="w", pady=2)

        def apply_choice():
            self.device_mode.set(chosen.get())
            self._on_device_mode_change()
            dialog.destroy()

        ttk.Button(box, text=t("save"), command=apply_choice).pack(anchor="e", pady=(12, 0))
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.bind("<Escape>", lambda event: dialog.destroy())
        self._center_toplevel(dialog)

    def _on_device_mode_change(self):
        if self.device_mode.get() == "GPU":
            self.keep_gpu_awake.set(True)
        else:
            self.keep_gpu_awake.set(False)
        self._apply_gpu_hold()
        toolbar_icons.apply_device_state(self)
        self._persist_settings()

    def _on_keep_gpu_awake_toggled(self):
        self._apply_gpu_hold()
        self._persist_settings()

    def _apply_gpu_hold(self):
        try:
            from whisperfast.setup.gpu_info import prepare_nvidia_gpu, release_nvidia_display_client
            if self.keep_gpu_awake.get():
                prepare_nvidia_gpu(hold=True)
            else:
                release_nvidia_display_client()
        except Exception:
            pass

    def ask_gpu_required(self):
        """Ask how to continue when GPU mode cannot start CUDA. Returns AUTO, CPU, or None."""
        if threading.current_thread() is threading.main_thread():
            return self._gpu_required_dialog()
        result = {"choice": None}
        done = threading.Event()

        def show():
            result["choice"] = self._gpu_required_dialog()
            done.set()

        self.root.after(0, show)
        done.wait()
        return result["choice"]

    def _gpu_required_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title(t("gpu_required_title"))
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)
        ttk.Label(dialog, text=t("gpu_required_body"), wraplength=460, justify="left").pack(
            padx=16, pady=(16, 8)
        )
        choice = {"value": None}

        def pick(value):
            choice["value"] = value
            dialog.destroy()

        row = ttk.Frame(dialog)
        row.pack(padx=16, pady=(0, 16))
        ttk.Button(row, text="AUTO", command=lambda: pick("AUTO")).pack(side="left", padx=4)
        ttk.Button(row, text="CPU", command=lambda: pick("CPU")).pack(side="left", padx=4)
        ttk.Button(row, text=t("gpu_required_close"), command=lambda: pick(None)).pack(side="left", padx=4)
        dialog.protocol("WM_DELETE_WINDOW", lambda: pick(None))
        self._center_toplevel(dialog)
        dialog.wait_window()
        selected = choice["value"]
        if selected in ("AUTO", "CPU"):
            self.device_mode.set(selected)
            if selected != "GPU":
                self.keep_gpu_awake.set(False)
            self._apply_gpu_hold()
            toolbar_icons.apply_device_state(self)
            self._persist_settings()
        return selected

    def on_window_close(self):
        """Вызывается при нажатии X на окне: в режиме «Трей» — свернуть в трей, иначе — диалог закрытия."""
        if self.tray_mode.get() == "tray":
            self.root.withdraw()
        else:
            if self._on_close_request:
                self._on_close_request()

    def _persist_settings(self):
        """Зберігає поточні налаштування в settings.json (викликається при закритті та при зміні слідкування)."""
        from whisperfast.telegram.links import normalize_social_quality

        payload = {
            "language": self.ui_language.get(),
            "output_mode": self.output_mode.get() or "beside",
            "output_dir": normalize_display_path((self.output_dir.get() or "").strip()),
            "output_named_folder": (self.output_named_folder.get() or "").strip() or "{basename}",
            "mp3_output_mode": self.mp3_output_mode.get() or "inherit",
            "mp3_output_dir": normalize_display_path((self.mp3_output_dir.get() or "").strip()),
            "watch_dir": serialize_watch_dirs(parse_watch_dirs(self.watch_dir.get())),
            "watch_enabled": self.watch_enabled.get(),
            "device_mode": self.device_mode.get(),
            "keep_gpu_awake": bool(self.keep_gpu_awake.get()),
            "play_sound_on_finish": self.play_sound_on_finish.get(),
            "save_audio_mp3": self.save_audio_mp3.get(),
            "send_txt_to_ai": self.send_txt_to_ai.get(),
            "send_txt_to_cursor": self.send_txt_to_ai.get(),
            "ai_default_prompt_nums": normalize_default_prompt_nums(
                getattr(self, "ai_default_prompt_nums", None)
            ),
            "ai_auto_process": bool(self.ai_auto_process.get()),
            "export_md_to_docx": self.export_md_to_docx.get(),
            "ai_provider": normalize_provider_id(self.ai_provider.get()),
            "cursor_api_key": (self.cursor_api_key.get() or "").strip(),
            "gemini_api_key": (self.gemini_api_key.get() or "").strip(),
            "gemini_model": (self.gemini_model.get() or "").strip() or "gemini-2.0-flash",
            "gemini_oauth_refresh_token": (self.gemini_oauth_refresh_token.get() or "").strip(),
            "gemini_oauth_email": (self.gemini_oauth_email.get() or "").strip(),
            "google_oauth_client_id": (self.google_oauth_client_id.get() or "").strip(),
            "google_oauth_client_secret": (self.google_oauth_client_secret.get() or "").strip(),
            "google_cloud_project_id": (self.google_cloud_project_id.get() or "").strip(),
            "anthropic_api_key": (self.anthropic_api_key.get() or "").strip(),
            "claude_model": (self.claude_model.get() or "").strip() or "claude-sonnet-4-5",
            "azure_openai_endpoint": (self.azure_openai_endpoint.get() or "").strip(),
            "azure_openai_api_key": (self.azure_openai_api_key.get() or "").strip(),
            "azure_openai_deployment": (self.azure_openai_deployment.get() or "").strip(),
            "azure_openai_api_version": (
                (self.azure_openai_api_version.get() or "").strip()
                or "2024-08-01-preview"
            ),
            "ollama_base_url": (self.ollama_base_url.get() or "").strip()
            or "http://127.0.0.1:11434",
            "ollama_model": (self.ollama_model.get() or "").strip() or "llama3.2",
            "openai_compatible_base_url": (
                (self.openai_compatible_base_url.get() or "").strip()
            ),
            "openai_compatible_api_key": (
                (self.openai_compatible_api_key.get() or "").strip()
            ),
            "openai_compatible_model": (
                (self.openai_compatible_model.get() or "").strip()
            ),
            "telegram_bot_token": (self.telegram_bot_token.get() or "").strip(),
            "telegram_api_id": (self.telegram_api_id.get() or "").strip(),
            "telegram_api_hash": (self.telegram_api_hash.get() or "").strip(),
            "telegram_bot_api_exe": (self.telegram_bot_api_exe.get() or "").strip(),
            "telegram_api_base": (self.telegram_api_base.get() or "").strip()
            or "http://127.0.0.1:8081",
            "telegram_allowed_chat_ids": parse_chat_id_text(
                self.telegram_allowed_chat_ids_text.get()
            ),
            "telegram_work_dir": (self.telegram_work_dir.get() or "").strip(),
            "telegram_social_to_queue": bool(self.telegram_social_to_queue.get()),
            "telegram_learn_mode": bool(self.telegram_learn_mode.get()),
            "telegram_social_quality": normalize_social_quality(self.telegram_social_quality.get()),
            "telegram_listener_enabled": bool(getattr(self, "_telegram_listener_wanted", False)),
            "telegram_mode": (self.telegram_mode.get() or "bot").strip().lower(),
            "telegram_phone": (self.telegram_phone.get() or "").strip(),
            "telegram_self_chat_names": normalize_chat_names(self.telegram_self_chat_names_text.get()),
            "telegram_ignored_chat_names": normalize_chat_names(
                self.telegram_ignored_chat_names_text.get()
            ),
            "telegram_ignored_chat_ids": normalize_chat_ids(
                getattr(self, "telegram_ignored_chat_ids", [])
            ),
            "ai_month_budget": float(self.ai_month_budget.get() or 0.0),
            "ai_prompt_rules": normalize_prompt_rules(
                getattr(self, "ai_prompt_rules", None)
            ),
            "export_json": bool(self.export_json.get()),
            "export_vtt": bool(self.export_vtt.get()),
            "word_timestamps": bool(self.word_timestamps.get()),
            "diarization_enabled": bool(self.diarization_enabled.get()),
            "capture_consent_shown": bool(self.capture_consent_shown.get()),
            "tray_mode": self.tray_mode.get(),
            "whisper_model": self.whisper_model.get(),
            "has_nvidia": self.has_nvidia,
            "gpu_model": self.gpu_model,
        }
        cid = (self.google_oauth_client_id.get() or "").strip()
        if cid:
            live = dict(getattr(self, "capture_cfg", None) or {})
            live["google_calendar_client_id"] = cid
            secret = (self.google_oauth_client_secret.get() or "").strip()
            if secret:
                live["google_calendar_client_secret"] = secret
            self.capture_cfg = live
        payload.update(snapshot_capture_settings(getattr(self, "capture_cfg", None) or {}))
        save_app_settings(payload)

    def _on_send_txt_to_ai_toggled(self):
        self._persist_settings()

    def _open_archive(self):
        show_archive_window(self)

    def _toggle_play_sound(self):
        self.play_sound_on_finish.set(not bool(self.play_sound_on_finish.get()))
        toolbar_icons.apply_notify_state(self)
        self._persist_settings()

    def _toggle_capture(self):
        capture_ui.toggle_capture(self)

    def _toggle_capture_pause(self):
        capture_ui.toggle_pause(self)

    def _save_capture_clip(self):
        capture_ui.save_clip(self)

    def _open_capture_settings(self):
        show_capture_settings_dialog(self)

    def auto_start_record(self):
        capture_ui.start_capture(self, trigger="manual")

    def _toggle_export_md_to_docx(self):
        try:
            self.export_md_to_docx.set(not bool(self.export_md_to_docx.get()))
        except tk.TclError:
            return
        self._on_export_md_to_docx_toggled()
        toolbar_icons.apply_feature_states(self)

    def _on_export_md_to_docx_toggled(self):
        self._persist_settings()
        if not self.export_md_to_docx.get():
            return
        from whisperfast.core.pandoc_export import is_pandoc_available, pandoc_version
        from whisperfast.setup.external_tools import (
            install_external_tools,
            log_pandoc_install_howto,
            pandoc_missing_dialog_text,
        )

        if is_pandoc_available():
            return
        self.log(t("pandoc_not_found"))
        if messagebox.askyesno(t("export_md_to_docx"), t("pandoc_install_now_prompt")):
            def worker():
                install_external_tools(self.log, names=["pandoc"], missing_only=True)
                if is_pandoc_available():
                    self.log(t("pandoc_found", version=pandoc_version() or "pandoc"))
                    return
                log_pandoc_install_howto(self.log)
                self.root.after(
                    0,
                    lambda: messagebox.showwarning(t("export_md_to_docx"), pandoc_missing_dialog_text()),
                )

            threading.Thread(target=worker, daemon=True).start()
        else:
            log_pandoc_install_howto(self.log)
            messagebox.showwarning(t("export_md_to_docx"), pandoc_missing_dialog_text())

    def resolve_output_paths(self, paths, force_ask=False):
        """Якщо файл уже існує: Так перезаписує, Ні не зберігає, окрема кнопка дає ім'я з _HHMM."""
        from whisperfast.core.output_conflict import resolve_output_paths
        from whisperfast.ui.dialogs import ask_overwrite_via_tk

        return resolve_output_paths(
            paths,
            ask_overwrite=lambda p, alt: ask_overwrite_via_tk(self, p, alt, force=force_ask),
        )

    def resolve_output_path(self, path, force_ask=False):
        """Повертає шлях або "" якщо користувач натиснув Skip."""
        return self.resolve_output_paths([path], force_ask=force_ask)[0]

    def _maybe_log_all_complete(self, send_txt_to_cursor, will_continue):
        self.ai_jobs.maybe_log_all_complete(send_txt_to_cursor, will_continue)

    def _export_markdown_to_docx(self, md_path, log_file_id=None):
        """MD → DOCX через Pandoc."""
        from whisperfast.core.pandoc_export import convert_markdown_with_pandoc, office_output_path

        out = self.resolve_output_path(office_output_path(md_path, "docx"))
        if not out:
            name = os.path.basename(office_output_path(md_path, "docx"))
            if log_file_id:
                self.log_file_event(t("file_exists_skipped", name=name), file_id=log_file_id)
            else:
                self.log(t("file_exists_skipped", name=name))
            return []
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
        self.ai_jobs.show_prompts_overview()

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

    def _delete_queue_row_at(self, event):
        """Drop the single queue row under the pointer."""
        iid = self.queue_list.identify_row(event.y)
        if not iid:
            return
        try:
            idx = self.queue_list.index(iid)
        except tk.TclError:
            return
        self.queue_ctrl.delete_indices([idx])

    def _ask_telegram_recipient(self, filename: str) -> str:
        """Ask which group or contact should receive a file that has no chat yet."""
        dialog = tk.Toplevel(self.root)
        dialog.title(t("telegram_send_ask_title"))
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.grab_set()
        ttk.Label(
            dialog,
            text=t("telegram_send_ask_body", name=filename),
            wraplength=420,
        ).pack(padx=16, pady=(16, 8), anchor="w")
        name = tk.StringVar()
        entry = ttk.Entry(dialog, textvariable=name, width=42)
        entry.pack(padx=16, fill="x")
        entry.focus_set()
        from whisperfast.telegram.recent import recent_names, remember_name

        recent = recent_names()
        if recent:
            ttk.Label(dialog, text=t("telegram_send_recent")).pack(padx=16, pady=(10, 4), anchor="w")
            listed = tk.Listbox(dialog, height=min(10, len(recent)), activestyle="dotbox")
            for item in recent:
                listed.insert("end", item)
            listed.pack(padx=16, fill="x")

            def pick(_event=None):
                sel = listed.curselection()
                if sel:
                    name.set(listed.get(sel[0]))

            listed.bind("<<ListboxSelect>>", pick)
            listed.bind("<Double-Button-1>", lambda _event: accept())
        chosen = {"value": ""}

        def accept(_event=None):
            chosen["value"] = (name.get() or "").strip()
            dialog.destroy()

        def cancel(_event=None):
            dialog.destroy()

        buttons = ttk.Frame(dialog)
        buttons.pack(padx=16, pady=16, anchor="e")
        ttk.Button(buttons, text=t("save"), command=accept).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text=t("cancel_btn"), command=cancel).pack(side="left")
        dialog.protocol("WM_DELETE_WINDOW", cancel)
        dialog.bind("<Return>", accept)
        dialog.bind("<Escape>", cancel)
        self._center_toplevel(dialog)
        dialog.wait_window()
        if chosen["value"]:
            remember_name(chosen["value"])
        return chosen["value"]

    def _send_queue_row_to_telegram(self, event):
        """Send this row's transcript and AI files back to its Telegram chat."""
        iid = self.queue_list.identify_row(event.y)
        if not iid:
            return
        try:
            idx = self.queue_list.index(iid)
        except tk.TclError:
            return
        if not (0 <= idx < len(self.queue)):
            return
        item = self.queue[idx]
        if not item.get("processed"):
            return
        source = str(item.get("path") or "")
        from whisperfast.telegram.gui_bridge import _file_entry, select_telegram_files
        from whisperfast.telegram.outbox import enqueue_outgoing

        entry = _file_entry(self, source_path=source)
        outputs = [o for o in ((entry or {}).get("outputs") or []) if isinstance(o, dict)]
        files = select_telegram_files(source, outputs)
        if not files:
            self.log(t("telegram_send_nothing", name=os.path.basename(source)))
            return
        chat_id = item.get("telegram_chat_id")
        chat_name = ""
        reply = None
        force_ask = bool(event is not None and (getattr(event, "state", 0) & 0x0001))
        if chat_id is None or force_ask:
            chat_name = self._ask_telegram_recipient(os.path.basename(source))
            if not chat_name:
                return
            chat_id = None
        else:
            try:
                reply = int(item.get("telegram_message_id") or 0)
            except (TypeError, ValueError):
                reply = 0
        enqueue_outgoing(
            None if chat_id is None else int(chat_id),
            reply,
            text="",
            files=[{"path": row["path"], "caption": row["caption"]} for row in files],
            chat_name=chat_name,
        )
        self.queue_ctrl.set_result(source, tg_sent=True)
        from whisperfast.library import note_telegram_recipient

        note_telegram_recipient(
            source,
            chat_name or ("" if chat_id is None else str(int(chat_id))),
            library=getattr(self, "library", None),
        )
        self._refresh_archive_window()
        self.log(t("telegram_sent_manual", name=os.path.basename(source)))

    def send_archive_job_to_telegram(self, job, *, force_ask=False):
        """Same send as a processed queue row. Shift always asks who should receive it."""
        if not isinstance(job, dict):
            return
        source = str(job.get("source") or job.get("txt_path") or "")
        name = os.path.basename(str(job.get("name") or source))
        from whisperfast.telegram.gui_bridge import _file_entry, select_telegram_files
        from whisperfast.telegram.origin import lookup
        from whisperfast.telegram.outbox import enqueue_outgoing

        entry = _file_entry(self, file_id=str(job.get("id") or ""), source_path=source)
        outputs = [o for o in ((entry or {}).get("outputs") or []) if isinstance(o, dict)]
        if not outputs:
            if job.get("txt_path"):
                outputs.append({"role": "txt", "path": job.get("txt_path")})
            if job.get("mp3_path"):
                outputs.append({"role": "mp3", "path": job.get("mp3_path")})
            for extra in job.get("extra_outputs") or []:
                if isinstance(extra, dict):
                    outputs.append(extra)
        files = select_telegram_files(source, outputs)
        if not files:
            self.log(t("telegram_send_nothing", name=name))
            return
        origin = lookup(source) or {}
        chat_id = None if force_ask else origin.get("chat_id")
        chat_name = ""
        reply = None
        if chat_id is None:
            chat_name = self._ask_telegram_recipient(name)
            if not chat_name:
                return
        else:
            try:
                reply = int(origin.get("message_id") or 0)
            except (TypeError, ValueError):
                reply = 0
        enqueue_outgoing(
            None if chat_id is None else int(chat_id),
            reply,
            text="",
            files=[{"path": row["path"], "caption": row["caption"]} for row in files],
            chat_name=chat_name,
        )
        from whisperfast.library import note_telegram_recipient

        note_telegram_recipient(
            source,
            chat_name or str(int(chat_id)),
            library=getattr(self, "library", None),
        )
        self._refresh_archive_window()
        self.log(t("telegram_sent_manual", name=name))

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
        files = ui_dialogs.add_multiple_files()
        if files:
            self.add_files_to_queue(files)

    def add_directory_action(self):
        """Обработчик кнопки 'Добавить каталог'"""
        files = ui_dialogs.add_directory(recursive=True)
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
        if self._queue_column_name(event) == "remove":
            self._drag_iid = None
            self._drag_index = -1
            self._delete_queue_row_at(event)
            return "break"
        if self._queue_column_name(event) == "tg":
            self._drag_iid = None
            self._drag_index = -1
            self._send_queue_row_to_telegram(event)
            return "break"
        if self._queue_column_name(event) == "ai":
            self._queue_ai_armed = True
            self._drag_iid = None
            self._drag_index = -1
            return "break"
        iid = self.queue_list.identify_row(event.y)
        if iid and (event.state & 0x0001):
            return self._on_queue_shift_click(event)
        self._queue_ai_armed = False
        self._drag_iid = iid
        try:
            self._drag_index = self.queue_list.index(iid) if iid else -1
        except tk.TclError:
            self._drag_index = -1

    def _queue_column_name(self, event):
        col = self.queue_list.identify_column(event.x)
        if not col or not col.startswith("#"):
            return ""
        try:
            idx = int(col[1:]) - 1
        except ValueError:
            return ""
        names = self.queue_list["columns"]
        if 0 <= idx < len(names):
            return names[idx]
        return ""

    def _on_queue_button_release(self, event):
        if not getattr(self, "_queue_ai_armed", False):
            return
        self._queue_ai_armed = False
        if self._queue_column_name(event) != "ai":
            return "break"
        self._run_queue_row_ai(event)
        return "break"

    def _run_queue_row_ai(self, event):
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
        path = row.get("path") or ""
        ok = self.ai_jobs.start_prompts_for_path(path)
        if not ok:
            messagebox.showinfo(t("app_title"), t("ai_no_transcript"), parent=self.root)

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
            if self._queue_column_name(event) == "tg":
                self._send_queue_row_to_telegram(event)
                return "break"
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
        self._persist_settings()
    
    def update_ui_language(self):
        """Обновляет все тексты интерфейса при смене языка"""
        # Обновляем заголовок окна
        self.root.title(t("app_title"))
        
        # Обновляем элементы интерфейса
        self.queue_header_label.config(text=t("queue_header"))
        self.log_header_label.config(text=t("log_header"))
        toolbar_icons.apply_static(self)
        try:
            capture_ui.refresh_capture_buttons(self)
        except Exception:
            toolbar_icons.apply_capture_state(self, running=False, paused=False)
        self.help_btn.config(text=t("help"))
        try:
            ui = self.ui_language.get()
            if ui in SUPPORTED_LANGUAGES:
                self.ui_lang_combo.current(SUPPORTED_LANGUAGES.index(ui))
        except (tk.TclError, ValueError):
            pass
        self._refresh_model_tooltip()
        self.queue_list.heading("num", text=t("col_num"))
        self._set_remove_column_heading()
        self.queue_list.heading("filename", text=t("col_filename"))
        self.queue_list.heading("note", text=t("col_note"))
        self.queue_list.heading("ai", text=t("col_ai"))
        self._set_tg_column_heading()
        self.queue_list.heading("start", text=t("col_start"))
        self.queue_list.heading("end_seg1", text=t("col_end_seg1"))
        self.queue_list.heading("end_seg2", text=t("col_end_seg2"))
        self.queue_list.heading("end", text=t("col_end"))
        self.queue_list.heading("status", text=t("col_status"))
        try:
            self.log_panel.update_copy_menu_label()
            self.log_panel.refresh_i18n()
        except (tk.TclError, IndexError):
            pass
        try:
            self.queue_menu.entryconfig(0, label=t("delete_from_queue"))
        except (tk.TclError, IndexError):
            pass
        # Відкриті діалоги (Help, реліз-ноти, промпти…)
        try:
            ui_dialogs.refresh_i18n_windows(self)
        except Exception:
            pass
