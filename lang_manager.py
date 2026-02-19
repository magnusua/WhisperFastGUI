"""
Модуль для управления переводами интерфейса.
Загружает переводы из lang.json и предоставляет функцию для получения переведенных строк.
"""
import json
import os

try:
    from config import BASE_DIR
except ImportError:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Текущий язык по умолчанию
_current_language = "EN"

# Словарь переводов
_translations = {}

# Файл настроек (путь относительно BASE_DIR приложения)
SETTINGS_FILE = "settings.json"


def _settings_path():
    """Единый путь к файлу настроек."""
    return os.path.join(BASE_DIR, SETTINGS_FILE)


def load_translations():
    """Загружает переводы из файла lang.json"""
    global _translations
    lang_file = os.path.join(os.path.dirname(__file__), "lang.json")
    
    try:
        with open(lang_file, "r", encoding="utf-8") as f:
            _translations = json.load(f)
    except FileNotFoundError:
        print(f"Warning: Language file {lang_file} not found. Using empty translations.")
        _translations = {}
    except json.JSONDecodeError as e:
        print(f"Error: Failed to parse language file: {e}")
        _translations = {}

def load_settings():
    """Загружает настройки из файла settings.json"""
    path = _settings_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                settings = json.load(f)
                return settings.get("language", "EN")
        except (json.JSONDecodeError, KeyError):
            return "EN"
    return "EN"


def load_app_settings():
    """Загружает всі налаштування з settings.json (мова, слідкування, каталоги, пристрій тощо).
    При першому запуску створює settings.json з налаштуваннями за замовчуванням."""
    path = _settings_path()
    defaults = {
        "language": "EN",
        "output_dir": "",
        "watch_dir": "",
        "watch_enabled": False,
        "device_mode": "AUTO",
        "play_sound_on_finish": False,
        "save_audio_mp3": False,
        "tray_mode": "panel",
        "whisper_model": "large-v3-turbo",
    }
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
        for k, v in defaults.items():
            if k not in data:
                data[k] = v
        return data
    except (json.JSONDecodeError, TypeError):
        return defaults.copy()


def save_settings(language):
    """Сохраняет настройки в файл settings.json"""
    path = _settings_path()
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
    """Зберігає налаштування в settings.json (злиття з існуючим вмістом)."""
    path = _settings_path()
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

def set_language(lang_code):
    """Устанавливает текущий язык интерфейса и сохраняет его"""
    global _current_language
    if lang_code in ("EN", "UK", "RU"):
        _current_language = lang_code
        save_settings(lang_code)
    else:
        print(f"Warning: Unknown language code {lang_code}, keeping current language {_current_language}")

def get_language():
    """Возвращает текущий код языка"""
    return _current_language

def t(key, **kwargs):
    """
    Получает переведенную строку по ключу.
    
    Args:
        key: Ключ перевода
        **kwargs: Параметры для форматирования строки (например, {name}, {count})
    
    Returns:
        Переведенная строка или ключ, если перевод не найден
    """
    if not _translations:
        load_translations()
    
    lang = _current_language
    if lang not in ("EN", "UK", "RU"):
        lang = "EN"  # Fallback на английский
    
    # Новая структура: _translations[key][lang]
    if key in _translations and lang in _translations[key]:
        text = _translations[key][lang]
        # Форматирование строки с параметрами
        if kwargs:
            try:
                return text.format(**kwargs)
            except KeyError:
                # Если не все параметры переданы, возвращаем как есть
                return text
        return text
    
    # Если перевод не найден, возвращаем ключ
    return key

# Загружаем переводы при импорте модуля
load_translations()

# Загружаем сохраненный язык при импорте
_current_language = load_settings()
