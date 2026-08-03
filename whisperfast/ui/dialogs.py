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
    """Сумісність: відкриває спільне вікно AI API keys."""
    show_ai_api_keys_dialog(app)


def show_ai_api_keys_dialog(app):
    """Модальне вікно ключів Cursor / Gemini / Azure OpenAI (Copilot)."""
    dialog = tk.Toplevel(app.root)
    dialog.title(t("ai_api_keys_title"))
    dialog.transient(app.root)
    dialog.resizable(False, False)
    dialog.grab_set()

    frame = ttk.Frame(dialog, padding=15)
    frame.pack(fill="both", expand=True)

    cursor_key = tk.StringVar(value=app.cursor_api_key.get())
    gemini_key = tk.StringVar(value=app.gemini_api_key.get())
    gemini_model = tk.StringVar(value=app.gemini_model.get() or "gemini-2.0-flash")
    azure_endpoint = tk.StringVar(value=app.azure_openai_endpoint.get())
    azure_key = tk.StringVar(value=app.azure_openai_api_key.get())
    azure_deployment = tk.StringVar(value=app.azure_openai_deployment.get())
    azure_version = tk.StringVar(
        value=app.azure_openai_api_version.get() or "2024-08-01-preview"
    )

    def section(title_key):
        ttk.Label(frame, text=t(title_key), font=("Segoe UI", 9, "bold")).pack(
            anchor="w", pady=(10, 4)
        )

    section("ai_api_keys_cursor")
    ttk.Label(frame, text=t("cursor_api_key_prompt")).pack(anchor="w")
    ttk.Entry(frame, textvariable=cursor_key, width=56, show="*").pack(fill="x", pady=(2, 0))

    section("ai_api_keys_gemini")
    ttk.Label(frame, text=t("gemini_api_key_prompt")).pack(anchor="w")
    ttk.Entry(frame, textvariable=gemini_key, width=56, show="*").pack(fill="x", pady=(2, 4))
    model_row = ttk.Frame(frame)
    model_row.pack(fill="x")
    ttk.Label(model_row, text=t("gemini_model_label")).pack(side="left")
    ttk.Entry(model_row, textvariable=gemini_model, width=28).pack(
        side="left", fill="x", expand=True, padx=(8, 0)
    )

    section("ai_api_keys_copilot")
    ttk.Label(frame, text=t("azure_openai_hint"), wraplength=420).pack(anchor="w", pady=(0, 4))
    ttk.Label(frame, text=t("azure_openai_endpoint_label")).pack(anchor="w")
    ttk.Entry(frame, textvariable=azure_endpoint, width=56).pack(fill="x", pady=(2, 4))
    ttk.Label(frame, text=t("azure_openai_api_key_label")).pack(anchor="w")
    ttk.Entry(frame, textvariable=azure_key, width=56, show="*").pack(fill="x", pady=(2, 4))
    dep_row = ttk.Frame(frame)
    dep_row.pack(fill="x", pady=(0, 4))
    ttk.Label(dep_row, text=t("azure_openai_deployment_label")).pack(side="left")
    ttk.Entry(dep_row, textvariable=azure_deployment, width=24).pack(
        side="left", fill="x", expand=True, padx=(8, 0)
    )
    ver_row = ttk.Frame(frame)
    ver_row.pack(fill="x")
    ttk.Label(ver_row, text=t("azure_openai_api_version_label")).pack(side="left")
    ttk.Entry(ver_row, textvariable=azure_version, width=24).pack(
        side="left", fill="x", expand=True, padx=(8, 0)
    )

    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(14, 0))

    def close_without_saving():
        dialog.destroy()

    def save_keys():
        app.cursor_api_key.set((cursor_key.get() or "").strip())
        app.gemini_api_key.set((gemini_key.get() or "").strip())
        app.gemini_model.set((gemini_model.get() or "").strip() or "gemini-2.0-flash")
        app.azure_openai_endpoint.set((azure_endpoint.get() or "").strip().rstrip("/"))
        app.azure_openai_api_key.set((azure_key.get() or "").strip())
        app.azure_openai_deployment.set((azure_deployment.get() or "").strip())
        app.azure_openai_api_version.set(
            (azure_version.get() or "").strip() or "2024-08-01-preview"
        )
        app._persist_settings()
        dialog.destroy()

    ttk.Button(buttons, text=t("cancel_btn"), command=close_without_saving).pack(
        side="right", padx=(5, 0)
    )
    ttk.Button(buttons, text=t("save"), command=save_keys).pack(side="right")

    dialog.protocol("WM_DELETE_WINDOW", close_without_saving)
    dialog.bind("<Escape>", lambda event: close_without_saving())
    center_toplevel(app, dialog)


def show_cursor_prompts_dialog(app, file_name, prompts, on_result, provider_id=None):
    """Сумісність: делегує в show_ai_prompts_dialog."""
    return show_ai_prompts_dialog(
        app, file_name, prompts, on_result, provider_id=provider_id
    )


def show_ai_prompts_dialog(
    app, file_name, prompts, on_result, provider_id=None, cascade_offset=None
):
    """Вікно вибору промптів і AI-провайдера (можна відкрити кілька одночасно).

    on_result(selected_or_none, provider_id):
      - selected None — скасовано
      - list of prompts + provider_id — запуск
    cascade_offset: (dx, dy) від центру батьківського вікна — щоб не накривали одне одне.
    """
    from whisperfast.postprocess.providers import (
        PROVIDER_CURSOR,
        normalize_provider_id,
        provider_choices,
    )

    dialog = tk.Toplevel(app.root)
    dialog.title(t("cursor_prompts_title"))
    dialog.transient(app.root)
    dialog.minsize(440, 380)
    dialog.geometry("500x460")
    # Без grab_set: кілька вікон + кліки по логу лишаються активними

    settled = {"done": False}
    initial = normalize_provider_id(
        provider_id
        if provider_id is not None
        else (getattr(app, "ai_provider", None) and app.ai_provider.get())
        or PROVIDER_CURSOR
    )
    provider_var = tk.StringVar(value=initial)

    frame = ttk.Frame(dialog, padding=15)
    frame.pack(fill="both", expand=True)

    ttk.Label(
        frame,
        text=t("cursor_prompts_for_file", name=file_name),
        wraplength=460,
        font=("Segoe UI", 10, "bold"),
    ).pack(anchor="w", pady=(0, 4))
    ttk.Label(frame, text=t("cursor_prompts_hint"), wraplength=460).pack(
        anchor="w", pady=(0, 8)
    )

    ttk.Label(frame, text=t("ai_provider_label"), font=("Segoe UI", 9, "bold")).pack(
        anchor="w", pady=(0, 4)
    )
    prov_row = ttk.Frame(frame)
    prov_row.pack(fill="x", pady=(0, 10))
    for pid, label_key in provider_choices():
        ttk.Radiobutton(
            prov_row,
            text=t(label_key),
            variable=provider_var,
            value=pid,
        ).pack(side="left", padx=(0, 12))

    list_wrap = ttk.Frame(frame)
    list_wrap.pack(fill="both", expand=True)

    canvas = tk.Canvas(list_wrap, highlightthickness=0)
    scrollbar = ttk.Scrollbar(list_wrap, orient="vertical", command=canvas.yview)
    rows_frame = ttk.Frame(canvas)
    rows_frame.bind(
        "<Configure>",
        lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
    )
    canvas_window = canvas.create_window((0, 0), window=rows_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    def _on_canvas_configure(event):
        canvas.itemconfigure(canvas_window, width=event.width)

    canvas.bind("<Configure>", _on_canvas_configure)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    check_vars = []
    for i, (num, name, _text) in enumerate(prompts):
        var = tk.BooleanVar(value=(i == 0))
        check_vars.append(var)
        label = name or f"#{num}"
        row = ttk.Frame(rows_frame)
        row.pack(fill="x", pady=2)
        ttk.Checkbutton(row, variable=var).pack(side="left")
        name_lbl = ttk.Label(
            row,
            text=t("cursor_prompt_row", num=num, name=label),
            cursor="hand2",
        )
        name_lbl.pack(side="left", fill="x", expand=True, padx=(4, 0))

        def _toggle(_event=None, v=var):
            v.set(not v.get())

        def _run_one(_event=None, prompt=(num, name, _text)):
            finish([prompt])

        name_lbl.bind("<Button-1>", _toggle)
        name_lbl.bind("<Double-Button-1>", _run_one)

    def _on_mousewheel(event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    canvas.bind("<MouseWheel>", _on_mousewheel)
    rows_frame.bind("<MouseWheel>", _on_mousewheel)

    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(12, 0))
    buttons.columnconfigure(0, weight=1)
    buttons.columnconfigure(1, weight=2)

    def finish(result):
        if settled["done"]:
            return
        settled["done"] = True
        pid = normalize_provider_id(provider_var.get())
        try:
            dialog.destroy()
        except tk.TclError:
            pass
        if on_result:
            on_result(result, pid)

    def on_close():
        finish(None)

    def on_run_selected(_event=None):
        if settled["done"]:
            return "break"
        selected = [p for p, v in zip(prompts, check_vars) if v.get()]
        if not selected:
            messagebox.showwarning(
                t("cursor_prompts_title"),
                t("cursor_prompts_none_selected"),
                parent=dialog,
            )
            return "break"
        finish(selected)
        return "break"

    def on_all():
        """Як «Виконати» з усіма відміченими промптами."""
        for v in check_vars:
            v.set(True)
        on_run_selected()

    ttk.Button(buttons, text=t("cursor_prompts_all"), command=on_all).grid(
        row=0, column=0, sticky="ew", padx=(0, 6)
    )
    ttk.Button(buttons, text=t("cursor_prompts_run"), command=on_run_selected).grid(
        row=0, column=1, sticky="ew"
    )

    def _bind_space(widget):
        widget.bind("<KeyPress-space>", on_run_selected)
        widget.bind("<space>", on_run_selected)
        for child in widget.winfo_children():
            _bind_space(child)

    dialog.protocol("WM_DELETE_WINDOW", on_close)
    dialog.bind("<Escape>", lambda e: on_close())
    dialog.bind("<KeyPress-space>", on_run_selected)
    dialog.bind("<space>", on_run_selected)
    center_toplevel(app, dialog)
    if cascade_offset:
        dx, dy = cascade_offset
        try:
            dialog.update_idletasks()
            geo = dialog.geometry()  # WxH+X+Y
            parts = geo.split("+")
            if len(parts) >= 3:
                x = int(parts[1]) + int(dx)
                y = int(parts[2]) + int(dy)
                sw = dialog.winfo_screenwidth()
                sh = dialog.winfo_screenheight()
                w = dialog.winfo_width() or 500
                h = dialog.winfo_height() or 460
                x = max(0, min(x, sw - w))
                y = max(0, min(y, sh - h))
                dialog.geometry(f"+{x}+{y}")
        except (tk.TclError, ValueError, IndexError):
            pass
    dialog.update_idletasks()
    _bind_space(dialog)
    dialog.focus_set()
    return dialog

