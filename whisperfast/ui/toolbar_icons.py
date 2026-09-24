"""Toolbar pictograms (Material Design Icons, Apache 2.0).

PNGs live in resources/icons/. Labels stay in tooltips so language changes
do not put words back on the icon-only buttons.
"""
from __future__ import annotations

import os
import tkinter as tk

from whisperfast.config import RESOURCES_DIR

ICONS_DIR = os.path.join(RESOURCES_DIR, "icons")

# Google Material Design Icons 24dp (Apache 2.0):
# https://github.com/google/material-design-icons
FILE_MAP = {
    "add_files": "note-add.png",
    "add_directory": "create-new-folder.png",
    "clear_queue": "delete-sweep.png",
    "archive": "search.png",
    "capture_start": "mic.png",
    "capture_stop": "stop.png",
    "capture_pause": "pause.png",
    "capture_resume": "play-arrow.png",
    "capture_clip": "content-cut.png",
    "capture_settings": "settings.png",
    "notify_on": "notifications.png",
    "notify_off": "notifications-off.png",
    "output_folder": "folder.png",
    "mp3_settings": "audiotrack.png",
    "mp3_on": "audiotrack.png",
    "mp3_off": "audiotrack.png",
    "watch_on": "visibility.png",
    "watch_off": "visibility-off.png",
    "lang_auto": "lang-auto.png",
    "lang_ru": "lang-ru.png",
    "lang_uk": "lang-uk.png",
    "lang_en": "lang-en.png",
    "prompts": "forum.png",
    "prompts_on": "forum.png",
    "prompts_off": "forum.png",
    "api_keys": "vpn-key.png",
    "telegram": "telegram.png",
    "telegram_on": "telegram.png",
    "telegram_off": "telegram.png",
    "export_md_docx": "description.png",
    "docx_on": "description.png",
    "docx_off": "description.png",
    "clear_log": "delete.png",
    "system": "computer.png",
    "updates": "refresh.png",
    "dependencies": "extension.png",
    "autostart_off": "power-settings-new.png",
    "autostart_on": "power-settings-new.png",
    "cancel": "cancel.png",
    "start_transcription": "play-arrow.png",
    "model": "model.png",
    "tray_mode": "tray.png",
    "device_auto": "device-auto.png",
    "device_gpu": "device-gpu.png",
    "device_cpu": "device-cpu.png",
}

# Recolor a black Material glyph (RGB kept, alpha from the PNG).
TINT_MAP = {
    "autostart_on": (22, 163, 74),
    "mp3_on": (22, 163, 74),
    "prompts_on": (22, 163, 74),
    "telegram_on": (22, 163, 74),
    "docx_on": (22, 163, 74),
}

# Segoe MDL2 Assets (Windows) if a PNG is missing.
# https://learn.microsoft.com/windows/apps/design/style/segoe-ui-symbol-font
UNICODE_FALLBACK = {
    "add_files": "\uE8E5",
    "add_directory": "\uE8F4",
    "clear_queue": "\uE74D",
    "archive": "\uE721",
    "capture_start": "\uE720",
    "capture_stop": "\uE71A",
    "capture_pause": "\uE769",
    "capture_resume": "\uE768",
    "capture_clip": "\uE8C6",
    "capture_settings": "\uE713",
    "notify_on": "\uEA8F",
    "notify_off": "\uE7ED",
    "output_folder": "\uE8B7",
    "mp3_settings": "\uE8D6",
    "mp3_on": "\uE8D6",
    "mp3_off": "\uE8D6",
    "watch_on": "\uE890",
    "watch_off": "\uE7B3",
    "lang_auto": "A",
    "lang_ru": "RU",
    "lang_uk": "UK",
    "lang_en": "EN",
    "prompts": "\uE8BD",
    "prompts_on": "\uE8BD",
    "prompts_off": "\uE8BD",
    "api_keys": "\uE192",
    "telegram": "\uE8F2",
    "telegram_on": "\uE8F2",
    "telegram_off": "\uE8F2",
    "export_md_docx": "\uE8A5",
    "docx_on": "\uE8A5",
    "docx_off": "\uE8A5",
    "clear_log": "\uE74D",
    "system": "\uE770",
    "updates": "\uE72C",
    "dependencies": "\uE90F",
    "autostart_off": "\uE7E8",
    "autostart_on": "\uE7E8",
    "cancel": "\uE711",
    "start_transcription": "\uE768",
    "model": "\uE964",
    "tray_mode": "\uE138",
    "device_auto": "\uE7F4",
    "device_gpu": "\uE7F8",
    "device_cpu": "\uE950",
}

_ICON_SIZE = 20


def _tint_rgba(img, rgb: tuple[int, int, int]):
    from PIL import Image

    alpha = img.getchannel("A")
    color = Image.new("RGB", img.size, rgb)
    out = Image.new("RGBA", img.size)
    out.paste(color, mask=alpha)
    out.putalpha(alpha)
    return out


def icon_path(key: str) -> str:
    fname = FILE_MAP.get(key, "")
    return os.path.join(ICONS_DIR, fname) if fname else ""


def load(master) -> dict:
    """Load PhotoImage objects. Keep the returned dict on the app (GC)."""
    photos: dict = {}
    try:
        from PIL import Image, ImageTk
    except ImportError:
        Image = ImageTk = None  # type: ignore
    for key, fname in FILE_MAP.items():
        path = os.path.join(ICONS_DIR, fname)
        if not os.path.isfile(path):
            continue
        try:
            if Image is not None and ImageTk is not None:
                img = Image.open(path).convert("RGBA")
                if img.size != (_ICON_SIZE, _ICON_SIZE):
                    img = img.resize((_ICON_SIZE, _ICON_SIZE), Image.Resampling.LANCZOS)
                tint = TINT_MAP.get(key)
                if tint:
                    img = _tint_rgba(img, tint)
                photos[key] = ImageTk.PhotoImage(img, master=master)
            else:
                photos[key] = tk.PhotoImage(file=path, master=master)
        except Exception:
            continue
    return photos


def set_icon(btn, photos: dict | None, key: str) -> None:
    if btn is None:
        return
    photos = photos or {}
    photo = photos.get(key)
    try:
        if photo is not None:
            btn.configure(image=photo, text="")
        else:
            glyph = UNICODE_FALLBACK.get(key, "")
            kwargs = {"text": glyph, "image": ""}
            if os.name == "nt" and glyph:
                kwargs["font"] = ("Segoe MDL2 Assets", 11)
            btn.configure(**kwargs)
        btn._toolbar_icon_key = key
    except tk.TclError:
        pass


def apply_static(app) -> None:
    photos = getattr(app, "_toolbar_photos", None) or {}
    for attr, key in (
        ("add_files_btn", "add_files"),
        ("add_directory_btn", "add_directory"),
        ("clear_queue_btn", "clear_queue"),
        ("archive_btn", "archive"),
        ("capture_clip_btn", "capture_clip"),
        ("capture_settings_btn", "capture_settings"),
        ("output_folder_btn", "output_folder"),
        ("cursor_api_key_btn", "api_keys"),
        ("clear_log_btn", "clear_log"),
        ("dependencies_btn", "dependencies"),
        ("cancel_btn", "cancel"),
        ("start_btn", "start_transcription"),
        ("model_btn", "model"),
        ("tray_mode_btn", "tray_mode"),
    ):
        set_icon(getattr(app, attr, None), photos, key)
    apply_notify_state(app)
    apply_watch_state(app)
    apply_recog_state(app)
    apply_feature_states(app)
    apply_autostart_state(app)
    apply_device_state(app)


def apply_notify_state(app) -> None:
    photos = getattr(app, "_toolbar_photos", None) or {}
    on = False
    var = getattr(app, "play_sound_on_finish", None)
    if var is not None:
        try:
            on = bool(var.get())
        except tk.TclError:
            pass
    set_icon(getattr(app, "play_sound_btn", None), photos, "notify_on" if on else "notify_off")


def apply_watch_state(app) -> None:
    photos = getattr(app, "_toolbar_photos", None) or {}
    on = False
    var = getattr(app, "watch_enabled", None)
    if var is not None:
        try:
            on = bool(var.get())
        except tk.TclError:
            pass
    set_icon(getattr(app, "watch_dirs_btn", None), photos, "watch_on" if on else "watch_off")


def apply_recog_state(app) -> None:
    photos = getattr(app, "_toolbar_photos", None) or {}
    label = "AUTO"
    reader = getattr(app, "_recog_lang_label", None)
    var = getattr(app, "lang_mode", None)
    if callable(reader) and var is not None:
        try:
            label = str(reader(var.get()) or "AUTO").upper()
        except tk.TclError:
            label = "AUTO"
    key = {"RU": "lang_ru", "UK": "lang_uk", "EN": "lang_en"}.get(label, "lang_auto")
    set_icon(getattr(app, "recog_lang_btn", None), photos, key)


def _flag(app, name: str) -> bool:
    var = getattr(app, name, None)
    if var is None:
        return False
    try:
        return bool(var.get())
    except tk.TclError:
        return False


def apply_feature_states(app) -> None:
    photos = getattr(app, "_toolbar_photos", None) or {}
    set_icon(getattr(app, "mp3_settings_btn", None), photos, "mp3_on" if _flag(app, "save_audio_mp3") else "mp3_off")
    set_icon(getattr(app, "edit_redactor_btn", None), photos, "prompts_on" if _flag(app, "send_txt_to_ai") else "prompts_off")
    set_icon(getattr(app, "telegram_btn", None), photos, "telegram_on" if _flag(app, "telegram_listener_on") else "telegram_off")
    set_icon(getattr(app, "export_md_docx_btn", None), photos, "docx_on" if _flag(app, "export_md_to_docx") else "docx_off")


def apply_device_state(app) -> None:
    photos = getattr(app, "_toolbar_photos", None) or {}
    mode = "AUTO"
    var = getattr(app, "device_mode", None)
    if var is not None:
        try:
            mode = str(var.get() or "AUTO").upper()
        except tk.TclError:
            pass
    key = {"GPU": "device_gpu", "CPU": "device_cpu"}.get(mode, "device_auto")
    set_icon(getattr(app, "device_btn", None), photos, key)


def apply_autostart_state(app) -> None:
    photos = getattr(app, "_toolbar_photos", None) or {}
    on = False
    var = getattr(app, "autostart_enabled", None)
    if var is not None:
        try:
            on = bool(var.get())
        except tk.TclError:
            pass
    set_icon(getattr(app, "autostart_btn", None), photos, "autostart_on" if on else "autostart_off")


def apply_capture_state(app, running: bool, paused: bool) -> None:
    photos = getattr(app, "_toolbar_photos", None) or {}
    set_icon(
        getattr(app, "capture_btn", None),
        photos,
        "capture_stop" if running else "capture_start",
    )
    set_icon(
        getattr(app, "capture_pause_btn", None),
        photos,
        "capture_resume" if paused else "capture_pause",
    )
    set_icon(getattr(app, "capture_settings_btn", None), photos, "capture_settings")
    set_icon(getattr(app, "capture_clip_btn", None), photos, "capture_clip")
