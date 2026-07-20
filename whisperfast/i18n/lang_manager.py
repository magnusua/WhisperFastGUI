"""
Модуль для управления переводами интерфейса.
Загружает переводы из lang.json и предоставляет функцию для получения переведенных строк.
"""
import json
import os

from whisperfast.settings import load_settings, save_settings

# Текущий язык по умолчанию
_current_language = "EN"

# Словарь переводов
_translations = {}

_LANG_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lang.json")


def load_translations():
    """Загружает переводы из lang.json рядом с этим модулем."""
    global _translations
    try:
        with open(_LANG_JSON, "r", encoding="utf-8") as f:
            _translations = json.load(f)
    except FileNotFoundError:
        print(f"Warning: Language file {_LANG_JSON} not found. Using empty translations.")
        _translations = {}
    except json.JSONDecodeError as e:
        print(f"Error: Failed to parse language file: {e}")
        _translations = {}


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
        lang = "EN"

    if key in _translations and lang in _translations[key]:
        text = _translations[key][lang]
        if kwargs:
            try:
                return text.format(**kwargs)
            except KeyError:
                return text
        return text

    return key


# Загружаем переводы и сохранённый язык при импорте модуля
load_translations()
_current_language = load_settings()
