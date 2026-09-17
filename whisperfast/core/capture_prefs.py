"""Defaults and helpers for capture / meeting settings stored in settings.json."""
from __future__ import annotations

from typing import Any, Dict, Mapping

AUTO_RECORD_PRESETS = (
    "zoom",
    "teams",
    "meet",
    "telegram",
    "viber",
    "phone_link",
    "whatsapp",
)

CAPTURE_DEFAULTS: Dict[str, Any] = {
    "user_name": "You",
    "them_name": "Them",
    "keep_audio": True,
    "capture_mix_mode": "all",  # all | process_allowlist | device
    "capture_include_mic": True,
    "capture_include_system": True,
    "capture_loopback_device": "",
    "capture_source_allowlist": "",
    "capture_source_denylist": "",
    "capture_codec": "opus",
    "capture_bitrate_kbit": 24,
    "capture_channels": "mono",
    "capture_dir": "",
    "capture_filename_template": "%W %Y-%m-%d %H.%M.%S",
    "capture_filename_use_calendar": False,
    "capture_auto_date_subdir": True,
    "capture_clip_seconds": 122,
    "capture_max_duration_s": 0,
    "auto_record_enabled": False,
    "auto_record_zoom": False,
    "auto_record_teams": False,
    "auto_record_meet": False,
    "auto_record_telegram": False,
    "auto_record_viber": False,
    "auto_record_phone_link": False,
    "auto_record_whatsapp": False,
    "auto_record_custom_exes": "",
    "auto_record_start_delay_s": 3,
    "auto_record_stop_delay_s": 8,
    "auto_record_min_duration_s": 15,
    "auto_record_silence_stop_s": 15,
    "calendar_google_enabled": False,
    "calendar_outlook_enabled": False,
    "calendar_ics_enabled": False,
    "calendar_ics_path": "",
    "calendar_start_lead_min": 2,
    "calendar_event_filter": "all",
    "calendar_keywords": "",
    "google_calendar_client_id": "",
    "google_calendar_client_secret": "",
    "google_calendar_refresh_token": "",
    "google_calendar_email": "",
    "google_calendar_ids": "primary",
    "live_preview_enabled": False,
    "live_preview_model": "tiny",
    "on_stop_hook": "",
}

CODEC_EXTENSIONS = {
    "opus": ".opus",
    "aac": ".m4a",
    "mp3": ".mp3",
    "wav": ".wav",
}


def capture_defaults() -> Dict[str, Any]:
    return dict(CAPTURE_DEFAULTS)


def enabled_auto_apps(settings: Mapping[str, Any]) -> list:
    out = []
    for key in AUTO_RECORD_PRESETS:
        if settings.get(f"auto_record_{key}"):
            out.append(key)
    return out


def codec_extension(codec: str) -> str:
    return CODEC_EXTENSIONS.get((codec or "opus").strip().lower(), ".opus")


def snapshot_capture_settings(settings: Mapping[str, Any]) -> Dict[str, Any]:
    out = capture_defaults()
    for key in out:
        if key in settings:
            out[key] = settings[key]
    return out
