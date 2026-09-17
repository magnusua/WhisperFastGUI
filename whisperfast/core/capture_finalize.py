"""Finalize capture WAV: encode, template name, enqueue, on_stop hook."""
from __future__ import annotations

import os
import subprocess
from datetime import datetime
from typing import Optional

from whisperfast.core.capture_encode import encode_capture, normalize_codec
from whisperfast.core.capture_names import render_filename
from whisperfast.core.capture_prefs import snapshot_capture_settings
from whisperfast.core.source_relocate import unique_dest_path


def capture_dir_from_settings(settings: dict, *, auto: bool) -> str:
    from whisperfast.core.capture import default_capture_dir

    raw = (settings.get("capture_dir") or "").strip()
    root = raw if raw and os.path.isdir(raw) else (raw if raw else default_capture_dir())
    if raw and not os.path.isdir(root):
        try:
            os.makedirs(root, exist_ok=True)
        except OSError:
            root = default_capture_dir()
    os.makedirs(root, exist_ok=True)
    if auto and settings.get("capture_auto_date_subdir", True):
        day = datetime.now().strftime("%Y-%m-%d")
        root = os.path.join(root, day)
        os.makedirs(root, exist_ok=True)
    return root


def settings_from_app(app) -> dict:
    cfg = getattr(app, "capture_cfg", None)
    if isinstance(cfg, dict):
        return snapshot_capture_settings(cfg)
    try:
        from whisperfast.settings import load_app_settings

        return snapshot_capture_settings(load_app_settings())
    except Exception:
        return snapshot_capture_settings({})


def finalize_wav(
    wav_path: str,
    settings: dict,
    *,
    window_title: str = "FTW",
    calendar_title: str = "",
    is_clip: bool = False,
    keep_draft: Optional[bool] = None,
    log_func=None,
) -> Optional[str]:
    if not wav_path or not os.path.isfile(wav_path):
        return None
    codec = normalize_codec(str(settings.get("capture_codec") or "opus"))
    bitrate = settings.get("capture_bitrate_kbit") or 24
    channels = str(settings.get("capture_channels") or "mono")
    use_cal = bool(settings.get("capture_filename_use_calendar"))
    stem = render_filename(
        str(settings.get("capture_filename_template") or ""),
        datetime.now(),
        window_title=window_title or "FTW",
        calendar_title=calendar_title or "",
        use_calendar=use_cal,
    )
    dest_dir = os.path.dirname(os.path.abspath(wav_path))
    from whisperfast.core.capture_prefs import codec_extension

    dest_name = stem + codec_extension(codec)
    dest = unique_dest_path(dest_dir, dest_name)
    encoded, err = encode_capture(
        wav_path,
        dest,
        codec=codec,
        bitrate_kbit=bitrate,
        channels=channels,
        log_func=log_func,
    )
    if err and log_func:
        try:
            log_func(err)
        except Exception:
            pass
    if not encoded or not os.path.isfile(encoded):
        return wav_path
    unique = unique_dest_path(os.path.dirname(encoded), os.path.basename(encoded))
    if os.path.normcase(unique) != os.path.normcase(encoded):
        try:
            os.replace(encoded, unique)
            encoded = unique
        except OSError:
            pass
    keep = settings.get("keep_audio") if keep_draft is None else keep_draft
    if not keep:
        try:
            if os.path.normcase(os.path.abspath(wav_path)) != os.path.normcase(
                os.path.abspath(encoded)
            ):
                os.remove(wav_path)
        except OSError:
            pass
    del is_clip
    return encoded


def run_on_stop_hook(command: str, path: str, log_func=None) -> None:
    cmd = (command or "").strip()
    if not cmd:
        return
    try:
        subprocess.Popen(cmd.replace("{path}", path), shell=True)
    except Exception as e:
        if log_func:
            try:
                log_func(str(e))
            except Exception:
                pass
