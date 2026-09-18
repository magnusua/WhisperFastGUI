"""Queue-header pictograms (Material Design Icons, Apache 2.0).

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
    "archive": "archive.png",
    "capture_start": "mic.png",
    "capture_stop": "stop.png",
    "capture_pause": "pause.png",
    "capture_resume": "play-arrow.png",
    "capture_clip": "content-cut.png",
    "capture_settings": "settings.png",
}

# Segoe MDL2 Assets (Windows) if a PNG is missing.
# https://learn.microsoft.com/windows/apps/design/style/segoe-ui-symbol-font
UNICODE_FALLBACK = {
    "add_files": "\uE8E5",
    "add_directory": "\uE8F4",
    "clear_queue": "\uE74D",
    "archive": "\uE7B8",
    "capture_start": "\uE720",
    "capture_stop": "\uE71A",
    "capture_pause": "\uE769",
    "capture_resume": "\uE768",
    "capture_clip": "\uE8C6",
    "capture_settings": "\uE713",
}

_ICON_SIZE = 20


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
    ):
        set_icon(getattr(app, attr, None), photos, key)


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
