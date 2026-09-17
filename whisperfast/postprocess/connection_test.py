"""Ping an AI provider so the user can check the key/URL before a real job."""
from __future__ import annotations

from typing import Any, Dict, Tuple

from whisperfast.postprocess.providers import (
    PROVIDER_CLAUDE,
    PROVIDER_COPILOT,
    PROVIDER_CURSOR,
    PROVIDER_GEMINI,
    PROVIDER_OLLAMA,
    PROVIDER_OPENAI_COMPAT,
    normalize_provider_id,
)


def test_provider(provider_id: str, credentials: Dict[str, Any]) -> Tuple[bool, str]:
    """Return (ok, message). Never raises."""
    pid = normalize_provider_id(provider_id)
    try:
        from whisperfast.i18n import t
    except ImportError:
        def t(key, **kw):
            return key

    try:
        if pid == PROVIDER_OLLAMA:
            from whisperfast.postprocess.providers.ollama import ping_ollama, resolve_ollama_base_url, resolve_ollama_model

            msg = ping_ollama(
                resolve_ollama_base_url((credentials.get("ollama_base_url") or "").strip()),
                resolve_ollama_model((credentials.get("ollama_model") or "").strip()),
            )
            return True, msg
        if pid == PROVIDER_OPENAI_COMPAT:
            from whisperfast.postprocess.providers.openai_compat import (
                call_openai_chat,
                resolve_compat_api_key,
                resolve_compat_base_url,
                resolve_compat_model,
            )

            base = resolve_compat_base_url(
                (credentials.get("openai_compatible_base_url") or "").strip()
            )
            if not base:
                return False, t("ai_test_missing_url")
            call_openai_chat(
                base,
                resolve_compat_api_key((credentials.get("openai_compatible_api_key") or "").strip()),
                resolve_compat_model((credentials.get("openai_compatible_model") or "").strip()),
                "Reply with the single word pong.",
            )
            return True, t("ai_test_ok")
        if pid == PROVIDER_GEMINI:
            from whisperfast.postprocess.providers.gemini import (
                call_gemini_generate,
                resolve_gemini_api_key,
                resolve_gemini_model,
            )

            key = resolve_gemini_api_key((credentials.get("gemini_api_key") or "").strip())
            if not key:
                return False, t("ai_test_missing_key")
            call_gemini_generate(
                key,
                resolve_gemini_model((credentials.get("gemini_model") or "").strip()),
                "Reply with the single word pong.",
            )
            return True, t("ai_test_ok")
        if pid == PROVIDER_CLAUDE:
            from whisperfast.postprocess.providers.claude import (
                call_claude_messages,
                resolve_anthropic_api_key,
                resolve_claude_model,
            )

            key = resolve_anthropic_api_key((credentials.get("anthropic_api_key") or "").strip())
            if not key:
                return False, t("ai_test_missing_key")
            call_claude_messages(
                key,
                resolve_claude_model((credentials.get("claude_model") or "").strip()),
                "Reply with the single word pong.",
            )
            return True, t("ai_test_ok")
        if pid == PROVIDER_COPILOT:
            from whisperfast.postprocess.providers.copilot import (
                call_azure_chat,
                resolve_azure_openai_api_key,
                resolve_azure_openai_api_version,
                resolve_azure_openai_deployment,
                resolve_azure_openai_endpoint,
            )

            key = resolve_azure_openai_api_key(
                (credentials.get("azure_openai_api_key") or "").strip()
            )
            endpoint = resolve_azure_openai_endpoint(
                (credentials.get("azure_openai_endpoint") or "").strip()
            )
            deployment = resolve_azure_openai_deployment(
                (credentials.get("azure_openai_deployment") or "").strip()
            )
            if not (key and endpoint and deployment):
                return False, t("ai_test_missing_key")
            call_azure_chat(
                endpoint,
                key,
                deployment,
                resolve_azure_openai_api_version(
                    (credentials.get("azure_openai_api_version") or "").strip()
                ),
                "Reply with the single word pong.",
            )
            return True, t("ai_test_ok")
        if pid == PROVIDER_CURSOR:
            from whisperfast.postprocess.cursor_postprocess import resolve_cursor_api_key

            if resolve_cursor_api_key((credentials.get("cursor_api_key") or "").strip()):
                return True, t("ai_test_key_present")
            return False, t("ai_test_missing_key")
        return False, t("ai_test_unknown_provider")
    except Exception as e:
        return False, str(e)
