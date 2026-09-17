"""OpenAI-compatible Chat Completions (LM Studio, Groq, DeepSeek, local vLLM)."""
from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin

from whisperfast.postprocess.common import http_json_request
from whisperfast.postprocess.providers.base import run_browser_fallback, run_provider_chain
from whisperfast.postprocess.usage import notify_usage

DEFAULT_COMPAT_URL = "http://127.0.0.1:1234/v1"
DEFAULT_COMPAT_MODEL = "local-model"
OPENAI_BROWSER_URL = "https://platform.openai.com/playground"


def resolve_compat_base_url(settings_url: str = "") -> str:
    env = (os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE") or "").strip()
    url = (env or settings_url or "").strip().rstrip("/")
    return url


def resolve_compat_api_key(settings_key: str = "") -> str:
    env = (os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_COMPAT_API_KEY") or "").strip()
    return (env or settings_key or "").strip()


def resolve_compat_model(settings_model: str = "") -> str:
    env = (os.environ.get("OPENAI_MODEL") or "").strip()
    return (env or settings_model or "").strip() or DEFAULT_COMPAT_MODEL


def call_openai_chat(base_url: str, api_key: str, model: str, user_message: str) -> str:
    url = urljoin(base_url.rstrip("/") + "/", "chat/completions")
    payload = {
        "model": model,
        "temperature": 0.2,
        "messages": [{"role": "user", "content": user_message}],
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    resp = http_json_request(url, payload, headers=headers)
    choices = resp.get("choices") or []
    if not choices:
        from whisperfast.i18n import t

        raise RuntimeError(t("openai_compat_no_choices", detail=str(resp)))
    text = (((choices[0] or {}).get("message") or {}).get("content") or "").strip()
    if not text:
        from whisperfast.i18n import t

        raise RuntimeError(t("openai_compat_empty_text"))
    usage = resp.get("usage") or {}
    notify_usage(
        "openai_compat",
        int(usage.get("prompt_tokens") or 0),
        int(usage.get("completion_tokens") or 0),
    )
    return text


class OpenAICompatProvider:
    id = "openai_compat"
    label_key = "ai_provider_openai_compat"

    def has_api_credentials(self, credentials: Dict[str, Any]) -> bool:
        return bool(resolve_compat_base_url((credentials.get("openai_compatible_base_url") or "").strip()))

    def process(
        self,
        txt_path: str,
        prompts: List[Tuple[int, str, str]],
        credentials: Dict[str, Any],
        log_func: Optional[Callable[..., None]] = None,
        on_file_created: Optional[Callable[[str], None]] = None,
        resolve_output_path: Optional[Callable[[str], str]] = None,
    ) -> List[str]:
        txt_path = os.path.abspath(txt_path)
        if not prompts:
            return []
        base = resolve_compat_base_url(
            (credentials.get("openai_compatible_base_url") or "").strip()
        )
        key = resolve_compat_api_key(
            (credentials.get("openai_compatible_api_key") or "").strip()
        )
        model = resolve_compat_model(
            (credentials.get("openai_compatible_model") or "").strip()
        )
        if not base:
            return run_browser_fallback(
                self.id, OPENAI_BROWSER_URL, txt_path, prompts, log_func
            )
        return run_provider_chain(
            self.id,
            lambda user_msg: call_openai_chat(base, key, model, user_msg),
            txt_path,
            prompts,
            log_func,
            on_file_created,
            resolve_output_path,
        )
