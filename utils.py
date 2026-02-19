import os
import sys
import glob
import subprocess
import pygame

try:
    from config import BASE_DIR
except ImportError:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def format_timestamp(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def format_timestamp_srt(seconds):
    """Формат времени для SRT: HH:MM:SS.mmm (точка вместо запятой)."""
    return format_timestamp(seconds).replace(",", ".")


def parse_timestamp_to_seconds(s):
    """
    Парсит строку времени в секунды (float).
    Форматы: HH:MM:SS или HH:MM:SS,mmm (запятая/точка для миллисекунд).
    Пустая строка или неверный формат -> None.
    """
    if s is None or not str(s).strip():
        return None
    s = str(s).strip().replace(",", ".")
    parts = s.split(":")
    if len(parts) != 3:
        return None
    try:
        h, m, sec = float(parts[0]), float(parts[1]), float(parts[2])
        if h < 0 or m < 0 or sec < 0 or m >= 60 or sec >= 60:
            return None
        return h * 3600 + m * 60 + sec
    except ValueError:
        return None

def get_audio_duration_seconds(path):
    """
    Возвращает длительность медиафайла в секундах без полной загрузки в память.
    Использует ffprobe (идет с FFmpeg). При ошибке — fallback через pydub.
    """
    try:
        kwargs = {"capture_output": True, "text": True, "timeout": 30}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", path
            ],
            **kwargs,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, OSError):
        pass
    try:
        from pydub import AudioSegment
        return len(AudioSegment.from_file(path)) / 1000.0
    except (ImportError, OSError, Exception):
        return 0.0


def play_finish_sound():
    try:
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        mp3 = glob.glob(os.path.join(BASE_DIR, "*.mp3"))
        if mp3:
            pygame.mixer.music.load(mp3[0])
        elif sys.platform == "win32":
            pygame.mixer.music.load(r"C:\Windows\Media\Alarm03.wav")
        else:
            # Linux/macOS: системный звук или пропуск, если нет локального mp3
            for path in ("/usr/share/sounds/freedesktop/stereo/complete.oga",
                         "/usr/share/sounds/freedesktop/stereo/bell.oga"):
                if os.path.exists(path):
                    pygame.mixer.music.load(path)
                    break
            else:
                return
        pygame.mixer.music.play()
    except (ImportError, OSError, Exception):
        pass