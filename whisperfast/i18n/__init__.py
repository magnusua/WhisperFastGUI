"""
Единая точка импорта переводов. Все модули импортируют t, set_language, get_language отсюда.
"""
try:
    from whisperfast.i18n.lang_manager import t, set_language, get_language
except ImportError:
    from whisperfast.i18n.fallback import t, set_language, get_language

__all__ = ["t", "set_language", "get_language"]
