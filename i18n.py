"""
Единая точка импорта переводов. Все модули импортируют t, set_language, get_language отсюда.
"""
try:
    from lang_manager import t, set_language, get_language
except ImportError:
    from i18n_fallback import t, set_language, get_language

__all__ = ["t", "set_language", "get_language"]
