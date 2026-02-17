"""
Fallback для i18n, когда lang_manager недоступен (например, при раннем импорте).
Используется во всех модулях через: try lang_manager except ImportError: i18n_fallback.
"""


def t(key, **kwargs):
    """Возвращает ключ как строку (перевод недоступен)."""
    return key


def set_language(lang):
    """Заглушка при недоступном lang_manager."""
    pass


def get_language():
    """Возвращает язык по умолчанию."""
    return "EN"
