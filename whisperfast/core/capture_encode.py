"""Encode capture PCM WAV to opus / aac / mp3 / wav after Stop or clip."""
from __future__ import annotations

import os
from typing import Optional, Tuple

from whisperfast.core.capture_prefs import codec_extension

BITRATE_CHOICES = (16, 24, 32, 48, 64, 96, 128)


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


def encode_capture(
    src_wav: str,
    dest_path: str,
    codec: str = "opus",
    bitrate_kbit: int = 24,
    channels: str = "mono",
    log_func=None,
) -> Tuple[str, Optional[str]]:
    """Encode src_wav → dest_path. Returns (final_path, error_or_None).

    On codec failure falls back to WAV (mono mix still applied).
    """
    codec = normalize_codec(codec)
    channels = (channels or "mono").strip().lower()
    if channels not in ("mono", "stereo"):
        channels = "mono"
    try:
        bitrate = int(bitrate_kbit)
    except (TypeError, ValueError):
        bitrate = 24
    if bitrate not in BITRATE_CHOICES:
        bitrate = min(BITRATE_CHOICES, key=lambda b: abs(b - bitrate))

    if not src_wav or not os.path.isfile(src_wav):
        return dest_path, "missing source wav"

    try:
        from pydub import AudioSegment
    except ImportError as e:
        return _copy_wav_fallback(src_wav, dest_path, channels), str(e)

    try:
        audio = AudioSegment.from_file(src_wav)
    except Exception as e:
        return _copy_wav_fallback(src_wav, dest_path, channels), str(e)

    if channels == "mono":
        audio = mix_to_mono(audio)
    elif audio.channels < 2:
        audio = audio.set_channels(2)

    parent = os.path.dirname(dest_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    params = _export_params(codec, bitrate)
    try:
        audio.export(dest_path, **params)
        if os.path.isfile(dest_path) and os.path.getsize(dest_path) > 0:
            return dest_path, None
    except Exception as e:
        if log_func:
            try:
                log_func(str(e))
            except Exception:
                pass
        fallback = os.path.splitext(dest_path)[0] + ".wav"
        try:
            audio.export(fallback, format="wav")
            return fallback, str(e)
        except Exception as e2:
            copied = _copy_wav_fallback(src_wav, fallback, channels)
            return copied, str(e2)
    fallback = os.path.splitext(dest_path)[0] + ".wav"
    copied = _copy_wav_fallback(src_wav, fallback, channels)
    return copied, "empty encode"


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
