"""Load and save application settings (settings.json)."""
import json
import os

from whisperfast.config import BASE_DIR, DEFAULT_MODEL

SETTINGS_FILE = "settings.json"

_DEFAULTS = {
    "language": "EN",
    "output_dir": "",
    "output_mode": "beside",
    "output_named_folder": "{basename}",
    "mp3_output_mode": "inherit",
    "mp3_output_dir": "",
    "watch_dir": "",
    "watch_enabled": False,
    "device_mode": "AUTO",
    "play_sound_on_finish": False,
    "save_audio_mp3": False,
    "tray_mode": "panel",
    "whisper_model": DEFAULT_MODEL,
    "has_nvidia": False,
    "gpu_model": "",
    "send_txt_to_cursor": False,
    "cursor_api_key": "",
    "python_path": "",
    "python_version": "",
}


def settings_path():
    """Absolute path to settings.json next to the application root."""
    return os.path.join(BASE_DIR, SETTINGS_FILE)


def default_settings():
    return _DEFAULTS.copy()


def load_settings():
    """Load UI language from settings.json."""
    path = settings_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                settings = json.load(f)
                return settings.get("language", "EN")
        except (json.JSONDecodeError, KeyError):
            return "EN"
    return "EN"


def load_app_settings():
    """Load all settings from settings.json; create file with defaults on first run."""
    path = settings_path()
    defaults = default_settings()
    if not os.path.exists(path):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(defaults, f, ensure_ascii=False, indent=2)
        except OSError:
            pass
        return defaults.copy()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        missing = False
        for k, v in defaults.items():
            if k not in data:
                data[k] = v
                missing = True
        if missing:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except OSError:
                pass
        return data
    except (json.JSONDecodeError, TypeError):
        return defaults.copy()


def save_settings(language):
    """Save only the UI language (other keys untouched)."""
    path = settings_path()
    try:
        settings = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    settings = json.load(f)
            except json.JSONDecodeError:
                settings = {}
        settings["language"] = language
        with open(path, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"Warning: Failed to save settings: {e}")


def save_app_settings(settings_dict):
    """Merge settings_dict into settings.json and write the file."""
    path = settings_path()
    try:
        settings = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    settings = json.load(f)
            except json.JSONDecodeError:
                settings = {}
        for k, v in settings_dict.items():
            settings[k] = v
        with open(path, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"Warning: Failed to save app settings: {e}")
