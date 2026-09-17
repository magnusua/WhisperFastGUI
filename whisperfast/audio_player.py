"""Play a time range from a media file (click-a-line in the archive).

Prefers ffplay (ships with FFmpeg). Falls back to extracting a WAV clip with
ffmpeg/pydub and playing it via pygame.mixer — the same mixer used for the
finish chime, so playback is serialized.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from typing import Optional

from whisperfast.platform_util import win_no_window_kwargs

_lock = threading.Lock()
_proc: Optional[subprocess.Popen] = None
_tmp_files: list = []


def stop() -> None:
    """Stop current playback if any."""
    global _proc
    with _lock:
        proc = _proc
        _proc = None
        if proc is not None:
            try:
                proc.terminate()
            except OSError:
                pass
        try:
            import pygame

            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
        except Exception:
            pass
        while _tmp_files:
            path = _tmp_files.pop()
            try:
                os.unlink(path)
            except OSError:
                pass


def play_range(path: str, start_s: float, duration_s: float = 3.0) -> bool:
    """Play [start_s, start_s+duration_s] from path. Returns False if it could not start."""
    if not path or not os.path.isfile(path):
        return False
    start_s = max(0.0, float(start_s or 0.0))
    duration_s = max(0.4, float(duration_s or 0.4))
    stop()
    if _play_ffplay(path, start_s, duration_s):
        return True
    return _play_clip(path, start_s, duration_s)


def resolve_audio_path(job: dict) -> str:
    """Best audio to play for a library job: mp3, else original source."""
    for key in ("mp3_path", "source"):
        p = (job or {}).get(key) or ""
        if p and os.path.isfile(p):
            return p
    return ""


def _play_ffplay(path: str, start_s: float, duration_s: float) -> bool:
    global _proc
    try:
        kwargs = dict(win_no_window_kwargs())
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
        proc = subprocess.Popen(
            [
                "ffplay",
                "-nodisp",
                "-autoexit",
                "-loglevel",
                "quiet",
                "-ss",
                f"{start_s:.3f}",
                "-t",
                f"{duration_s:.3f}",
                path,
            ],
            **kwargs,
        )
    except (FileNotFoundError, OSError):
        return False
    with _lock:
        _proc = proc
    return True


def _play_clip(path: str, start_s: float, duration_s: float) -> bool:
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    wav = tmp.name
    ok = False
    try:
        kwargs = dict(win_no_window_kwargs())
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
        r = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{start_s:.3f}",
                "-t",
                f"{duration_s:.3f}",
                "-i",
                path,
                "-vn",
                "-acodec",
                "pcm_s16le",
                wav,
            ],
            timeout=60,
            **kwargs,
        )
        ok = r.returncode == 0 and os.path.isfile(wav) and os.path.getsize(wav) > 44
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        ok = False
    if not ok:
        try:
            from pydub import AudioSegment

            clip = AudioSegment.from_file(path)[
                int(start_s * 1000) : int((start_s + duration_s) * 1000)
            ]
            clip.export(wav, format="wav")
            ok = os.path.isfile(wav)
        except Exception:
            try:
                os.unlink(wav)
            except OSError:
                pass
            return False
    try:
        import pygame

        if not pygame.mixer.get_init():
            pygame.mixer.init()
        pygame.mixer.music.load(wav)
        pygame.mixer.music.play()
        with _lock:
            _tmp_files.append(wav)
        return True
    except Exception:
        try:
            os.unlink(wav)
        except OSError:
            pass
        return False
