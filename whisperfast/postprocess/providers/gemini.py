"""Google Gemini: Generative Language API + browser fallback."""
from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional, Tuple

from whisperfast.postprocess.common import http_json_request
from whisperfast.postprocess.providers.base import run_browser_fallback, run_provider_chain

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

        if not self.has_api_credentials(credentials):
            return run_browser_fallback(self.id, GEMINI_BROWSER_URL, txt_path, prompts, log_func)

        return run_provider_chain(
            self.id,
            lambda user_msg: call_gemini_generate(api_key, model, user_msg),
            txt_path,
            prompts,
            log_func,
            on_file_created,
            resolve_output_path,
        )
