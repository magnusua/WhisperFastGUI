"""Оркестратор AI-постпроцесингу: Cursor / Gemini / Copilot."""
from __future__ import annotations

import os
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from whisperfast.postprocess.cursor_postprocess import (
    CURSOR_POSTPROCESS_DELAY_S,
    ensure_redactor_file,
    parse_redactor_prompts,
)
from whisperfast.postprocess.providers import get_provider, normalize_provider_id

AI_POSTPROCESS_DELAY_S = CURSOR_POSTPROCESS_DELAY_S

LogFunc = Callable[..., None]
PromptTuple = Tuple[int, str, str]


def process_txt_with_ai(
    txt_path: str,
    provider_id: str = "cursor",
    credentials: Optional[Dict[str, Any]] = None,
    log_func: Optional[LogFunc] = None,
    on_file_created: Optional[Callable[[str], None]] = None,
    delay_s: float = AI_POSTPROCESS_DELAY_S,
    resolve_output_path: Optional[Callable[[str], str]] = None,
    prompts: Optional[List[PromptTuple]] = None,
) -> None:
    """Затримка → промпти → обраний AI-провайдер (API або browser/Chat fallback)."""
    credentials = credentials or {}
    provider_id = normalize_provider_id(provider_id)

    if delay_s > 0:
        if log_func:
            try:
                from whisperfast.i18n import t
                log_func(t("ai_waiting", seconds=int(delay_s), provider=provider_id))
            except ImportError:
                pass
        threading.Event().wait(delay_s)

    ensure_redactor_file()
    if prompts is None:
        prompts = parse_redactor_prompts()
    if not prompts:
        if log_func:
            try:
                from whisperfast.i18n import t
                log_func(t("cursor_no_prompts"))
            except ImportError:
                pass
        return

    provider = get_provider(provider_id)
    if log_func:
        try:
            from whisperfast.i18n import t
            log_func(
                t(
                    "ai_using_provider",
                    provider=t(provider.label_key),
                    name=os.path.basename(txt_path),
                )
            )
        except ImportError:
            pass

    provider.process(
        txt_path,
        prompts,
        credentials,
        log_func=log_func,
        on_file_created=on_file_created,
        resolve_output_path=resolve_output_path,
    )


def start_ai_postprocess_async(
    txt_path: str,
    provider_id: str = "cursor",
    credentials: Optional[Dict[str, Any]] = None,
    log_func: Optional[LogFunc] = None,
    on_file_created: Optional[Callable[[str], None]] = None,
    on_complete: Optional[Callable[[], None]] = None,
    delay_s: float = AI_POSTPROCESS_DELAY_S,
    resolve_output_path: Optional[Callable[[str], str]] = None,
    prompts: Optional[List[PromptTuple]] = None,
) -> None:
    """Запускає process_txt_with_ai у daemon-потоці."""

    def _run():
        try:
            process_txt_with_ai(
                txt_path,
                provider_id=provider_id,
                credentials=credentials,
                log_func=log_func,
                on_file_created=on_file_created,
                delay_s=delay_s,
                resolve_output_path=resolve_output_path,
                prompts=prompts,
            )
        except Exception as e:
            if log_func:
                try:
                    from whisperfast.i18n import t
                    log_func(t("ai_unexpected_error", error=str(e)))
                except ImportError:
                    pass
        finally:
            if on_complete:
                try:
                    on_complete()
                except Exception:
                    pass

    threading.Thread(target=_run, daemon=True).start()
