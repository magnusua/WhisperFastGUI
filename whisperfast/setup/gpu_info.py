"""Визначення NVIDIA GPU та збереження моделі в settings.json."""
import subprocess
import sys

from whisperfast.settings import load_app_settings, save_app_settings
from whisperfast.platform_util import win_no_window_kwargs




def detect_nvidia_gpu():
    """
    Повертає (has_nvidia, gpu_name).
    gpu_name — рядок з nvidia-smi / torch або None.
    """
    try:
        import torch
        if torch.cuda.is_available():
            return True, torch.cuda.get_device_name(0)
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=8,
            **win_no_window_kwargs(),
        )
        if result.returncode == 0:
            line = (result.stdout or "").strip().splitlines()
            if line and line[0].strip():
                return True, line[0].strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    if sys.platform == "win32":
        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "(Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match 'NVIDIA' } | Select-Object -First 1 -ExpandProperty Name)",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                **win_no_window_kwargs(),
            )
            if result.returncode == 0:
                name = (result.stdout or "").strip()
                if name:
                    return True, name
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

    return False, None


def refresh_gpu_settings():
    """Оновлює has_nvidia та gpu_model у settings.json. Повертає (has_nvidia, gpu_name)."""
    has_nvidia, name = detect_nvidia_gpu()
    save_app_settings({
        "has_nvidia": has_nvidia,
        "gpu_model": (name or "").strip(),
    })
    return has_nvidia, name


def get_saved_gpu_settings():
    """Читає збережені значення з settings.json."""
    data = load_app_settings()
    return bool(data.get("has_nvidia")), (data.get("gpu_model") or "").strip()
