"""Google Gemini: Generative Language API + browser fallback."""
from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional, Tuple

from whisperfast.postprocess.common import (
    build_clipboard_fallback_text,
    build_transform_user_message,
    http_json_request,
    open_browser_fallback,
    read_text_file,
    write_text_file,
)
from whisperfast.postprocess.cursor_postprocess import edited_output_path

GEMINI_BROWSER_URL = "https://gemini.google.com/app"
DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"


def resolve_gemini_api_key(settings_key: str = "") -> str:
    env_key = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
    if env_key:
        return env_key
    return (settings_key or "").strip()


def resolve_gemini_model(settings_model: str = "") -> str:
    env_model = (os.environ.get("GEMINI_MODEL") or "").strip()
    if env_model:
        return env_model
    return (settings_model or "").strip() or DEFAULT_GEMINI_MODEL


def _extract_gemini_text(resp: Dict[str, Any]) -> str:
    from whisperfast.i18n import t

    candidates = resp.get("candidates") or []
    if not candidates:
        feedback = resp.get("promptFeedback") or {}
        raise RuntimeError(t("gemini_no_candidates", detail=str(feedback or resp)))
    parts = (((candidates[0] or {}).get("content") or {}).get("parts")) or []
    texts = []
    for p in parts:
        if isinstance(p, dict) and p.get("text"):
            texts.append(str(p["text"]))
    text = "\n".join(texts).strip()
    if not text:
        raise RuntimeError(t("gemini_empty_text"))
    return text


def call_gemini_generate(api_key: str, model: str, user_message: str) -> str:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    payload = {
        "contents": [{"role": "user", "parts": [{"text": user_message}]}],
        "generationConfig": {"temperature": 0.2},
    }
    resp = http_json_request(url, payload)
    return _extract_gemini_text(resp)


class GeminiProvider:
    id = "gemini"
    label_key = "ai_provider_gemini"

    def has_api_credentials(self, credentials: Dict[str, Any]) -> bool:
        return bool(resolve_gemini_api_key((credentials.get("gemini_api_key") or "").strip()))

    def process(
        self,
        txt_path: str,
        prompts: List[Tuple[int, str, str]],
        credentials: Dict[str, Any],
        log_func: Optional[Callable[..., None]] = None,
        on_file_created: Optional[Callable[[str], None]] = None,
        resolve_output_path: Optional[Callable[[str], str]] = None,
    ) -> List[str]:
        api_key = resolve_gemini_api_key((credentials.get("gemini_api_key") or "").strip())
        model = resolve_gemini_model((credentials.get("gemini_model") or "").strip())
        txt_path = os.path.abspath(txt_path)
        if not prompts:
            return []

        if not api_key:
            if log_func:
                try:
                    from whisperfast.i18n import t
                    log_func(t("gemini_no_api_key_fallback"))
                except ImportError:
                    pass
            open_browser_fallback(
                GEMINI_BROWSER_URL,
                build_clipboard_fallback_text(prompts[0][2], txt_path),
                log_func,
                opened_key="gemini_browser_opened",
                copied_key="gemini_browser_prompt_copied",
                manual_key="gemini_browser_manual_hint",
                name=os.path.basename(txt_path),
            )
            return []

        created: List[str] = []
        current_input = txt_path
        for num, name, text in prompts:
            out_path = edited_output_path(txt_path, num, name)
            if resolve_output_path:
                out_path = resolve_output_path(out_path)
            label = name or f"#{num}"
            if log_func:
                try:
                    from whisperfast.i18n import t
                    log_func(t("gemini_processing_prompt", num=num, name=label))
                except ImportError:
                    pass
            try:
                content = read_text_file(current_input)
                user_msg = build_transform_user_message(text, content)
                result = call_gemini_generate(api_key, model, user_msg)
                write_text_file(out_path, result)
            except Exception as e:
                if log_func:
                    try:
                        from whisperfast.i18n import t
                        log_func(t("gemini_prompt_error", num=num, error=str(e)))
                    except ImportError:
                        pass
                break
            if not os.path.isfile(out_path):
                if log_func:
                    try:
                        from whisperfast.i18n import t
                        log_func(t("cursor_output_missing", path=out_path))
                    except ImportError:
                        pass
                break
            created.append(out_path)
            if on_file_created:
                on_file_created(out_path)
            if log_func:
                try:
                    from whisperfast.i18n import t
                    log_func(t("gemini_file_created", num=num, name=os.path.basename(out_path)))
                except ImportError:
                    pass
            current_input = out_path

        if log_func:
            try:
                from whisperfast.i18n import t
                base = os.path.basename(txt_path)
                if created:
                    log_func(t("gemini_chain_done", count=len(created), name=base))
                else:
                    log_func(t("gemini_chain_no_output", name=base))
            except ImportError:
                pass
        return created
