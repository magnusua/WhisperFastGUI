import os
import sys
import glob
import subprocess
import pygame

def format_timestamp(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

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
        mp3 = glob.glob("*.mp3")
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