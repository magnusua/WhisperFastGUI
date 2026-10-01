"""Choose Telegram media type (video / audio / document) for an outgoing path."""
from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Dict

from whisperfast.config import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS
from whisperfast.utils import win_no_window_kwargs

# Bot API sendVideo is reliable for these; others fall back to document.
_BOT_VIDEO_EXTS = frozenset({".mp4", ".mov", ".m4v", ".mpeg", ".mpg"})
_BOT_AUDIO_EXTS = frozenset(
    {
        ".mp3",
        ".m4a",
        ".aac",
        ".ogg",
        ".opus",
        ".flac",
        ".wav",
        ".wma",
    }
)


def path_ext(path: str) -> str:
    return os.path.splitext(path or "")[1].lower()


def media_kind(path: str) -> str:
    """Return ``video``, ``audio``, or ``document`` for Bot / Telethon send."""
    ext = path_ext(path)
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    return "document"


def bot_send_method(path: str) -> str:
    """Bot API method name: sendVideo, sendAudio, or sendDocument."""
    ext = path_ext(path)
    if ext in _BOT_VIDEO_EXTS:
        return "sendVideo"
    if ext in _BOT_AUDIO_EXTS:
        return "sendAudio"
    return "sendDocument"


def bot_file_field(method: str) -> str:
    if method == "sendVideo":
        return "video"
    if method == "sendAudio":
        return "audio"
    return "document"


def probe_video(path: str) -> Dict[str, Any]:
    """Duration (seconds) and stream size via ffprobe. Empty dict on failure."""
    if not path or not os.path.isfile(path):
        return {}
    try:
        kwargs = {"capture_output": True, "text": True, "timeout": 30, **win_no_window_kwargs()}
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                path,
            ],
            **kwargs,
        )
        if result.returncode != 0 or not (result.stdout or "").strip():
            return {}
        data = json.loads(result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, OSError, json.JSONDecodeError):
        return {}
    out: Dict[str, Any] = {}
    try:
        duration = float((data.get("format") or {}).get("duration") or 0)
        if duration > 0:
            out["duration"] = duration
    except (TypeError, ValueError):
        pass
    streams = data.get("streams") or []
    if streams:
        try:
            width = int(streams[0].get("width") or 0)
            height = int(streams[0].get("height") or 0)
            if width > 0 and height > 0:
                out["width"] = width
                out["height"] = height
        except (TypeError, ValueError, IndexError):
            pass
    return out


def telethon_send_kwargs(path: str) -> Dict[str, Any]:
    """Extra kwargs for Telethon ``send_file`` so video shows as playable media."""
    kind = media_kind(path)
    if kind != "video":
        return {}
    kwargs: Dict[str, Any] = {"supports_streaming": True, "force_document": False}
    info = probe_video(path)
    duration = int(info.get("duration") or 0)
    width = int(info.get("width") or 0)
    height = int(info.get("height") or 0)
    if duration > 0 and width > 0 and height > 0:
        try:
            from telethon.tl.types import DocumentAttributeVideo

            kwargs["attributes"] = [
                DocumentAttributeVideo(
                    duration=duration,
                    w=width,
                    h=height,
                    supports_streaming=True,
                )
            ]
        except Exception:
            pass
    return kwargs
