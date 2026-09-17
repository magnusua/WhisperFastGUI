"""Ollama local LLM: /api/chat on localhost, no API key required."""
from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin

from whisperfast.postprocess.common import http_json_get, http_json_request
from whisperfast.postprocess.providers.base import run_provider_chain
from whisperfast.postprocess.usage import notify_usage

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "llama3.2"


def resolve_ollama_base_url(settings_url: str = "") -> str:
    env = (os.environ.get("OLLAMA_HOST") or os.environ.get("OLLAMA_BASE_URL") or "").strip()
    url = (env or settings_url or DEFAULT_OLLAMA_URL).strip().rstrip("/")
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return "http://" + url


def resolve_ollama_model(settings_model: str = "") -> str:
    env = (os.environ.get("OLLAMA_MODEL") or "").strip()
    return (env or settings_model or "").strip() or DEFAULT_OLLAMA_MODEL


def call_ollama_chat(base_url: str, model: str, user_message: str) -> str:
    url = urljoin(base_url + "/", "api/chat")
    payload = {
        "model": model,
        "stream": False,
        "messages": [{"role": "user", "content": user_message}],
    }
    resp = http_json_request(url, payload)
    msg = (resp.get("message") or {})
    text = (msg.get("content") or "").strip()
    if not text:
        from whisperfast.i18n import t

        raise RuntimeError(t("ollama_empty_text"))
    notify_usage(
        "ollama",
        int(resp.get("prompt_eval_count") or 0),
        int(resp.get("eval_count") or 0),
    )
    return text


def ping_ollama(base_url: str, model: str = "") -> str:
    """Return a short status string or raise."""
    tags = http_json_get(urljoin(base_url + "/", "api/tags"), timeout_s=8.0)
    names = [m.get("name") for m in (tags.get("models") or []) if isinstance(m, dict)]
    if model and names and not any(model == n or (n or "").startswith(model + ":") for n in names):
        from whisperfast.i18n import t

        return t("ai_test_ok_models", models=", ".join(names[:8]) or "—")
    from whisperfast.i18n import t

    return t("ai_test_ok_models", models=", ".join(names[:8]) or "—")


class OllamaProvider:
    id = "ollama"
    label_key = "ai_provider_ollama"

    def has_api_credentials(self, credentials: Dict[str, Any]) -> bool:
        # Local daemon; URL is enough. Ping is optional at Test-connection time.
        return True

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
        base = resolve_ollama_base_url((credentials.get("ollama_base_url") or "").strip())
        model = resolve_ollama_model((credentials.get("ollama_model") or "").strip())
        return run_provider_chain(
            self.id,
            lambda user_msg: call_ollama_chat(base, model, user_msg),
            txt_path,
            prompts,
            log_func,
            on_file_created,
            resolve_output_path,
        )
