"""Microsoft Copilot path: Azure OpenAI API + browser Copilot fallback."""
from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin

from whisperfast.postprocess.common import (
    build_clipboard_fallback_text,
    build_transform_user_message,
    http_json_request,
    open_browser_fallback,
    read_text_file,
    write_text_file,
)
from whisperfast.postprocess.cursor_postprocess import edited_output_path

COPILOT_BROWSER_URL = "https://copilot.microsoft.com/"
DEFAULT_AZURE_API_VERSION = "2024-08-01-preview"


def resolve_azure_openai_api_key(settings_key: str = "") -> str:
    env_key = (
        os.environ.get("AZURE_OPENAI_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    ).strip()
    if env_key:
        return env_key
    return (settings_key or "").strip()


def resolve_azure_openai_endpoint(settings_endpoint: str = "") -> str:
    env_ep = (os.environ.get("AZURE_OPENAI_ENDPOINT") or "").strip()
    return (env_ep or settings_endpoint or "").strip().rstrip("/")


def resolve_azure_openai_deployment(settings_deployment: str = "") -> str:
    env_d = (os.environ.get("AZURE_OPENAI_DEPLOYMENT") or "").strip()
    return (env_d or settings_deployment or "").strip()


def resolve_azure_openai_api_version(settings_version: str = "") -> str:
    env_v = (os.environ.get("AZURE_OPENAI_API_VERSION") or "").strip()
    return (env_v or settings_version or "").strip() or DEFAULT_AZURE_API_VERSION


def _extract_chat_text(resp: Dict[str, Any]) -> str:
    from whisperfast.i18n import t

    choices = resp.get("choices") or []
    if not choices:
        raise RuntimeError(t("azure_no_choices", detail=str(resp)))
    message = (choices[0] or {}).get("message") or {}
    text = (message.get("content") or "").strip()
    if not text:
        raise RuntimeError(t("azure_empty_content"))
    return text


def call_azure_chat(
    endpoint: str,
    api_key: str,
    deployment: str,
    api_version: str,
    user_message: str,
) -> str:
    base = endpoint if endpoint.endswith("/") else endpoint + "/"
    path = f"openai/deployments/{deployment}/chat/completions?api-version={api_version}"
    url = urljoin(base, path)
    payload = {
        "messages": [
            {
                "role": "system",
                "content": (
                    "You transform documents. Reply with the full Markdown document only, "
                    "without surrounding commentary."
                ),
            },
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.2,
    }
    resp = http_json_request(
        url,
        payload,
        headers={"api-key": api_key},
    )
    return _extract_chat_text(resp)


class CopilotProvider:
    id = "copilot"
    label_key = "ai_provider_copilot"

    def has_api_credentials(self, credentials: Dict[str, Any]) -> bool:
        key = resolve_azure_openai_api_key((credentials.get("azure_openai_api_key") or "").strip())
        endpoint = resolve_azure_openai_endpoint(
            (credentials.get("azure_openai_endpoint") or "").strip()
        )
        deployment = resolve_azure_openai_deployment(
            (credentials.get("azure_openai_deployment") or "").strip()
        )
        return bool(key and endpoint and deployment)

    def process(
        self,
        txt_path: str,
        prompts: List[Tuple[int, str, str]],
        credentials: Dict[str, Any],
        log_func: Optional[Callable[..., None]] = None,
        on_file_created: Optional[Callable[[str], None]] = None,
        resolve_output_path: Optional[Callable[[str], str]] = None,
    ) -> List[str]:
        api_key = resolve_azure_openai_api_key(
            (credentials.get("azure_openai_api_key") or "").strip()
        )
        endpoint = resolve_azure_openai_endpoint(
            (credentials.get("azure_openai_endpoint") or "").strip()
        )
        deployment = resolve_azure_openai_deployment(
            (credentials.get("azure_openai_deployment") or "").strip()
        )
        api_version = resolve_azure_openai_api_version(
            (credentials.get("azure_openai_api_version") or "").strip()
        )
        txt_path = os.path.abspath(txt_path)
        if not prompts:
            return []

        if not (api_key and endpoint and deployment):
            if log_func:
                try:
                    from whisperfast.i18n import t
                    log_func(t("copilot_no_api_key_fallback"))
                except ImportError:
                    pass
            open_browser_fallback(
                COPILOT_BROWSER_URL,
                build_clipboard_fallback_text(prompts[0][2], txt_path),
                log_func,
                opened_key="copilot_browser_opened",
                copied_key="copilot_browser_prompt_copied",
                manual_key="copilot_browser_manual_hint",
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
                    log_func(t("copilot_processing_prompt", num=num, name=label))
                except ImportError:
                    pass
            try:
                content = read_text_file(current_input)
                user_msg = build_transform_user_message(text, content)
                result = call_azure_chat(
                    endpoint, api_key, deployment, api_version, user_msg
                )
                write_text_file(out_path, result)
            except Exception as e:
                if log_func:
                    try:
                        from whisperfast.i18n import t
                        log_func(t("copilot_prompt_error", num=num, error=str(e)))
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
                    log_func(t("copilot_file_created", num=num, name=os.path.basename(out_path)))
                except ImportError:
                    pass
            current_input = out_path

        if log_func:
            try:
                from whisperfast.i18n import t
                base = os.path.basename(txt_path)
                if created:
                    log_func(t("copilot_chain_done", count=len(created), name=base))
                else:
                    log_func(t("copilot_chain_no_output", name=base))
            except ImportError:
                pass
        return created
