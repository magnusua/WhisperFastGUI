"""Google Gemini: Generative Language API, OAuth, or browser fallback."""
from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional, Tuple

from whisperfast.core.google_oauth import refresh_google_token, resolve_google_client_id
from whisperfast.postprocess.common import http_json_request
from whisperfast.postprocess.providers.base import run_browser_fallback, run_provider_chain
from whisperfast.secrets_store import protect_string, unprotect_string

GEMINI_BROWSER_URL = "https://gemini.google.com/app"
DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_GENERATE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


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


def resolve_gemini_refresh_token(credentials: Dict[str, Any]) -> str:
    return unprotect_string(str((credentials or {}).get("gemini_oauth_refresh_token") or ""))


def resolve_gemini_client_id(credentials: Dict[str, Any]) -> str:
    cred = credentials or {}
    return resolve_google_client_id(
        str(cred.get("google_oauth_client_id") or cred.get("google_calendar_client_id") or "")
    )


def resolve_gemini_client_secret(credentials: Dict[str, Any]) -> str:
    cred = credentials or {}
    return unprotect_string(
        str(cred.get("google_oauth_client_secret") or cred.get("google_calendar_client_secret") or "")
    )


def resolve_gemini_project_id(credentials: Dict[str, Any]) -> str:
    return str((credentials or {}).get("google_cloud_project_id") or "").strip()


def store_gemini_session(
    settings: Dict[str, Any],
    refresh_token: str,
    email: str = "",
) -> None:
    settings["gemini_oauth_refresh_token"] = protect_string(refresh_token or "")
    settings["gemini_oauth_email"] = str(email or "").strip()


def clear_gemini_session(settings: Dict[str, Any]) -> None:
    settings["gemini_oauth_refresh_token"] = ""
    settings["gemini_oauth_email"] = ""


def gemini_access_token(credentials: Dict[str, Any]) -> Tuple[str, str]:
    """Return (access_token, project_id) from a stored refresh token."""
    refresh = resolve_gemini_refresh_token(credentials)
    client_id = resolve_gemini_client_id(credentials)
    if not refresh or not client_id:
        return "", resolve_gemini_project_id(credentials)
    token = refresh_google_token(client_id, resolve_gemini_client_secret(credentials), refresh)
    access = str(token.get("access_token") or "").strip()
    return access, resolve_gemini_project_id(credentials)


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


def call_gemini_generate(
    api_key: str,
    model: str,
    user_message: str,
    access_token: str = "",
    project_id: str = "",
) -> str:
    payload = {
        "contents": [{"role": "user", "parts": [{"text": user_message}]}],
        "generationConfig": {"temperature": 0.2},
    }
    headers: Dict[str, str] = {}
    url = GEMINI_GENERATE_URL.format(model=model)
    if api_key:
        url = f"{url}?key={api_key}"
    elif access_token:
        headers["Authorization"] = f"Bearer {access_token}"
        if project_id:
            headers["x-goog-user-project"] = project_id
    else:
        raise RuntimeError("missing Gemini credentials")
    resp = http_json_request(url, payload, headers=headers or None)
    return _extract_gemini_text(resp)


def generate_gemini(credentials: Dict[str, Any], model: str, user_message: str) -> str:
    api_key = resolve_gemini_api_key((credentials.get("gemini_api_key") or "").strip())
    if api_key:
        return call_gemini_generate(api_key, model, user_message)
    access, project_id = gemini_access_token(credentials)
    if not access:
        raise RuntimeError("missing Gemini credentials")
    return call_gemini_generate("", model, user_message, access_token=access, project_id=project_id)


class GeminiProvider:
    id = "gemini"
    label_key = "ai_provider_gemini"

    def has_api_credentials(self, credentials: Dict[str, Any]) -> bool:
        if resolve_gemini_api_key((credentials.get("gemini_api_key") or "").strip()):
            return True
        return bool(resolve_gemini_refresh_token(credentials) and resolve_gemini_client_id(credentials))

    def process(
        self,
        txt_path: str,
        prompts: List[Tuple[int, str, str]],
        credentials: Dict[str, Any],
        log_func: Optional[Callable[..., None]] = None,
        on_file_created: Optional[Callable[[str], None]] = None,
        resolve_output_path: Optional[Callable[[str], str]] = None,
    ) -> List[str]:
        model = resolve_gemini_model((credentials.get("gemini_model") or "").strip())
        txt_path = os.path.abspath(txt_path)
        if not prompts:
            return []

        if not self.has_api_credentials(credentials):
            return run_browser_fallback(self.id, GEMINI_BROWSER_URL, txt_path, prompts, log_func)

        return run_provider_chain(
            self.id,
            lambda user_msg: generate_gemini(credentials, model, user_msg),
            txt_path,
            prompts,
            log_func,
            on_file_created,
            resolve_output_path,
        )
