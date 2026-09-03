"""Load and save application settings (settings.json)."""
import json
import os

from whisperfast.config import BASE_DIR, DEFAULT_MODEL

SETTINGS_FILE = "settings.json"


def _restrict_settings_file_permissions(path):
    """
    Ограничивает доступ к settings.json владельцем файла (chmod 0600).

    В файле хранятся ключи API AI-провайдеров в открытом виде (см.
    docs/CONFIGURATION.uk.md / docs/CODE-REVIEW.md, раздел 3) — по умолчанию файл
    создаётся с правами процесса (umask), которые на многопользовательских
    системах могут разрешать чтение другим пользователям. На Windows
    os.chmod не задаёт POSIX-права доступа — там для ограничения доступа
    нужны ACL, а не биты chmod, поэтому там ничего не делаем.
    """
    if os.name != "posix":
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


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
    "send_txt_to_ai": False,
    "send_txt_to_cursor": False,  # legacy alias → send_txt_to_ai
    "export_md_to_docx": False,
    "ai_provider": "cursor",
    "cursor_api_key": "",
    "gemini_api_key": "",
    "gemini_model": "gemini-2.0-flash",
    "anthropic_api_key": "",
    "claude_model": "claude-sonnet-4-5",
    "azure_openai_endpoint": "",
    "azure_openai_api_key": "",
    "azure_openai_deployment": "",
    "azure_openai_api_version": "2024-08-01-preview",
    "python_path": "",
    "python_version": "",
    "python_path_chosen": False,
    "python_discovered": [],
    "skip_app_update_version": "",  # не пропонувати цю remote-версію при старті
}


def settings_path():
    """Absolute path to settings.json next to the application root."""
    return os.path.join(BASE_DIR, SETTINGS_FILE)


def default_settings():
    return _DEFAULTS.copy()


def _types_match_default(value, default):
    """True if JSON value is the same kind as the default (bool is not int)."""
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(default, float):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, type(default))


def _sanitize_loaded_settings(data, defaults):
    """
    Merge defaults, coerce wrong types back to defaults, sync legacy AI flag.

    Returns (sanitized_dict, changed) where changed means the file should be rewritten.
    """
    if not isinstance(data, dict):
        return defaults.copy(), True

    if "send_txt_to_ai" not in data and "send_txt_to_cursor" in data:
        data["send_txt_to_ai"] = bool(data.get("send_txt_to_cursor"))

    changed = False
    sanitized = {}
    for key, default in defaults.items():
        if key not in data:
            sanitized[key] = default
            changed = True
            continue
        value = data[key]
        if _types_match_default(value, default):
            sanitized[key] = value
        else:
            sanitized[key] = default
            changed = True

    # Extra keys (unknown / future) are kept only if JSON-serializable primitives
    for key, value in data.items():
        if key not in sanitized:
            sanitized[key] = value

    sanitized["send_txt_to_cursor"] = bool(sanitized.get("send_txt_to_ai", False))
    return sanitized, changed


def load_settings():
    """Load UI language from settings.json."""
    path = settings_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                settings = json.load(f)
            if not isinstance(settings, dict):
                return "EN"
            language = settings.get("language", "EN")
            return language if isinstance(language, str) and language else "EN"
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
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
            _restrict_settings_file_permissions(path)
        except OSError:
            pass
        return defaults.copy()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        sanitized, changed = _sanitize_loaded_settings(data, defaults)
        if changed:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(sanitized, f, ensure_ascii=False, indent=2)
                _restrict_settings_file_permissions(path)
            except OSError:
                pass
        return sanitized
    except (json.JSONDecodeError, TypeError, AttributeError):
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
        _restrict_settings_file_permissions(path)
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
        _restrict_settings_file_permissions(path)
    except OSError as e:
        print(f"Warning: Failed to save app settings: {e}")
