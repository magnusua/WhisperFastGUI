import os
import sys
import glob
import subprocess
import pygame

from whisperfast.config import BASE_DIR, DEFAULT_START_TIMESTAMP, RESOURCES_DIR
from whisperfast.platform_util import win_no_window_kwargs

def format_timestamp(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def format_timestamp_srt(seconds):
    """Формат времени для SRT: HH:MM:SS.mmm (точка вместо запятой)."""
    return format_timestamp(seconds).replace(",", ".")


def format_timestamp_filename(seconds):
    """Формат для имени файла (суффикс отрезка): HH-MM-SS, без миллисекунд."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}-{m:02d}-{s:02d}"


def normalize_display_path(path):
    """
    Приводит путь к виду, принятому на текущей ОС (\\ на Windows, / на Linux/macOS).
    Пустая строка возвращается без изменений.
    """
    if not path or not isinstance(path, str):
        return ""
    path = path.strip()
    if not path:
        return ""
    return os.path.normpath(path)


def normalize_queue_path(path):
    """
    Нормализует путь из элемента очереди (может быть строкой или list/tuple из JSON).
    Возвращает os.path.normpath(str) или None, если путь пустой или невалидный.
    """
    if path is None:
        return None
    if isinstance(path, (list, tuple)):
        path = path[0] if len(path) > 0 else None
    if not path or not isinstance(path, str):
        return None
    path = str(path).strip()
    if not path:
        return None
    return os.path.normpath(path)


def make_queue_item(path, **overrides):
    """
    Элемент очереди (dict) с полями path, start, end_segment_1, end_segment_2, end, processed.
    path должен быть уже нормализованной строкой. overrides подставляются поверх умолчаний.
    """
    duration = get_audio_duration_seconds(path) or 0.0
    end_ts = format_timestamp(duration) if duration > 0 else DEFAULT_START_TIMESTAMP
    item = {
        "path": path,
        "start": DEFAULT_START_TIMESTAMP,
        "end_segment_1": "",
        "end_segment_2": "",
        "end": end_ts,
        "processed": False,
    }
    item.update(overrides)
    return item


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
        kwargs = {"capture_output": True, "text": True, "timeout": 30, **win_no_window_kwargs()}
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
    except Exception:
        return 0.0


def play_finish_sound():
    try:
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        mp3 = glob.glob(os.path.join(RESOURCES_DIR, "*.mp3"))
        if not mp3:
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
    except Exception:
        pass