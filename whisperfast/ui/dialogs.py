"""Modal dialogs for WhisperGUI."""
import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

from whisperfast.config import (
    DEFAULT_MODEL,
    WHISPER_MODELS,
    find_whisper_model_cache_path,
    get_whisper_cache_dir,
    load_help_text,
)
from whisperfast.core.model_manager import WhisperModelSingleton
from whisperfast.i18n import t
from whisperfast.updates.model_updates import (
    is_model_downloaded,
    model_needs_update,
    update_whisper_model,
)
from whisperfast.utils import normalize_display_path


def center_toplevel(app, win, parent=None):
    """Размещает Toplevel по центру родительского окна (или экрана). Не выносит за границы экрана."""
    parent = parent or app.root
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


def show_help(app):
    """Показывает окно справки с прокруткой и адаптивным размером"""
    help_window = tk.Toplevel(app.root)
    help_window.title(t("help_title"))
    help_window.transient(app.root)
    
    # Обновляем главное окно для получения актуальных размеров
    app.root.update_idletasks()
    
    main_width = app.root.winfo_width()
    main_height = app.root.winfo_height()
    help_width = max(700, int(main_width * 0.85))
    help_height = max(600, int(main_height * 0.85))
    help_window.geometry(f"{help_width}x{help_height}")
    center_toplevel(app, help_window)
    
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
    
    # Текст справки на языке интерфейса (Help_EN / Help_UK / Help_RU)
    text_widget.insert("1.0", load_help_text(app.ui_language.get()))
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


def show_mp3_settings_dialog(app):
    """Налаштування каталогу для MP3, створених програмою (чекбокс лишається окремо)."""
    dialog = tk.Toplevel(app.root)
    dialog.title(t("mp3_settings_title"))
    dialog.transient(app.root)
    dialog.resizable(False, False)
    dialog.grab_set()

    frame = ttk.Frame(dialog, padding=15)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text=t("mp3_settings_hint")).pack(anchor="w", pady=(0, 8))

    mode_var = tk.StringVar(value=app.mp3_output_mode.get() or "inherit")
    dir_var = tk.StringVar(value=app.mp3_output_dir.get() or "")

    ttk.Radiobutton(
        frame, text=t("mp3_mode_inherit"), variable=mode_var, value="inherit"
    ).pack(anchor="w", pady=2)
    ttk.Radiobutton(
        frame, text=t("save_mode_beside"), variable=mode_var, value="beside"
    ).pack(anchor="w", pady=2)

    custom_row = ttk.Frame(frame)
    custom_row.pack(fill="x", pady=2)
    ttk.Radiobutton(
        custom_row, text=t("save_mode_custom"), variable=mode_var, value="custom"
    ).pack(side="left")
    dir_entry = ttk.Entry(custom_row, textvariable=dir_var, width=36)
    dir_entry.pack(side="left", fill="x", expand=True, padx=(8, 4))

    def browse():
        d = filedialog.askdirectory(parent=dialog)
        if d:
            dir_var.set(normalize_display_path(d))
            mode_var.set("custom")

    ttk.Button(custom_row, text="…", width=3, command=browse).pack(side="left")

    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(12, 0))

    def close_cancel():
        dialog.destroy()

    def save_and_close():
        mode = mode_var.get() or "inherit"
        path = normalize_display_path((dir_var.get() or "").strip())
        if mode == "custom":
            if not path or not os.path.isdir(path):
                messagebox.showerror(t("error"), t("save_dir_invalid"), parent=dialog)
                return
        app.mp3_output_mode.set(mode)
        app.mp3_output_dir.set(
            path if mode == "custom" else (path if os.path.isabs(path) else "")
        )
        app._persist_settings()
        dialog.destroy()

    ttk.Button(buttons, text=t("cancel_btn"), command=close_cancel).pack(
        side="right", padx=(5, 0)
    )
    ttk.Button(buttons, text=t("save"), command=save_and_close).pack(side="right")
    dialog.protocol("WM_DELETE_WINDOW", close_cancel)
    dialog.bind("<Escape>", lambda e: close_cancel())
    center_toplevel(app, dialog)


def show_output_settings_dialog(app):
    """Налаштування збереження всіх файлів, створених програмою."""
    dialog = tk.Toplevel(app.root)
    dialog.title(t("output_settings_title"))
    dialog.transient(app.root)
    dialog.resizable(False, False)
    dialog.grab_set()

    frame = ttk.Frame(dialog, padding=15)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text=t("output_settings_hint")).pack(anchor="w", pady=(0, 8))

    mode_var = tk.StringVar(value=app.output_mode.get() or "beside")
    dir_var = tk.StringVar(value=app.output_dir.get() or "")
    named_var = tk.StringVar(value=app.output_named_folder.get() or "{basename}")

    ttk.Radiobutton(frame, text=t("save_mode_beside"), variable=mode_var, value="beside").pack(anchor="w", pady=2)

    custom_row = ttk.Frame(frame)
    custom_row.pack(fill="x", pady=2)
    ttk.Radiobutton(custom_row, text=t("save_mode_custom"), variable=mode_var, value="custom").pack(side="left")
    dir_entry = ttk.Entry(custom_row, textvariable=dir_var, width=36)
    dir_entry.pack(side="left", fill="x", expand=True, padx=(8, 4))

    def browse():
        d = filedialog.askdirectory(parent=dialog)
        if d:
            dir_var.set(normalize_display_path(d))
            mode_var.set("custom")

    ttk.Button(custom_row, text="…", width=3, command=browse).pack(side="left")

    named_row = ttk.Frame(frame)
    named_row.pack(fill="x", pady=2)
    ttk.Radiobutton(
        named_row, text=t("save_mode_named_folder"), variable=mode_var, value="named_folder"
    ).pack(side="left")
    named_entry = ttk.Entry(named_row, textvariable=named_var, width=28)
    named_entry.pack(side="left", fill="x", expand=True, padx=(8, 0))
    ttk.Label(frame, text=t("save_named_folder_hint"), wraplength=420).pack(anchor="w", pady=(4, 0))

    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(12, 0))

    def close_cancel():
        dialog.destroy()

    def save_and_close():
        mode = mode_var.get() or "beside"
        path = normalize_display_path((dir_var.get() or "").strip())
        named = (named_var.get() or "").strip() or "{basename}"
        if mode == "custom":
            if not path or not os.path.isdir(path):
                messagebox.showerror(t("error"), t("save_dir_invalid"), parent=dialog)
                return
        if mode == "named_folder" and not app._sanitize_folder_name(
            named.replace("{basename}", "x").replace("{name}", "x")
        ):
            messagebox.showerror(t("error"), t("save_named_folder_invalid"), parent=dialog)
            return
        app.output_mode.set(mode)
        app.output_dir.set(path if mode == "custom" else (path if os.path.isabs(path) else ""))
        app.output_named_folder.set(named)
        app._persist_settings()
        dialog.destroy()

    ttk.Button(buttons, text=t("cancel_btn"), command=close_cancel).pack(side="right", padx=(5, 0))
    ttk.Button(buttons, text=t("save"), command=save_and_close).pack(side="right")
    dialog.protocol("WM_DELETE_WINDOW", close_cancel)
    dialog.bind("<Escape>", lambda e: close_cancel())
    center_toplevel(app, dialog)


def folder_size_mb(path):
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


def model_dialog_refresh_listbox(app, lb, cache_root):
    """Оновлює рядки списку моделей (статус завантаження та оновлення)."""
    lb.delete(0, "end")
    for name in WHISPER_MODELS:
        full_path = find_whisper_model_cache_path(cache_root, name)
        if full_path:
            size_mb = folder_size_mb(full_path)
            line = f"{name}  —  {t('model_dialog_downloaded')}  ~{size_mb} MB"
            if model_needs_update(name, cache_root):
                line += f"  ({t('model_dialog_update_available')})"
        else:
            line = f"{name}  —  {t('model_dialog_not_downloaded')}"
        lb.insert("end", line)


def show_model_dialog(app):
    """Открывает окно выбора модели Whisper: список моделей, отметка загруженных и размер."""
    cache_root = get_whisper_cache_dir()
    current = app.whisper_model.get() or DEFAULT_MODEL

    win = tk.Toplevel(app.root)
    win.title(t("model_dialog_title"))
    win.transient(app.root)
    win.grab_set()
    win.geometry("460x380")
    win.minsize(400, 300)
    main_f = ttk.Frame(win, padding=10)
    main_f.pack(fill="both", expand=True)
    header_f = ttk.Frame(main_f)
    header_f.pack(fill="x")
    ttk.Label(
        header_f,
        text=t("model_dialog_cache", cache_dir=cache_root),
        wraplength=300,
    ).pack(side="left", fill="x", expand=True)

    frame = ttk.Frame(main_f)
    frame.pack(fill="both", expand=True)
    lb = tk.Listbox(frame, height=12, selectmode="single", font=("Segoe UI", 9))
    scroll = ttk.Scrollbar(frame)
    lb.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")
    lb.config(yscrollcommand=scroll.set)
    scroll.config(command=lb.yview)

    model_dialog_refresh_listbox(app, lb, cache_root)
    try:
        idx = WHISPER_MODELS.index(current)
        lb.selection_set(idx)
        lb.see(idx)
    except ValueError:
        pass

    def on_update_model():
        sel = lb.curselection()
        if not sel:
            messagebox.showwarning(t("model_update_btn"), t("model_update_select"), parent=win)
            return
        chosen = WHISPER_MODELS[sel[0]]
        if not is_model_downloaded(chosen, cache_root):
            messagebox.showinfo(t("model_update_btn"), t("model_update_not_downloaded", model=chosen), parent=win)
            return
        if not model_needs_update(chosen, cache_root):
            if not messagebox.askyesno(
                t("model_update_btn"),
                t("model_update_already_latest", model=chosen),
                parent=win,
            ):
                return
        update_btn.config(state="disabled")

        def worker():
            try:
                update_whisper_model(chosen, log_func=app.log, force=True)
                WhisperModelSingleton.reset()
                if app.whisper_model.get() == chosen:
                    try:
                        WhisperModelSingleton.get(app.log, app.device_mode.get(), chosen)
                    except Exception:
                        pass
            finally:
                def done():
                    model_dialog_refresh_listbox(app, lb, cache_root)
                    update_btn.config(state="normal")
                win.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    update_btn = ttk.Button(header_f, text=t("model_update_btn"), command=on_update_model)
    update_btn.pack(side="right", padx=(8, 0))

    def on_load():
        sel = lb.curselection()
        if not sel:
            return
        chosen = WHISPER_MODELS[sel[0]]
        app.whisper_model.set(chosen)
        WhisperModelSingleton.reset()
        try:
            WhisperModelSingleton.get(app.log, app.device_mode.get(), chosen)
        except Exception:
            pass
        app.model_btn.config(text=app._model_button_label())
        app._persist_settings()
        app.log(t("model_loaded", model=chosen))

    def on_ok():
        sel = lb.curselection()
        if sel:
            chosen = WHISPER_MODELS[sel[0]]
            app.whisper_model.set(chosen)
            app.model_btn.config(text=app._model_button_label())
            WhisperModelSingleton.reset()
            app._persist_settings()
            app.log(t("model_selected", model=chosen))
        win.destroy()

    def on_cancel():
        win.destroy()

    btn_f = ttk.Frame(main_f)
    btn_f.pack(fill="x", pady=(10, 0))
    ttk.Button(btn_f, text=t("model_load_btn"), command=on_load).pack(side="left", padx=2)
    ttk.Button(btn_f, text=t("ok"), command=on_ok).pack(side="left", padx=2)
    ttk.Button(btn_f, text=t("cancel"), command=on_cancel).pack(side="left", padx=2)
    win.protocol("WM_DELETE_WINDOW", on_cancel)
    center_toplevel(app, win)
    win.focus_set()


def show_cursor_api_key_dialog(app):
    """Окреме модальне вікно API-ключа; закриття через X не зберігає зміни."""
    dialog = tk.Toplevel(app.root)
    dialog.title(t("cursor_api_key_title"))
    dialog.transient(app.root)
    dialog.resizable(False, False)
    dialog.grab_set()

    frame = ttk.Frame(dialog, padding=15)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text=t("cursor_api_key_prompt")).pack(anchor="w", pady=(0, 6))

    draft_key = tk.StringVar(value=app.cursor_api_key.get())
    entry = ttk.Entry(frame, textvariable=draft_key, width=52, show="*")
    entry.pack(fill="x", pady=(0, 12))

    buttons = ttk.Frame(frame)
    buttons.pack(fill="x")

    def close_without_saving():
        dialog.destroy()

    def save_key():
        app.cursor_api_key.set((draft_key.get() or "").strip())
        app._persist_settings()
        dialog.destroy()

    ttk.Button(buttons, text=t("cancel_btn"), command=close_without_saving).pack(
        side="right", padx=(5, 0)
    )
    ttk.Button(buttons, text=t("save"), command=save_key).pack(side="right")

    dialog.protocol("WM_DELETE_WINDOW", close_without_saving)
    dialog.bind("<Escape>", lambda event: close_without_saving())
    dialog.bind("<Return>", lambda event: save_key())
    center_toplevel(app, dialog)
    entry.focus_set()

