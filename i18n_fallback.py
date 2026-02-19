"""
Fallback для i18n, когда lang_manager недоступен (например, при раннем импорте).
Используется во всех модулях через: try lang_manager except ImportError: i18n_fallback.
"""


def t(key, **kwargs):
    """Возвращает ключ как строку (перевод недоступен); при наличии kwargs пробует key.format(**kwargs)."""
    if kwargs:
        try:
            return key.format(**kwargs)
        except (KeyError, ValueError):
            pass
    return key


def set_language(lang):
    """Заглушка при недоступном lang_manager."""
    pass


def get_language():
    """Возвращает язык по умолчанию."""
    return "EN"
