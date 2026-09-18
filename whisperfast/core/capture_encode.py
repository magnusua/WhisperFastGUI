"""Encode capture PCM WAV to opus / aac / mp3 / wav after Stop or clip."""
from __future__ import annotations

import os
import subprocess
from typing import Optional, Tuple

from whisperfast.core.capture_prefs import codec_extension
from whisperfast.platform_util import win_no_window_kwargs

BITRATE_CHOICES = (16, 24, 32, 48, 64, 96, 128)

_FFMPEG_CODECS = {
    "opus": "libopus",
    "aac": "aac",
    "mp3": "libmp3lame",
    "wav": "pcm_s16le",
}


def normalize_codec(codec: str) -> str:
    c = (codec or "opus").strip().lower()
    if c in ("m4a", "aac-m4a"):
        return "aac"
    if c not in ("opus", "aac", "mp3", "wav"):
        return "opus"
    return c


def mix_to_mono(audio):
    if audio.channels <= 1:
        return audio.set_channels(1)
    return audio.set_channels(1)


def _norm_channels(channels: str) -> str:
    c = (channels or "mono").strip().lower()
    return c if c in ("mono", "stereo") else "mono"


def _norm_bitrate(bitrate_kbit) -> int:
    try:
        bitrate = int(bitrate_kbit)
    except (TypeError, ValueError):
        bitrate = 24
    if bitrate not in BITRATE_CHOICES:
        bitrate = min(BITRATE_CHOICES, key=lambda b: abs(b - bitrate))
    return bitrate


def encode_capture(
    src_wav: str,
    dest_path: str,
    codec: str = "opus",
    bitrate_kbit: int = 24,
    channels: str = "mono",
    log_func=None,
) -> Tuple[str, Optional[str]]:
    """Encode src_wav → dest_path. Returns (final_path, error_or_None).

    Tries pydub, then FFmpeg CLI. On codec failure falls back to WAV.
    """
    codec = normalize_codec(codec)
    channels = _norm_channels(channels)
    bitrate = _norm_bitrate(bitrate_kbit)

    if not src_wav or not os.path.isfile(src_wav):
        return dest_path, "missing source wav"

    parent = os.path.dirname(dest_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    pydub_err = None
    try:
        from pydub import AudioSegment

        audio = AudioSegment.from_file(src_wav)
        if channels == "mono":
            audio = mix_to_mono(audio)
        elif audio.channels < 2:
            audio = audio.set_channels(2)
        audio.export(dest_path, **_export_params(codec, bitrate))
        if os.path.isfile(dest_path) and os.path.getsize(dest_path) > 0:
            return dest_path, None
        pydub_err = "empty encode"
    except Exception as e:
        pydub_err = str(e)
        if log_func:
            try:
                log_func(str(e))
            except Exception:
                pass

    ff_err = _ffmpeg_export(src_wav, dest_path, codec, bitrate, channels)
    if ff_err is None and os.path.isfile(dest_path) and os.path.getsize(dest_path) > 0:
        return dest_path, None

    fallback = os.path.splitext(dest_path)[0] + ".wav"
    err = ff_err or pydub_err or "empty encode"
    copied = _copy_wav_fallback(src_wav, fallback, channels)
    return copied, err


def _ffmpeg_export(src: str, dest: str, codec: str, bitrate: int, channels: str) -> Optional[str]:
    """Return None on success, error string otherwise."""
    try:
        from whisperfast.setup.external_tools import find_tool_exe
    except Exception as e:
        return str(e)
    exe = find_tool_exe("ffmpeg")
    if not exe:
        return "ffmpeg not found"
    ac = _FFMPEG_CODECS.get(codec, "libopus")
    cmd = [exe, "-y", "-i", src]
    cmd += ["-ac", "1" if channels == "mono" else "2"]
    if codec != "wav":
        cmd += ["-b:a", f"{int(bitrate)}k"]
    cmd += ["-c:a", ac, dest]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            **win_no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return str(e)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip().splitlines()
        return err[-1] if err else f"ffmpeg exit {result.returncode}"
    return None


def _export_params(codec: str, bitrate: int) -> dict:
    br = f"{int(bitrate)}k"
    if codec == "opus":
        return {"format": "opus", "bitrate": br, "parameters": ["-c:a", "libopus"]}
    if codec == "aac":
        return {"format": "ipod", "bitrate": br, "parameters": ["-c:a", "aac"]}
    if codec == "mp3":
        return {"format": "mp3", "bitrate": br}
    return {"format": "wav"}


def _copy_wav_fallback(src: str, dest: str, channels: str) -> str:
    dest = os.path.splitext(dest)[0] + ".wav"
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if channels == "mono":
        try:
            from pydub import AudioSegment

            audio = AudioSegment.from_file(src)
            mix_to_mono(audio).export(dest, format="wav")
            return dest
        except Exception:
            pass
    if os.path.normcase(os.path.abspath(src)) != os.path.normcase(os.path.abspath(dest)):
        import shutil

        shutil.copy2(src, dest)
    return dest


def encode_to_dir(
    src_wav: str,
    dest_dir: str,
    stem: str,
    codec: str = "opus",
    bitrate_kbit: int = 24,
    channels: str = "mono",
    log_func=None,
) -> Tuple[str, Optional[str]]:
    ext = codec_extension(normalize_codec(codec))
    dest = os.path.join(dest_dir, stem + ext)
    return encode_capture(
        src_wav,
        dest,
        codec=codec,
        bitrate_kbit=bitrate_kbit,
        channels=channels,
        log_func=log_func,
    )
