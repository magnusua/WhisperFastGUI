"""Anthropic Claude: Messages API + browser fallback."""
from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional, Tuple

from whisperfast.postprocess.common import http_json_request
from whisperfast.postprocess.providers.base import run_browser_fallback, run_provider_chain

CLAUDE_BROWSER_URL = "https://claude.ai/new"
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-5"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 16384


def resolve_anthropic_api_key(settings_key: str = "") -> str:
    env_key = (
        os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("CLAUDE_API_KEY")
        or ""
    ).strip()
    if env_key:
        return env_key
    return (settings_key or "").strip()


def resolve_claude_model(settings_model: str = "") -> str:
    env_model = (os.environ.get("CLAUDE_MODEL") or "").strip()
    if env_model:
        return env_model
    return (settings_model or "").strip() or DEFAULT_CLAUDE_MODEL


def _extract_claude_text(resp: Dict[str, Any]) -> str:
    from whisperfast.i18n import t

    content = resp.get("content") or []
    if not content:
        raise RuntimeError(t("claude_no_content", detail=str(resp)))
    texts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
            texts.append(str(block["text"]))
    text = "\n".join(texts).strip()
    if not text:
        raise RuntimeError(t("claude_empty_text"))
    return text


def call_claude_messages(api_key: str, model: str, user_message: str) -> str:
    payload = {
        "model": model,
        "max_tokens": DEFAULT_MAX_TOKENS,
        "temperature": 0.2,
        "messages": [{"role": "user", "content": user_message}],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
    }
    resp = http_json_request(ANTHROPIC_API_URL, payload, headers=headers)
    return _extract_claude_text(resp)


class ClaudeProvider:
    id = "claude"
    label_key = "ai_provider_claude"

    def has_api_credentials(self, credentials: Dict[str, Any]) -> bool:
        return bool(
            resolve_anthropic_api_key((credentials.get("anthropic_api_key") or "").strip())
        )

    def process(
        self,
        txt_path: str,
        prompts: List[Tuple[int, str, str]],
        credentials: Dict[str, Any],
        log_func: Optional[Callable[..., None]] = None,
        on_file_created: Optional[Callable[[str], None]] = None,
        resolve_output_path: Optional[Callable[[str], str]] = None,
    ) -> List[str]:
        api_key = resolve_anthropic_api_key(
            (credentials.get("anthropic_api_key") or "").strip()
        )
        model = resolve_claude_model((credentials.get("claude_model") or "").strip())
        txt_path = os.path.abspath(txt_path)
        if not prompts:
            return []

        if not self.has_api_credentials(credentials):
            return run_browser_fallback(self.id, CLAUDE_BROWSER_URL, txt_path, prompts, log_func)

        return run_provider_chain(
            self.id,
            lambda user_msg: call_claude_messages(api_key, model, user_msg),
            txt_path,
            prompts,
            log_func,
            on_file_created,
            resolve_output_path,
        )
