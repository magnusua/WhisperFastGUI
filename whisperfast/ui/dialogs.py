"""Modal dialogs for WhisperGUI."""
import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from typing import List, Optional

from whisperfast.config import (
    DEFAULT_MODEL,
    VALID_EXTS,
    WHISPER_MODELS,
    find_whisper_model_cache_path,
    get_whisper_cache_dir,
    list_help_documents,
    load_help_document,
)
from whisperfast.core.input_files import (
    get_file_dialog_filetypes,
    get_valid_files_from_directory,
    is_valid_file,
    validate_and_filter_files,
)
from whisperfast.core.model_manager import WhisperModelSingleton
from whisperfast.core.output_conflict import ai_prompts_dialog_is_open
from whisperfast.i18n import get_language, t
from whisperfast.ui.widgets import Tooltip
from whisperfast.updates.model_updates import (
    is_model_downloaded,
    model_needs_update,
    update_whisper_model,
)
from whisperfast.updates.release_notes import format_release_notes_text
from whisperfast.utils import normalize_display_path


def _next_entry_batch_size(current_count):
    """Скільки порожніх рядків додає кнопка +: 1 → 5 → 10 (для open_watch_dirs_dialog)."""
    if current_count < 5:
        return 1
    if current_count < 10:
        return 5
    return 10


def open_watch_dirs_dialog(parent, initial_dirs, on_save, center_fn=None):
    """
    Модальне вікно списку каталогів слідкування.
    Зберегти — викликає on_save(list); закриття вікна = скасування.

    Перенесено з core/queue_manager.py (core має лишатись без Tkinter,
    а показ модального вікна вибору каталогів — це UI-дія) — див.
    docs/INTERNAL-ARCHITECTURE.uk.md і docs/CODE-REVIEW.md, розділ 1.
    """
    initial = list(initial_dirs or [])
    if not initial:
        initial = [""]

    dialog = tk.Toplevel(parent)
    dialog.title(t("watch_dirs_dialog_title"))
    dialog.transient(parent)
    dialog.grab_set()
    dialog.resizable(True, True)

    outer = ttk.Frame(dialog, padding=12)
    outer.pack(fill="both", expand=True)

    ttk.Label(outer, text=t("watch_dirs_dialog_hint")).pack(anchor="w", pady=(0, 8))

    entries_host = ttk.Frame(outer)
    entries_host.pack(fill="both", expand=True)

    entry_vars = []

    def _rebuild_layout():
        for child in entries_host.winfo_children():
            child.destroy()
        n = len(entry_vars)
        cols = 2 if n > 10 else 1
        rows_per_col = (n + cols - 1) // cols if cols else n
        for i, var in enumerate(entry_vars):
            col = i // rows_per_col if cols > 1 else 0
            row = i % rows_per_col if cols > 1 else i
            cell = ttk.Frame(entries_host)
            cell.grid(row=row, column=col, sticky="ew", padx=4, pady=2)
            ent = ttk.Entry(cell, textvariable=var, width=42)
            ent.pack(side="left", fill="x", expand=True)

            def browse(v=var):
                d = filedialog.askdirectory(parent=dialog)
                if d:
                    v.set(normalize_display_path(d))

            ttk.Button(cell, text="…", width=3, command=browse).pack(side="left", padx=(4, 0))
        for c in range(cols):
            entries_host.columnconfigure(c, weight=1)
        _resize_dialog()

    def _resize_dialog():
        dialog.update_idletasks()
        n = len(entry_vars)
        cols = 2 if n > 10 else 1
        rows = (n + cols - 1) // cols
        row_h = 32
        base_h = 120
        base_w = 520 if cols == 1 else 980
        h = min(base_h + rows * row_h, 700)
        w = base_w
        dialog.geometry(f"{w}x{h}")
        if center_fn:
            center_fn(dialog)

    def add_rows():
        batch = _next_entry_batch_size(len(entry_vars))
        for _ in range(batch):
            entry_vars.append(tk.StringVar(value=""))
        _rebuild_layout()

    for path in initial:
        entry_vars.append(tk.StringVar(value=normalize_display_path(path) if path else ""))
    _rebuild_layout()

    btns = ttk.Frame(outer)
    btns.pack(fill="x", pady=(10, 0))

    def close_cancel():
        dialog.destroy()

    def save_and_close():
        dirs = []
        for var in entry_vars:
            raw = (var.get() or "").strip()
            if not raw:
                continue
            path = normalize_display_path(raw.strip('"').strip("'"))
            if not path:
                continue
            if not os.path.isdir(path):
                messagebox.showerror(
                    t("error"),
                    t("watch_dir_invalid", path=path),
                    parent=dialog,
                )
                return
            dirs.append(path)
        on_save(dirs)
        dialog.destroy()

    ttk.Button(btns, text="+", width=4, command=add_rows).pack(side="left")
    ttk.Button(btns, text=t("save"), command=save_and_close).pack(side="right", padx=(5, 0))
    ttk.Button(btns, text=t("cancel_btn"), command=close_cancel).pack(side="right")

    dialog.protocol("WM_DELETE_WINDOW", close_cancel)
    dialog.bind("<Escape>", lambda e: close_cancel())
    if center_fn:
        center_fn(dialog)
    else:
        dialog.update_idletasks()
        dialog.geometry("+%d+%d" % (parent.winfo_rootx() + 40, parent.winfo_rooty() + 40))
    dialog.wait_window()


# --- Диалоги выбора файла/каталога (перенесены из core/input_files.py: core должен
# оставаться независимым от Tkinter, а показ файлового диалога — это UI-действие,
# см. docs/INTERNAL-ARCHITECTURE.uk.md и docs/CODE-REVIEW.md, раздел 1) ---

def add_single_file():
    """
    Диалог выбора одного файла.

    Returns:
        Путь к выбранному файлу или None
    """
    file_path = filedialog.askopenfilename(title=t("select_file"), filetypes=get_file_dialog_filetypes())

    if file_path and is_valid_file(file_path):
        return file_path
    elif file_path:
        messagebox.showwarning(
            t("unsupported_format"),
            t("unsupported_format_msg", filename=os.path.basename(file_path), formats=', '.join(VALID_EXTS))
        )

    return None


def add_multiple_files():
    """
    Диалог выбора нескольких файлов.

    Returns:
        Список путей к выбранным файлам
    """
    file_paths = filedialog.askopenfilenames(title=t("select_files"), filetypes=get_file_dialog_filetypes())

    if not file_paths:
        return []

    valid_files, invalid_files, _ = validate_and_filter_files(file_paths)

    if invalid_files:
        invalid_names = [os.path.basename(f) for f in invalid_files[:5]]
        files_str = ', '.join(invalid_names) + ("..." if len(invalid_files) > 5 else "")
        messagebox.showwarning(
            t("unsupported_formats"),
            t("unsupported_formats_msg", files=files_str)
        )

    return valid_files


def add_directory(recursive=True):
    """
    Диалог выбора каталога с добавлением всех валидных файлов из него.

    Args:
        recursive: Если True, обрабатывает вложенные каталоги рекурсивно

    Returns:
        Список путей к валидным файлам из каталога
    """
    directory = filedialog.askdirectory(
        title=t("select_directory")
    )

    if not directory:
        return []

    if not os.path.isdir(directory):
        messagebox.showerror(t("error_not_directory"), t("error_not_directory_msg"))
        return []

    valid_files = get_valid_files_from_directory(directory, recursive=recursive)

    if not valid_files:
        messagebox.showinfo(
            t("files_not_found"),
            t("files_not_found_msg", dirname=os.path.basename(directory), formats=', '.join(VALID_EXTS))
        )
    else:
        messagebox.showinfo(
            t("files_added"),
            t("files_added_msg", count=len(valid_files))
        )

    return valid_files


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


def ask_overwrite_via_tk(app, path: str, alt_name: str) -> Optional[bool]:
    """
    Blocking ask from a worker thread using Tk main loop.
    Returns True (overwrite), False (use timed name), or None (skip write / closed).

    Якщо відкрите вікно «Промты» — не питаємо (щоб діалог не ховався
    під ним і не стопорив Whisper); одразу збереження з суфіксом часу.

    Перенесено з core/output_conflict.py (core має лишатись без Tkinter) —
    див. docs/INTERNAL-ARCHITECTURE.uk.md.
    """
    if ai_prompts_dialog_is_open(app):
        return False

    pending = object()
    choice: List[object] = [pending]
    done = threading.Event()

    def ask():
        try:
            # Якщо за час очікування відкрили «Промты» — без запитання
            if ai_prompts_dialog_is_open(app):
                choice[0] = False
                done.set()
                return

            parent = getattr(app, "root", None)
            dlg = tk.Toplevel(parent)
            dlg.title(t("file_exists_title"))
            dlg.resizable(False, False)
            if parent is not None:
                dlg.transient(parent)
            dlg.grab_set()

            body = ttk.Frame(dlg, padding=16)
            body.pack(fill="both", expand=True)
            ttk.Label(
                body,
                text=t(
                    "file_exists_msg",
                    name=os.path.basename(path),
                    alt=alt_name,
                ),
                justify="left",
                wraplength=420,
            ).pack(anchor="w")

            bf = ttk.Frame(body)
            bf.pack(fill="x", pady=(16, 0))

            def finish(val: Optional[bool]):
                choice[0] = val
                try:
                    dlg.grab_release()
                except Exception:
                    pass
                try:
                    dlg.destroy()
                except Exception:
                    pass
                done.set()

            # Right-aligned: Yes | No | Skip
            ttk.Button(bf, text=t("file_exists_skip"), command=lambda: finish(None)).pack(
                side="right"
            )
            ttk.Button(bf, text=t("file_exists_no"), command=lambda: finish(False)).pack(
                side="right", padx=(0, 8)
            )
            yes_btn = ttk.Button(bf, text=t("file_exists_yes"), command=lambda: finish(True))
            yes_btn.pack(side="right", padx=(0, 8))

            dlg.protocol("WM_DELETE_WINDOW", lambda: finish(None))
            dlg.bind("<Escape>", lambda _e: finish(None))
            dlg.bind("<Return>", lambda _e: finish(True))

            dlg.update_idletasks()
            if parent is not None:
                try:
                    center_toplevel(app, dlg, parent=parent)
                except Exception:
                    pass
            yes_btn.focus_set()
        except Exception:
            choice[0] = False
            done.set()

    try:
        app.root.after(0, ask)
    except Exception:
        return False

    while not done.is_set():
        if getattr(app, "cancel_requested", False):
            return False
        if ai_prompts_dialog_is_open(app):
            return False
        done.wait(timeout=0.05)

    result = choice[0]
    if result is True:
        return True
    if result is False:
        return False
    return None


def track_i18n_window(app, window, refresh_cb):
    """Реєструє Toplevel для оновлення мови при зміні UI language."""
    registry = getattr(app, "_i18n_windows", None)
    if registry is None:
        app._i18n_windows = []
        registry = app._i18n_windows
    entry = {"win": window, "refresh": refresh_cb}
    registry.append(entry)

    def _on_destroy(event):
        if event.widget is not window:
            return
        lst = getattr(app, "_i18n_windows", None)
        if not lst:
            return
        app._i18n_windows = [e for e in lst if e.get("win") is not window]

    window.bind("<Destroy>", _on_destroy, add="+")
    return entry


def refresh_i18n_windows(app):
    """Оновити всі зареєстровані відкриті діалоги під поточну мову."""
    lst = getattr(app, "_i18n_windows", None) or []
    alive = []
    for entry in lst:
        win = entry.get("win")
        refresh = entry.get("refresh")
        try:
            if win is None or not win.winfo_exists():
                continue
            if callable(refresh):
                refresh()
            alive.append(entry)
        except tk.TclError:
            continue
    app._i18n_windows = alive


def _lift_existing(app, attr_name):
    """Якщо вікно вже відкрите — підняти і повернути його, інакше None."""
    win = getattr(app, attr_name, None)
    if win is None:
        return None
    try:
        if win.winfo_exists():
            win.lift()
            win.focus_force()
            return win
    except tk.TclError:
        pass
    setattr(app, attr_name, None)
    return None


def show_help(app):
    """Показывает окно справки с прокруткой; список документов из docs/; при смене языка текст обновляется."""
    existing = _lift_existing(app, "_help_window")
    if existing is not None:
        return existing

    help_window = tk.Toplevel(app.root)
    app._help_window = help_window
    help_window.title(t("help_title"))
    help_window.transient(app.root)

    app.root.update_idletasks()
    main_width = app.root.winfo_width()
    main_height = app.root.winfo_height()
    help_width = max(700, int(main_width * 0.85))
    help_height = max(600, int(main_height * 0.85))
    help_window.geometry(f"{help_width}x{help_height}")
    center_toplevel(app, help_window)

    main_frame = ttk.Frame(help_window, padding=10)
    main_frame.pack(fill="both", expand=True)

    docs = list_help_documents() or [("user", "help_doc_user", None)]
    selected = ["user"]

    nav = ttk.Frame(main_frame)
    nav.pack(fill="x", pady=(0, 8))
    doc_label = ttk.Label(nav, text=t("help_doc_label"))
    doc_label.pack(side="left")
    doc_combo = ttk.Combobox(nav, state="readonly")
    doc_combo.pack(side="left", fill="x", expand=True, padx=(8, 0))

    text_widget = scrolledtext.ScrolledText(
        main_frame,
        wrap="word",
        font=("Segoe UI", 10),
        padx=15,
        pady=15,
        state="normal",
        relief="flat",
        borderwidth=1,
    )
    text_widget.pack(fill="both", expand=True)

    btn_frame = ttk.Frame(main_frame)
    btn_frame.pack(fill="x", pady=(10, 0))
    close_btn = ttk.Button(btn_frame, text=t("close"), width=15)
    close_btn.pack(side="right")

    def _ids():
        return [item[0] for item in docs]

    def _fill_combo():
        labels = [t(item[1]) for item in docs]
        doc_combo["values"] = labels
        ids = _ids()
        try:
            doc_combo.current(ids.index(selected[0]))
        except ValueError:
            selected[0] = ids[0]
            doc_combo.current(0)

    def _show_current():
        lang = app.ui_language.get() if getattr(app, "ui_language", None) else None
        text_widget.config(state="normal")
        text_widget.delete("1.0", "end")
        text_widget.insert("1.0", load_help_document(selected[0], lang))
        text_widget.config(state="disabled")
        text_widget.see("1.0")

    def on_doc_selected(_event=None):
        idx = doc_combo.current()
        ids = _ids()
        if 0 <= idx < len(ids):
            selected[0] = ids[idx]
        _show_current()

    def apply_language():
        help_window.title(t("help_title"))
        close_btn.config(text=t("close"))
        doc_label.config(text=t("help_doc_label"))
        _fill_combo()
        _show_current()

    def close():
        try:
            help_window.destroy()
        except tk.TclError:
            pass
        if getattr(app, "_help_window", None) is help_window:
            app._help_window = None

    close_btn.config(command=close)
    help_window.protocol("WM_DELETE_WINDOW", close)
    doc_combo.bind("<<ComboboxSelected>>", on_doc_selected)
    apply_language()
    track_i18n_window(app, help_window, apply_language)

    def on_mousewheel(event):
        text_widget.yview_scroll(int(-1 * (event.delta / 120)), "units")

    text_widget.bind("<MouseWheel>", on_mousewheel)
    text_widget.focus_set()
    return help_window


def show_release_notes(app):
    """Вікно реліз-нотів (EN/UK/RU з resources/release_notes.json)."""
    existing = _lift_existing(app, "_release_notes_window")
    if existing is not None:
        return existing

    win = tk.Toplevel(app.root)
    app._release_notes_window = win
    win.title(t("release_notes_title"))
    win.transient(app.root)

    app.root.update_idletasks()
    main_width = app.root.winfo_width()
    main_height = app.root.winfo_height()
    w = max(560, int(main_width * 0.7))
    h = max(480, int(main_height * 0.75))
    win.geometry(f"{w}x{h}")
    center_toplevel(app, win)

    main_frame = ttk.Frame(win, padding=10)
    main_frame.pack(fill="both", expand=True)

    text_widget = scrolledtext.ScrolledText(
        main_frame,
        wrap="word",
        font=("Segoe UI", 10),
        padx=15,
        pady=15,
        state="normal",
        relief="flat",
        borderwidth=1,
    )
    text_widget.pack(fill="both", expand=True)

    btn_frame = ttk.Frame(main_frame)
    btn_frame.pack(fill="x", pady=(10, 0))
    close_btn = ttk.Button(btn_frame, text=t("close"), width=15)
    close_btn.pack(side="right")

    def apply_language():
        win.title(t("release_notes_title"))
        close_btn.config(text=t("close"))
        text_widget.config(state="normal")
        text_widget.delete("1.0", "end")
        lang = app.ui_language.get() if getattr(app, "ui_language", None) else None
        text_widget.insert("1.0", format_release_notes_text(lang))
        text_widget.config(state="disabled")
        text_widget.see("1.0")

    def close():
        try:
            win.destroy()
        except tk.TclError:
            pass
        if getattr(app, "_release_notes_window", None) is win:
            app._release_notes_window = None

    close_btn.config(command=close)
    win.protocol("WM_DELETE_WINDOW", close)
    apply_language()
    track_i18n_window(app, win, apply_language)

    def on_mousewheel(event):
        text_widget.yview_scroll(int(-1 * (event.delta / 120)), "units")

    text_widget.bind("<MouseWheel>", on_mousewheel)
    text_widget.focus_set()
    return win


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
    dialog.resizable(True, False)
    dialog.grab_set()

    frame = ttk.Frame(dialog, padding=15)
    frame.pack(fill="both", expand=True)
    hint_lbl = ttk.Label(frame, text=t("output_settings_hint"))
    hint_lbl.pack(anchor="w", pady=(0, 8))

    mode_var = tk.StringVar(value=app.output_mode.get() or "beside")
    dir_var = tk.StringVar(value=app.output_dir.get() or "")
    named_var = tk.StringVar(value=app.output_named_folder.get() or "{basename}")

    beside_radio = ttk.Radiobutton(
        frame, text=t("save_mode_beside"), variable=mode_var, value="beside"
    )
    beside_radio.pack(anchor="w", pady=2)

    custom_row = ttk.Frame(frame)
    custom_row.pack(fill="x", pady=2)
    custom_radio = ttk.Radiobutton(
        custom_row, text=t("save_mode_custom"), variable=mode_var, value="custom"
    )
    custom_radio.pack(side="left")
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
    named_radio = ttk.Radiobutton(
        named_row, text=t("save_mode_named_folder"), variable=mode_var, value="named_folder"
    )
    named_radio.pack(side="left")
    named_entry = ttk.Entry(named_row, textvariable=named_var, width=28)
    named_entry.pack(side="left", fill="x", expand=True, padx=(8, 0))

    custom_named_row = ttk.Frame(frame)
    custom_named_row.pack(fill="x", pady=2)
    custom_named_radio = ttk.Radiobutton(
        custom_named_row,
        text=t("save_mode_custom_named"),
        variable=mode_var,
        value="custom_named",
    )
    custom_named_radio.pack(side="left")
    cn_dir_entry = ttk.Entry(custom_named_row, textvariable=dir_var, width=22)
    cn_dir_entry.pack(side="left", fill="x", expand=True, padx=(8, 4))

    def browse_custom_named():
        d = filedialog.askdirectory(parent=dialog)
        if d:
            dir_var.set(normalize_display_path(d))
            mode_var.set("custom_named")

    ttk.Button(custom_named_row, text="…", width=3, command=browse_custom_named).pack(
        side="left"
    )
    custom_named_with_lbl = ttk.Label(
        custom_named_row, text=t("save_mode_custom_named_with")
    )
    custom_named_with_lbl.pack(side="left", padx=(8, 4))
    cn_named_entry = ttk.Entry(custom_named_row, textvariable=named_var, width=16)
    cn_named_entry.pack(side="left", fill="x", expand=True)
    dir_entry.bind("<FocusIn>", lambda e: mode_var.set("custom"))
    named_entry.bind("<FocusIn>", lambda e: mode_var.set("named_folder"))
    cn_dir_entry.bind("<FocusIn>", lambda e: mode_var.set("custom_named"))
    cn_named_entry.bind("<FocusIn>", lambda e: mode_var.set("custom_named"))

    named_hint_lbl = ttk.Label(frame, text=t("save_named_folder_hint"), wraplength=520)
    named_hint_lbl.pack(anchor="w", pady=(4, 0))

    extra = ttk.LabelFrame(frame, text=t("export_extra_title"))
    extra.pack(fill="x", pady=(10, 0))
    json_var = tk.BooleanVar(value=bool(getattr(app, "export_json", tk.BooleanVar(value=False)).get()))
    vtt_var = tk.BooleanVar(value=bool(getattr(app, "export_vtt", tk.BooleanVar(value=False)).get()))
    words_var = tk.BooleanVar(
        value=bool(getattr(app, "word_timestamps", tk.BooleanVar(value=False)).get())
    )
    diar_var = tk.BooleanVar(
        value=bool(getattr(app, "diarization_enabled", tk.BooleanVar(value=False)).get())
    )
    json_chk = ttk.Checkbutton(extra, text=t("export_json"), variable=json_var)
    json_chk.pack(anchor="w")
    vtt_chk = ttk.Checkbutton(extra, text=t("export_vtt"), variable=vtt_var)
    vtt_chk.pack(anchor="w")
    words_chk = ttk.Checkbutton(extra, text=t("word_timestamps"), variable=words_var)
    words_chk.pack(anchor="w")
    diar_chk = ttk.Checkbutton(extra, text=t("diarization_enabled"), variable=diar_var)
    diar_chk.pack(anchor="w")

    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(12, 0))

    def close_cancel():
        dialog.destroy()

    def save_and_close():
        mode = mode_var.get() or "beside"
        path = normalize_display_path((dir_var.get() or "").strip())
        named = (named_var.get() or "").strip() or "{basename}"
        if mode in ("custom", "custom_named"):
            if not path or not os.path.isdir(path):
                messagebox.showerror(t("error"), t("save_dir_invalid"), parent=dialog)
                return
        if mode in ("named_folder", "custom_named") and not app._sanitize_folder_name(
            named.replace("{basename}", "x").replace("{name}", "x")
        ):
            messagebox.showerror(t("error"), t("save_named_folder_invalid"), parent=dialog)
            return
        app.output_mode.set(mode)
        app.output_dir.set(
            path if mode in ("custom", "custom_named") else (path if os.path.isabs(path) else "")
        )
        app.output_named_folder.set(named)
        if hasattr(app, "export_json"):
            app.export_json.set(bool(json_var.get()))
            app.export_vtt.set(bool(vtt_var.get()))
            app.word_timestamps.set(bool(words_var.get()))
            app.diarization_enabled.set(bool(diar_var.get()))
        app._persist_settings()
        dialog.destroy()

    cancel_btn = ttk.Button(buttons, text=t("cancel_btn"), command=close_cancel)
    cancel_btn.pack(side="right", padx=(5, 0))
    save_btn = ttk.Button(buttons, text=t("save"), command=save_and_close)
    save_btn.pack(side="right")

    def apply_language():
        dialog.title(t("output_settings_title"))
        hint_lbl.config(text=t("output_settings_hint"))
        beside_radio.config(text=t("save_mode_beside"))
        custom_radio.config(text=t("save_mode_custom"))
        named_radio.config(text=t("save_mode_named_folder"))
        custom_named_radio.config(text=t("save_mode_custom_named"))
        custom_named_with_lbl.config(text=t("save_mode_custom_named_with"))
        named_hint_lbl.config(text=t("save_named_folder_hint"))
        cancel_btn.config(text=t("cancel_btn"))
        save_btn.config(text=t("save"))

    apply_language()
    track_i18n_window(app, dialog, apply_language)
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


def _queue_is_busy(app):
    """True while the transcription/document queue worker holds its lock."""
    lock = getattr(app, "_process_queue_lock", None)
    return bool(lock is not None and lock.locked())


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
        if _queue_is_busy(app):
            messagebox.showwarning(t("model_update_btn"), t("model_change_while_busy"), parent=win)
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
        if _queue_is_busy(app):
            messagebox.showwarning(t("model_dialog_title"), t("model_change_while_busy"), parent=win)
            return
        chosen = WHISPER_MODELS[sel[0]]
        app.whisper_model.set(chosen)
        WhisperModelSingleton.reset()
        try:
            WhisperModelSingleton.get(app.log, app.device_mode.get(), chosen)
        except Exception:
            # WhisperModelSingleton.get() already logged model_load_error.
            return
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


def show_ai_api_keys_dialog(app):
    """Модальне вікно ключів Cursor / Gemini / Claude / Azure OpenAI (Copilot)."""
    dialog = tk.Toplevel(app.root)
    dialog.title(t("ai_api_keys_title"))
    dialog.transient(app.root)
    dialog.resizable(True, True)
    dialog.minsize(480, 520)
    dialog.grab_set()

    frame = ttk.Frame(dialog, padding=15)
    frame.pack(fill="both", expand=True)

    cursor_key = tk.StringVar(value=app.cursor_api_key.get())
    gemini_key = tk.StringVar(value=app.gemini_api_key.get())
    gemini_model = tk.StringVar(value=app.gemini_model.get() or "gemini-2.0-flash")
    anthropic_key = tk.StringVar(value=app.anthropic_api_key.get())
    claude_model = tk.StringVar(value=app.claude_model.get() or "claude-sonnet-4-5")
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
    ttk.Label(frame, text=t("gemini_api_key_prompt"), wraplength=520, justify="left").pack(anchor="w")
    ttk.Entry(frame, textvariable=gemini_key, width=56, show="*").pack(fill="x", pady=(2, 4))
    model_row = ttk.Frame(frame)
    model_row.pack(fill="x")
    ttk.Label(model_row, text=t("gemini_model_label")).pack(side="left")
    ttk.Entry(model_row, textvariable=gemini_model, width=28).pack(
        side="left", fill="x", expand=True, padx=(8, 0)
    )
    ttk.Label(frame, text=t("gemini_oauth_hint"), wraplength=520, justify="left").pack(
        anchor="w", pady=(8, 4)
    )
    capture_cfg = getattr(app, "capture_cfg", None) or {}
    gemini_email = tk.StringVar(value=(getattr(app, "gemini_oauth_email", None) and app.gemini_oauth_email.get() or ""))
    google_client = tk.StringVar(
        value=(getattr(app, "google_oauth_client_id", None) and app.google_oauth_client_id.get() or "").strip()
        or str(capture_cfg.get("google_calendar_client_id") or "").strip()
    )
    google_secret = tk.StringVar(
        value=(getattr(app, "google_oauth_client_secret", None) and app.google_oauth_client_secret.get() or "").strip()
        or str(capture_cfg.get("google_calendar_client_secret") or "").strip()
    )
    google_project = tk.StringVar(
        value=(getattr(app, "google_cloud_project_id", None) and app.google_cloud_project_id.get() or "").strip()
    )
    ttk.Label(frame, text=t("calendar_google_client_id")).pack(anchor="w")
    ttk.Entry(frame, textvariable=google_client).pack(fill="x")
    ttk.Label(frame, text=t("calendar_google_secret_optional")).pack(anchor="w", pady=(4, 0))
    ttk.Entry(frame, textvariable=google_secret, show="*").pack(fill="x")
    ttk.Label(frame, text=t("gemini_oauth_project")).pack(anchor="w", pady=(4, 0))
    ttk.Entry(frame, textvariable=google_project).pack(fill="x")
    gemini_status = ttk.Label(frame, text="", wraplength=520, justify="left")
    gemini_status.pack(anchor="w", pady=(6, 2))
    gemini_busy = {"on": False, "session": None}

    def _gemini_refresh_token():
        var = getattr(app, "gemini_oauth_refresh_token", None)
        raw = (var.get() if var is not None else "") or ""
        from whisperfast.secrets_store import unprotect_string

        return unprotect_string(str(raw))

    def _refresh_gemini_status(extra: str = ""):
        if extra:
            gemini_status.configure(text=extra)
            return
        who = (gemini_email.get() or "").strip()
        if who or _gemini_refresh_token():
            gemini_status.configure(text=t("gemini_oauth_signed_in", email=who or "Google"))
        else:
            gemini_status.configure(text="")

    def _close_gemini_session():
        session = gemini_busy.get("session")
        gemini_busy["session"] = None
        gemini_busy["on"] = False
        if session is not None:
            try:
                session.close()
            except Exception:
                pass

    def _persist_gemini_oauth():
        if hasattr(app, "google_oauth_client_id"):
            app.google_oauth_client_id.set(google_client.get().strip())
        if hasattr(app, "google_oauth_client_secret"):
            app.google_oauth_client_secret.set(google_secret.get())
        if hasattr(app, "google_cloud_project_id"):
            app.google_cloud_project_id.set(google_project.get().strip())
        if hasattr(app, "gemini_oauth_email"):
            app.gemini_oauth_email.set(gemini_email.get().strip())
        persist = getattr(app, "_persist_settings", None)
        if callable(persist):
            persist()

    def do_gemini_login():
        if gemini_busy["on"]:
            return
        from whisperfast.core.google_oauth import (
            CLIENT_ID_ENV,
            GEMINI_SCOPE,
            GOOGLE_CLOUD_CREDENTIALS_URL,
            GOOGLE_GENERATIVE_LANGUAGE_API_URL,
            GoogleLoopbackAuth,
            resolve_google_client_id,
        )
        from whisperfast.postprocess.common import copy_text_to_clipboard, open_url_in_browser
        from whisperfast.postprocess.providers.gemini import store_gemini_session
        from whisperfast.secrets_store import unprotect_string

        client_id = resolve_google_client_id(google_client.get())
        if not client_id:
            copy_text_to_clipboard("http://127.0.0.1")
            open_url_in_browser(GOOGLE_GENERATIVE_LANGUAGE_API_URL)
            open_url_in_browser(GOOGLE_CLOUD_CREDENTIALS_URL)
            messagebox.showinfo(
                t("ai_api_keys_title"),
                t("gemini_oauth_need_client", env=CLIENT_ID_ENV),
                parent=dialog,
            )
            return
        google_client.set(client_id)
        try:
            session = GoogleLoopbackAuth(
                client_id,
                google_secret.get().strip(),
                scope=GEMINI_SCOPE,
                success_message="Gemini is connected. You can close this tab.",
            )
            session.open_browser()
        except Exception as e:
            if "session" in locals():
                try:
                    session.close()
                except Exception:
                    pass
            messagebox.showerror(t("ai_api_keys_title"), str(e), parent=dialog)
            return
        gemini_busy["on"] = True
        gemini_busy["session"] = session
        gemini_login_btn.configure(state="disabled")
        _refresh_gemini_status(t("gemini_oauth_waiting"))

        def worker():
            err = ""
            token = None
            try:
                token = session.wait(180)
            except Exception as e:
                err = str(e)
            finally:
                try:
                    session.close()
                except Exception:
                    pass

            def done():
                gemini_busy["session"] = None
                gemini_busy["on"] = False
                try:
                    if gemini_login_btn.winfo_exists():
                        gemini_login_btn.configure(state="normal")
                except tk.TclError:
                    return
                if token:
                    refresh = token.get("refresh_token") or unprotect_string(
                        str(
                            (getattr(app, "gemini_oauth_refresh_token", None) and app.gemini_oauth_refresh_token.get())
                            or ""
                        )
                    )
                    if not refresh:
                        messagebox.showerror(
                            t("ai_api_keys_title"), t("calendar_google_no_refresh"), parent=dialog
                        )
                        _refresh_gemini_status()
                        return
                    email = str(token.get("email") or "").strip()
                    box = {}
                    store_gemini_session(box, refresh, email)
                    if hasattr(app, "gemini_oauth_refresh_token"):
                        app.gemini_oauth_refresh_token.set(box.get("gemini_oauth_refresh_token") or "")
                    gemini_email.set(email)
                    _persist_gemini_oauth()
                    _refresh_gemini_status()
                    messagebox.showinfo(
                        t("ai_api_keys_title"),
                        t("gemini_oauth_ok", email=email or "Google"),
                        parent=dialog,
                    )
                    return
                if err == "timeout":
                    messagebox.showinfo(
                        t("ai_api_keys_title"), t("calendar_google_timeout"), parent=dialog
                    )
                elif err:
                    messagebox.showerror(t("ai_api_keys_title"), err, parent=dialog)
                _refresh_gemini_status()

            try:
                dialog.after(0, done)
            except tk.TclError:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def do_gemini_logout():
        from whisperfast.postprocess.providers.gemini import clear_gemini_session

        _close_gemini_session()
        box = {}
        clear_gemini_session(box)
        if hasattr(app, "gemini_oauth_refresh_token"):
            app.gemini_oauth_refresh_token.set("")
        gemini_email.set("")
        _persist_gemini_oauth()
        _refresh_gemini_status()

    g_btns = ttk.Frame(frame)
    g_btns.pack(fill="x", pady=4)
    gemini_login_btn = ttk.Button(g_btns, text=t("gemini_oauth_login"), command=do_gemini_login)
    gemini_login_btn.pack(side="left")
    ttk.Button(g_btns, text=t("calendar_google_logout"), command=do_gemini_logout).pack(
        side="left", padx=6
    )
    _refresh_gemini_status()

    section("ai_api_keys_claude")
    ttk.Label(frame, text=t("claude_api_key_prompt")).pack(anchor="w")
    ttk.Entry(frame, textvariable=anthropic_key, width=56, show="*").pack(fill="x", pady=(2, 4))
    claude_model_row = ttk.Frame(frame)
    claude_model_row.pack(fill="x")
    ttk.Label(claude_model_row, text=t("claude_model_label")).pack(side="left")
    ttk.Entry(claude_model_row, textvariable=claude_model, width=28).pack(
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

    ollama_url = tk.StringVar(value=getattr(app, "ollama_base_url", tk.StringVar(value="")).get())
    ollama_model = tk.StringVar(value=getattr(app, "ollama_model", tk.StringVar(value="")).get() or "llama3.2")
    compat_url = tk.StringVar(
        value=getattr(app, "openai_compatible_base_url", tk.StringVar(value="")).get()
    )
    compat_key = tk.StringVar(
        value=getattr(app, "openai_compatible_api_key", tk.StringVar(value="")).get()
    )
    compat_model = tk.StringVar(
        value=getattr(app, "openai_compatible_model", tk.StringVar(value="")).get()
    )
    budget_var = tk.StringVar(
        value=str(getattr(app, "ai_month_budget", tk.DoubleVar(value=0.0)).get() or 0)
    )

    section("ai_api_keys_ollama")
    ttk.Label(frame, text=t("ollama_url_label")).pack(anchor="w")
    ttk.Entry(frame, textvariable=ollama_url, width=56).pack(fill="x", pady=(2, 4))
    om_row = ttk.Frame(frame)
    om_row.pack(fill="x")
    ttk.Label(om_row, text=t("ollama_model_label")).pack(side="left")
    ttk.Entry(om_row, textvariable=ollama_model, width=28).pack(
        side="left", fill="x", expand=True, padx=(8, 0)
    )

    section("ai_api_keys_openai_compat")
    ttk.Label(frame, text=t("openai_compat_hint"), wraplength=420).pack(anchor="w", pady=(0, 4))
    ttk.Entry(frame, textvariable=compat_url, width=56).pack(fill="x", pady=(2, 4))
    ttk.Label(frame, text=t("openai_compat_key_label")).pack(anchor="w")
    ttk.Entry(frame, textvariable=compat_key, width=56, show="*").pack(fill="x", pady=(2, 4))
    oc_row = ttk.Frame(frame)
    oc_row.pack(fill="x")
    ttk.Label(oc_row, text=t("openai_compat_model_label")).pack(side="left")
    ttk.Entry(oc_row, textvariable=compat_model, width=28).pack(
        side="left", fill="x", expand=True, padx=(8, 0)
    )

    section("ai_budget_section")
    ttk.Label(frame, text=t("ai_month_budget_label"), wraplength=420).pack(anchor="w")
    ttk.Entry(frame, textvariable=budget_var, width=16).pack(anchor="w", pady=(2, 4))

    status_lbl = ttk.Label(frame, text="")
    status_lbl.pack(anchor="w", pady=(8, 0))

    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(14, 0))

    def close_without_saving():
        _close_gemini_session()
        dialog.destroy()

    def _creds():
        refresh = ""
        if hasattr(app, "gemini_oauth_refresh_token"):
            refresh = (app.gemini_oauth_refresh_token.get() or "").strip()
        return {
            "cursor_api_key": (cursor_key.get() or "").strip(),
            "gemini_api_key": (gemini_key.get() or "").strip(),
            "gemini_model": (gemini_model.get() or "").strip(),
            "gemini_oauth_refresh_token": refresh,
            "google_oauth_client_id": (google_client.get() or "").strip(),
            "google_oauth_client_secret": (google_secret.get() or "").strip(),
            "google_cloud_project_id": (google_project.get() or "").strip(),
            "anthropic_api_key": (anthropic_key.get() or "").strip(),
            "claude_model": (claude_model.get() or "").strip(),
            "azure_openai_endpoint": (azure_endpoint.get() or "").strip(),
            "azure_openai_api_key": (azure_key.get() or "").strip(),
            "azure_openai_deployment": (azure_deployment.get() or "").strip(),
            "azure_openai_api_version": (azure_version.get() or "").strip(),
            "ollama_base_url": (ollama_url.get() or "").strip(),
            "ollama_model": (ollama_model.get() or "").strip(),
            "openai_compatible_base_url": (compat_url.get() or "").strip(),
            "openai_compatible_api_key": (compat_key.get() or "").strip(),
            "openai_compatible_model": (compat_model.get() or "").strip(),
        }

    def test_conn():
        from whisperfast.postprocess.connection_test import test_provider
        from whisperfast.postprocess.providers import normalize_provider_id

        pid = normalize_provider_id(app.ai_provider.get())
        status_lbl.config(text=t("ai_test_running"))

        def worker():
            ok, msg = test_provider(pid, _creds())

            def done():
                status_lbl.config(text=(t("ai_test_ok") + ": " + msg) if ok else msg)

            app.root.after(0, done)

        import threading

        threading.Thread(target=worker, daemon=True).start()

    def save_keys():
        app.cursor_api_key.set((cursor_key.get() or "").strip())
        app.gemini_api_key.set((gemini_key.get() or "").strip())
        app.gemini_model.set((gemini_model.get() or "").strip() or "gemini-2.0-flash")
        if hasattr(app, "google_oauth_client_id"):
            app.google_oauth_client_id.set(google_client.get().strip())
        if hasattr(app, "google_oauth_client_secret"):
            app.google_oauth_client_secret.set(google_secret.get())
        if hasattr(app, "google_cloud_project_id"):
            app.google_cloud_project_id.set(google_project.get().strip())
        if hasattr(app, "gemini_oauth_email"):
            app.gemini_oauth_email.set(gemini_email.get().strip())
        app.anthropic_api_key.set((anthropic_key.get() or "").strip())
        app.claude_model.set((claude_model.get() or "").strip() or "claude-sonnet-4-5")
        app.azure_openai_endpoint.set((azure_endpoint.get() or "").strip().rstrip("/"))
        app.azure_openai_api_key.set((azure_key.get() or "").strip())
        app.azure_openai_deployment.set((azure_deployment.get() or "").strip())
        app.azure_openai_api_version.set(
            (azure_version.get() or "").strip() or "2024-08-01-preview"
        )
        if hasattr(app, "ollama_base_url"):
            app.ollama_base_url.set(
                (ollama_url.get() or "").strip() or "http://127.0.0.1:11434"
            )
            app.ollama_model.set((ollama_model.get() or "").strip() or "llama3.2")
            app.openai_compatible_base_url.set((compat_url.get() or "").strip())
            app.openai_compatible_api_key.set((compat_key.get() or "").strip())
            app.openai_compatible_model.set((compat_model.get() or "").strip())
            try:
                app.ai_month_budget.set(float((budget_var.get() or "0").replace(",", ".")))
            except (TypeError, ValueError, tk.TclError):
                app.ai_month_budget.set(0.0)
        app._persist_settings()
        _close_gemini_session()
        dialog.destroy()

    ttk.Button(buttons, text=t("cancel_btn"), command=close_without_saving).pack(
        side="right", padx=(5, 0)
    )
    ttk.Button(buttons, text=t("save"), command=save_keys).pack(side="right")
    ttk.Button(buttons, text=t("ai_test_connection"), command=test_conn).pack(side="left")

    dialog.protocol("WM_DELETE_WINDOW", close_without_saving)
    dialog.bind("<Escape>", lambda event: close_without_saving())
    center_toplevel(app, dialog)


def show_ai_prompts_dialog(
    app,
    file_name,
    prompts,
    on_result,
    provider_id=None,
    cascade_offset=None,
    default_nums=None,
):
    """Вікно вибору промптів і AI-провайдера (можна відкрити кілька одночасно).

    on_result(selected_or_none, provider_id):
      - selected None — скасовано
      - list of prompts + provider_id — запуск
    cascade_offset: (dx, dy) від центру батьківського вікна — щоб не накривали одне одне.
    default_nums: номери промптів, позначені за замовчуванням.
    """
    from whisperfast.postprocess.cursor_postprocess import default_checked_prompt_nums
    from whisperfast.postprocess.prompt_library import load_prompt_specs
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

    title_lbl = ttk.Label(
        frame,
        text=t("cursor_prompts_for_file", name=file_name),
        wraplength=460,
        font=("Segoe UI", 10, "bold"),
    )
    title_lbl.pack(anchor="w", pady=(0, 4))
    hint_lbl = ttk.Label(frame, text=t("cursor_prompts_hint"), wraplength=460)
    hint_lbl.pack(anchor="w", pady=(0, 8))

    provider_lbl = ttk.Label(
        frame, text=t("ai_provider_label"), font=("Segoe UI", 9, "bold")
    )
    provider_lbl.pack(anchor="w", pady=(0, 4))
    prov_row = ttk.Frame(frame)
    prov_row.pack(fill="x", pady=(0, 10))
    provider_radios = []
    for pid, label_key in provider_choices():
        rb = ttk.Radiobutton(
            prov_row,
            text=t(label_key),
            variable=provider_var,
            value=pid,
        )
        rb.pack(side="left", padx=(0, 12))
        provider_radios.append((rb, label_key))

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
    prompt_name_labels = []
    dialog._wf_tips = []
    hint_by_num = {s.num: s.hint_for(get_language()) for s in load_prompt_specs()}
    checked_nums = default_checked_prompt_nums(prompts, default_nums)
    for num, name, _text in prompts:
        var = tk.BooleanVar(value=(num in checked_nums))
        check_vars.append(var)
        label = name or f"#{num}"
        row = ttk.Frame(rows_frame)
        row.pack(fill="x", pady=2)
        cb = ttk.Checkbutton(row, variable=var)
        cb.pack(side="left")
        name_lbl = ttk.Label(
            row,
            text=t("cursor_prompt_row", num=num, name=label),
            cursor="hand2",
        )
        name_lbl.pack(side="left", fill="x", expand=True, padx=(4, 0))
        prompt_name_labels.append((name_lbl, num, label))
        prompt_hint = hint_by_num.get(num) or t("tooltip_ai_prompt_checkbox")
        dialog._wf_tips.append(Tooltip(cb, prompt_hint, is_key=False))
        dialog._wf_tips.append(Tooltip(name_lbl, prompt_hint, is_key=False))

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

    all_btn = ttk.Button(buttons, text=t("cursor_prompts_all"), command=on_all)
    all_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))
    run_btn = ttk.Button(buttons, text=t("cursor_prompts_run"), command=on_run_selected)
    run_btn.grid(row=0, column=1, sticky="ew")
    dialog._wf_tips.append(Tooltip(all_btn, "tooltip_cursor_prompts_all", is_key=True))
    dialog._wf_tips.append(Tooltip(run_btn, "tooltip_cursor_prompts_run", is_key=True))
    for rb, _label_key in provider_radios:
        dialog._wf_tips.append(Tooltip(rb, "tooltip_ai_provider_choice", is_key=True))

    def apply_language():
        dialog.title(t("cursor_prompts_title"))
        title_lbl.config(text=t("cursor_prompts_for_file", name=file_name))
        hint_lbl.config(text=t("cursor_prompts_hint"))
        provider_lbl.config(text=t("ai_provider_label"))
        for rb, label_key in provider_radios:
            rb.config(text=t(label_key))
        for name_lbl, num, label in prompt_name_labels:
            name_lbl.config(text=t("cursor_prompt_row", num=num, name=label))
        all_btn.config(text=t("cursor_prompts_all"))
        run_btn.config(text=t("cursor_prompts_run"))

    track_i18n_window(app, dialog, apply_language)

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


def show_ai_prompts_overview_dialog(app):
    """Огляд промптів: галочка = увімкнено за замовчуванням; редагування — у рядку."""
    from whisperfast.postprocess.cursor_postprocess import (
        default_checked_prompt_nums,
        ensure_redactor_file,
    )
    from whisperfast.postprocess.prompt_library import load_prompt_specs, open_prompt_file

    ensure_redactor_file()

    dialog = tk.Toplevel(app.root)
    dialog.title(t("ai_prompts_overview_title"))
    dialog.transient(app.root)
    dialog.minsize(480, 360)
    dialog.geometry("560x460")

    frame = ttk.Frame(dialog, padding=15)
    frame.pack(fill="both", expand=True)

    hint_lbl = ttk.Label(frame, text=t("ai_prompts_overview_hint"), wraplength=520)
    hint_lbl.pack(anchor="w", pady=(0, 8))

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

    state = {
        "check_vars": [],
        "name_labels": [],
        "edit_buttons": [],
        "empty_lbl": None,
        "tips": [],
    }

    def _save_defaults():
        nums = [num for num, var in state["check_vars"] if var.get()]
        app.ai_default_prompt_nums = nums
        persist = getattr(app, "_persist_settings", None)
        if callable(persist):
            persist()

    def rebuild_rows():
        for child in rows_frame.winfo_children():
            child.destroy()
        state["check_vars"] = []
        state["name_labels"] = []
        state["edit_buttons"] = []
        state["empty_lbl"] = None
        state["tips"] = []

        specs = load_prompt_specs()
        if not specs:
            empty_lbl = ttk.Label(
                rows_frame,
                text=t("ai_prompts_overview_empty"),
                wraplength=420,
            )
            empty_lbl.pack(anchor="w", pady=4)
            state["empty_lbl"] = empty_lbl
            return

        stored = getattr(app, "ai_default_prompt_nums", None)
        tuples = [s.as_tuple() for s in specs]
        checked_nums = default_checked_prompt_nums(tuples, stored)
        log_func = getattr(app, "log", None)
        for spec in specs:
            var = tk.BooleanVar(value=(spec.num in checked_nums))
            label = spec.name or f"#{spec.num}"
            row = ttk.Frame(rows_frame)
            row.pack(fill="x", pady=2)
            cb = ttk.Checkbutton(row, variable=var, command=_save_defaults)
            cb.pack(side="left")
            name_lbl = ttk.Label(
                row,
                text=t("cursor_prompt_row", num=spec.num, name=label),
                cursor="hand2",
            )
            name_lbl.pack(side="left", fill="x", expand=True, padx=(4, 8))
            edit_btn = ttk.Button(
                row,
                text=t("ai_prompt_edit"),
                width=10,
                command=lambda p=spec.path: open_prompt_file(p, log_func=log_func),
            )
            edit_btn.pack(side="right")

            def _click(_event=None, v=var):
                v.set(not v.get())
                _save_defaults()

            name_lbl.bind("<Button-1>", _click)
            prompt_hint = spec.hint_for(get_language())
            state["tips"].append(Tooltip(cb, "tooltip_ai_prompt_checkbox", is_key=True))
            if prompt_hint:
                state["tips"].append(Tooltip(name_lbl, prompt_hint, is_key=False))
            else:
                state["tips"].append(Tooltip(name_lbl, "tooltip_ai_prompt_checkbox", is_key=True))
            state["tips"].append(Tooltip(edit_btn, "tooltip_ai_prompt_edit", is_key=True))
            state["check_vars"].append((spec.num, var))
            state["name_labels"].append((name_lbl, spec.num, label))
            state["edit_buttons"].append(edit_btn)

        dialog._wf_tips = state["tips"]

    def _on_mousewheel(event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    canvas.bind("<MouseWheel>", _on_mousewheel)
    rows_frame.bind("<MouseWheel>", _on_mousewheel)

    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(12, 0))

    def on_rules():
        show_prompt_rules_dialog(app)

    rules_btn = ttk.Button(buttons, text=t("ai_prompt_rules_button"), command=on_rules)
    rules_btn.pack(side="right")
    dialog._wf_rules_tip = Tooltip(rules_btn, "tooltip_ai_prompt_rules", is_key=True)

    def apply_language():
        dialog.title(t("ai_prompts_overview_title"))
        hint_lbl.config(text=t("ai_prompts_overview_hint"))
        rules_btn.config(text=t("ai_prompt_rules_button"))
        rebuild_rows()

    rebuild_rows()
    track_i18n_window(app, dialog, apply_language)

    def on_close():
        try:
            dialog.destroy()
        except tk.TclError:
            pass

    dialog.protocol("WM_DELETE_WINDOW", on_close)
    dialog.bind("<Escape>", lambda e: on_close())
    dialog._wf_refresh_prompts = rebuild_rows
    center_toplevel(app, dialog)
    dialog.focus_set()
    return dialog


def show_startup_app_update_dialog(app, current, latest, remote_date=None, on_result=None):
    """Окремий діалог при старті: Оновити / Пізніше / Пропустити цю версію.

    on_result(choice): 'update' | 'later' | 'skip'
    """
    dialog = tk.Toplevel(app.root)
    dialog.title(t("startup_update_title"))
    dialog.transient(app.root)
    dialog.resizable(False, False)
    dialog.grab_set()

    settled = {"done": False}

    frame = ttk.Frame(dialog, padding=16)
    frame.pack(fill="both", expand=True)

    msg_lbl = ttk.Label(frame, justify="left", wraplength=440)
    msg_lbl.pack(anchor="w")

    bf = ttk.Frame(frame)
    bf.pack(fill="x", pady=(16, 0))

    def finish(choice):
        if settled["done"]:
            return
        settled["done"] = True
        try:
            dialog.grab_release()
        except tk.TclError:
            pass
        try:
            dialog.destroy()
        except tk.TclError:
            pass
        if on_result:
            on_result(choice)

    btn_now = ttk.Button(
        bf, text=t("startup_update_now"), command=lambda: finish("update"), width=16
    )
    btn_now.pack(side="left", padx=(0, 6))
    btn_later = ttk.Button(
        bf, text=t("startup_update_later"), command=lambda: finish("later"), width=14
    )
    btn_later.pack(side="left", padx=(0, 6))
    btn_skip = ttk.Button(
        bf, text=t("startup_update_skip"), command=lambda: finish("skip"), width=20
    )
    btn_skip.pack(side="left")

    def apply_language():
        dialog.title(t("startup_update_title"))
        date_part = ""
        if remote_date:
            date_part = t("startup_update_date_part", date=remote_date)
        msg_lbl.config(
            text=t(
                "startup_update_msg",
                current=current or "?",
                latest=latest or "?",
                date_part=date_part,
            )
        )
        btn_now.config(text=t("startup_update_now"))
        btn_later.config(text=t("startup_update_later"))
        btn_skip.config(text=t("startup_update_skip"))

    apply_language()
    track_i18n_window(app, dialog, apply_language)

    dialog.protocol("WM_DELETE_WINDOW", lambda: finish("later"))
    dialog.bind("<Escape>", lambda e: finish("later"))
    center_toplevel(app, dialog)
    dialog.focus_set()
    return dialog


def show_prompt_rules_dialog(app):
    """Edit auto-run prompt rules (always / watch_dir / filename)."""
    from whisperfast.postprocess.prompt_rules import normalize_prompt_rules

    dialog = tk.Toplevel(app.root)
    dialog.title(t("ai_prompt_rules_title"))
    dialog.transient(app.root)
    dialog.minsize(520, 280)
    dialog.geometry("580x340")

    frame = ttk.Frame(dialog, padding=12)
    frame.pack(fill="both", expand=True)
    hint = ttk.Label(frame, text=t("ai_prompt_rules_hint"), wraplength=540)
    hint.pack(anchor="w", pady=(0, 8))
    text = tk.Text(frame, height=10, wrap="word", font=("Consolas", 10))
    text.pack(fill="both", expand=True)
    rules = normalize_prompt_rules(getattr(app, "ai_prompt_rules", None) or [])
    import json as _json

    text.insert("1.0", _json.dumps(rules, ensure_ascii=False, indent=2))
    status = ttk.Label(frame, text="")
    status.pack(anchor="w", pady=(6, 0))
    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(8, 0))

    def save():
        raw = text.get("1.0", "end").strip() or "[]"
        try:
            data = _json.loads(raw)
        except _json.JSONDecodeError as e:
            status.config(text=str(e))
            return
        app.ai_prompt_rules = normalize_prompt_rules(data)
        persist = getattr(app, "_persist_settings", None)
        if callable(persist):
            persist()
        dialog.destroy()

    ttk.Button(buttons, text=t("cancel_btn"), command=dialog.destroy).pack(side="right")
    ttk.Button(buttons, text=t("save"), command=save).pack(side="right", padx=(0, 6))
    center_toplevel(app, dialog)
    return dialog

