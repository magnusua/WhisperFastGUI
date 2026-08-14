"""Anthropic Claude: Messages API + browser fallback."""
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

        if not api_key:
            if log_func:
                try:
                    from whisperfast.i18n import t

                    log_func(t("claude_no_api_key_fallback"))
                except ImportError:
                    pass
            open_browser_fallback(
                CLAUDE_BROWSER_URL,
                build_clipboard_fallback_text(prompts[0][2], txt_path),
                log_func,
                opened_key="claude_browser_opened",
                copied_key="claude_browser_prompt_copied",
                manual_key="claude_browser_manual_hint",
                name=os.path.basename(txt_path),
            )
            return []

        created: List[str] = []
        current_input = txt_path
        for num, name, text in prompts:
            out_path = edited_output_path(txt_path, num, name)
            if resolve_output_path:
                out_path = resolve_output_path(out_path)
                if not out_path:
                    if log_func:
                        try:
                            from whisperfast.i18n import t

                            log_func(
                                t(
                                    "file_exists_skipped",
                                    name=os.path.basename(
                                        edited_output_path(txt_path, num, name)
                                    ),
                                )
                            )
                        except ImportError:
                            pass
                    break
            label = name or f"#{num}"
            if log_func:
                try:
                    from whisperfast.i18n import t

                    log_func(t("claude_processing_prompt", num=num, name=label))
                except ImportError:
                    pass
            try:
                content = read_text_file(current_input)
                user_msg = build_transform_user_message(text, content)
                result = call_claude_messages(api_key, model, user_msg)
                write_text_file(out_path, result)
            except Exception as e:
                if log_func:
                    try:
                        from whisperfast.i18n import t

                        log_func(t("claude_prompt_error", num=num, error=str(e)))
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

                    log_func(t("claude_file_created", num=num, name=os.path.basename(out_path)))
                except ImportError:
                    pass
            current_input = out_path

        if log_func:
            try:
                from whisperfast.i18n import t

                base = os.path.basename(txt_path)
                if created:
                    log_func(t("claude_chain_done", count=len(created), name=base))
                else:
                    log_func(t("claude_chain_no_output", name=base))
            except ImportError:
                pass
        return created
