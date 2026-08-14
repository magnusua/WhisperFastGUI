"""Базовий контракт AI-провайдера + спільна реалізація ланцюжка промптів.

claude.py / gemini.py / copilot.py раніше дублювали (майже дослівно, ~90 рядків
кожен) цикл "чи є ключ -> або ланцюжок викликів API, або буфер обміну + браузер".
run_provider_chain()/run_browser_fallback() тут — той самий цикл один раз,
параметризований функцією виклику конкретного API (call_llm) та ідентифікатором
провайдера (self.id), з якого виводяться імена i18n-ключів за конвенцією, що вже
використовується в lang.json: "{id}_processing_prompt", "{id}_prompt_error",
"{id}_file_created", "{id}_chain_done", "{id}_chain_no_output",
"{id}_no_api_key_fallback", "{id}_browser_opened", "{id}_browser_prompt_copied",
"{id}_browser_manual_hint". Див. docs/CODE-REVIEW.md, розділ 4, і
docs/POSTPROCESSING-PROVIDERS.uk.md.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

from whisperfast.postprocess.common import (
    build_clipboard_fallback_text,
    build_transform_user_message,
    open_browser_fallback,
    read_text_file,
    write_text_file,
)
from whisperfast.postprocess.cursor_postprocess import edited_output_path

LogFunc = Callable[..., None]
PromptTuple = Tuple[int, str, str]  # (num, name, text)


class AIProvider(Protocol):
    id: str
    label_key: str

    def has_api_credentials(self, credentials: Dict[str, Any]) -> bool:
        """Чи достатньо даних для API-режиму (не browser fallback)."""
        ...

    def process(
        self,
        txt_path: str,
        prompts: List[PromptTuple],
        credentials: Dict[str, Any],
        log_func: Optional[LogFunc] = None,
        on_file_created: Optional[Callable[[str], None]] = None,
        resolve_output_path: Optional[Callable[[str], str]] = None,
    ) -> List[str]:
        """Виконує ланцюжок промптів. Повертає створені шляхи."""
        ...


def _log(log_func: Optional[LogFunc], key: str, **kwargs: Any) -> None:
    if not log_func:
        return
    try:
        from whisperfast.i18n import t

        log_func(t(key, **kwargs))
    except ImportError:
        pass


def run_browser_fallback(
    provider_id: str,
    browser_url: str,
    txt_path: str,
    prompts: List[PromptTuple],
    log_func: Optional[LogFunc] = None,
) -> List[str]:
    """Гілка «немає ключа API»: буфер обміну з першим промптом + відкриття браузера."""
    _log(log_func, f"{provider_id}_no_api_key_fallback")
    open_browser_fallback(
        browser_url,
        build_clipboard_fallback_text(prompts[0][2], txt_path),
        log_func,
        opened_key=f"{provider_id}_browser_opened",
        copied_key=f"{provider_id}_browser_prompt_copied",
        manual_key=f"{provider_id}_browser_manual_hint",
        name=os.path.basename(txt_path),
    )
    return []


def run_provider_chain(
    provider_id: str,
    call_llm: Callable[[str], str],
    txt_path: str,
    prompts: List[PromptTuple],
    log_func: Optional[LogFunc] = None,
    on_file_created: Optional[Callable[[str], None]] = None,
    resolve_output_path: Optional[Callable[[str], str]] = None,
) -> List[str]:
    """
    Гілка «є ключ API»: послідовно проганяє позначені промпти через call_llm,
    записуючи результат кожного кроку у файл (вихід попереднього кроку стає входом
    наступного). call_llm(user_message) -> text вже має бути прив'язана до
    конкретних ключа/моделі/endpoint провайдера — сама ця функція про це не знає.
    """
    created: List[str] = []
    current_input = txt_path
    for num, name, text in prompts:
        out_path = edited_output_path(txt_path, num, name)
        if resolve_output_path:
            out_path = resolve_output_path(out_path)
            if not out_path:
                _log(
                    log_func,
                    "file_exists_skipped",
                    name=os.path.basename(edited_output_path(txt_path, num, name)),
                )
                break
        label = name or f"#{num}"
        _log(log_func, f"{provider_id}_processing_prompt", num=num, name=label)
        try:
            content = read_text_file(current_input)
            user_msg = build_transform_user_message(text, content)
            result = call_llm(user_msg)
            write_text_file(out_path, result)
        except Exception as e:
            _log(log_func, f"{provider_id}_prompt_error", num=num, error=str(e))
            break
        if not os.path.isfile(out_path):
            _log(log_func, "cursor_output_missing", path=out_path)
            break
        created.append(out_path)
        if on_file_created:
            on_file_created(out_path)
        _log(log_func, f"{provider_id}_file_created", num=num, name=os.path.basename(out_path))
        current_input = out_path

    base = os.path.basename(txt_path)
    if created:
        _log(log_func, f"{provider_id}_chain_done", count=len(created), name=base)
    else:
        _log(log_func, f"{provider_id}_chain_no_output", name=base)
    return created
