"""Transcribe one media file and run the configured AI prompts. No GUI."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional

from whisperfast.config import DEFAULT_MODEL, LANG_AUTO_VALUE
from whisperfast.core.export_transcript import format_segment_line
from whisperfast.i18n import t
from whisperfast.postprocess.providers import get_provider, normalize_provider_id
from whisperfast.settings import normalize_default_prompt_nums

LogFunc = Callable[[str], None]


class PipelineError(RuntimeError):
    """Whisper or AI failed. ``txt_path`` is set when the transcript was written."""

    def __init__(self, message: str, txt_path: Optional[str] = None):
        super().__init__(message)
        self.txt_path = txt_path


@dataclass
class PipelineResult:
    txt_path: str
    md_paths: List[str] = field(default_factory=list)
    docx_paths: List[str] = field(default_factory=list)


def credentials_from_settings(settings: Mapping[str, Any]) -> Dict[str, Any]:
    """Same credential bag the GUI passes to AI providers, read from settings."""
    src = settings or {}

    def g(key: str, default: str = "") -> str:
        return str(src.get(key) or default).strip()

    return {
        "cursor_api_key": g("cursor_api_key"),
        "gemini_api_key": g("gemini_api_key"),
        "gemini_model": g("gemini_model") or "gemini-2.0-flash",
        "gemini_oauth_refresh_token": g("gemini_oauth_refresh_token"),
        "gemini_oauth_email": g("gemini_oauth_email"),
        "google_oauth_client_id": g("google_oauth_client_id") or g("google_calendar_client_id"),
        "google_oauth_client_secret": g("google_oauth_client_secret") or g("google_calendar_client_secret"),
        "google_calendar_client_id": g("google_calendar_client_id"),
        "google_calendar_client_secret": g("google_calendar_client_secret"),
        "google_cloud_project_id": g("google_cloud_project_id"),
        "anthropic_api_key": g("anthropic_api_key"),
        "claude_model": g("claude_model") or "claude-sonnet-4-5",
        "azure_openai_endpoint": g("azure_openai_endpoint"),
        "azure_openai_api_key": g("azure_openai_api_key"),
        "azure_openai_deployment": g("azure_openai_deployment"),
        "azure_openai_api_version": g("azure_openai_api_version") or "2024-08-01-preview",
        "ollama_base_url": g("ollama_base_url"),
        "ollama_model": g("ollama_model"),
        "openai_compatible_base_url": g("openai_compatible_base_url"),
        "openai_compatible_api_key": g("openai_compatible_api_key"),
        "openai_compatible_model": g("openai_compatible_model"),
    }


def _language_param(settings: Mapping[str, Any]):
    lang_val = settings.get("lang_mode", LANG_AUTO_VALUE)
    if lang_val in (None, "", LANG_AUTO_VALUE, "AUTO"):
        return None
    return lang_val


def _write_transcript(media_path: str, segments) -> str:
    folder = os.path.dirname(os.path.abspath(media_path))
    base = os.path.splitext(os.path.basename(media_path))[0]
    txt_path = os.path.join(folder, base + ".txt")
    lines = [format_segment_line(seg) for seg in segments]
    with open(txt_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    return txt_path


def run_pipeline(
    media_path: str,
    settings: Mapping[str, Any],
    *,
    model_getter: Optional[Callable] = None,
    ai_runner: Optional[Callable] = None,
    log: Optional[LogFunc] = None,
) -> PipelineResult:
    """Transcribe ``media_path`` and run ``ai_default_prompt_nums``. No dialogs."""
    log = log or (lambda _msg: None)
    if model_getter is None:
        from whisperfast.core.model_manager import WhisperModelSingleton

        model_getter = WhisperModelSingleton.get
    if ai_runner is None:
        from whisperfast.postprocess.ai_postprocess import process_txt_with_ai

        ai_runner = process_txt_with_ai

    device = str(settings.get("device_mode") or "AUTO")
    model_name = str(settings.get("whisper_model") or DEFAULT_MODEL)
    model = model_getter(log, device, model_name)
    segments_iter, _info = model.transcribe(
        media_path,
        language=_language_param(settings),
        vad_filter=True,
    )
    txt_path = _write_transcript(media_path, list(segments_iter))

    provider_id = normalize_provider_id(str(settings.get("ai_provider") or "cursor"))
    credentials = credentials_from_settings(settings)
    provider = get_provider(provider_id)
    if not provider.has_api_credentials(credentials):
        raise PipelineError(t("telegram_ai_no_key", provider=provider_id), txt_path=txt_path)

    from whisperfast.postprocess.cursor_postprocess import ensure_redactor_file, parse_redactor_prompts
    from whisperfast.postprocess.prompt_rules import select_prompts

    ensure_redactor_file()
    nums = normalize_default_prompt_nums(settings.get("ai_default_prompt_nums"))
    prompts = select_prompts(parse_redactor_prompts(), nums)
    if not prompts:
        raise PipelineError(t("telegram_no_prompts"), txt_path=txt_path)

    try:
        md_paths = ai_runner(
            txt_path,
            provider_id=provider_id,
            credentials=credentials,
            log_func=log,
            delay_s=0,
            prompts=prompts,
        ) or []
    except Exception as exc:
        raise PipelineError(str(exc), txt_path=txt_path) from exc

    docx_paths: List[str] = []
    if settings.get("export_md_to_docx") and md_paths:
        from whisperfast.core.pandoc_export import export_markdown

        for md_path in md_paths:
            docx_paths.extend(export_markdown(md_path, log_func=log) or [])

    return PipelineResult(
        txt_path=txt_path,
        md_paths=list(md_paths),
        docx_paths=docx_paths,
    )
