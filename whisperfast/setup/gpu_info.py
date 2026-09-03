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


def gpu_model_looks_nvidia(name):
    """True if settings.gpu_model already names an NVIDIA card (no hardware probe)."""
    return "nvidia" in (name or "").strip().lower()


def nvidia_from_settings():
    """
    Trust settings.json when gpu_model contains 'NVIDIA'.
    Returns (True, name) or (False, name_or_empty). Does not probe hardware.
    """
    name = (load_app_settings().get("gpu_model") or "").strip()
    if gpu_model_looks_nvidia(name):
        return True, name
    return False, name


def install_gpu_status_line():
    """One-line status for install.bat: SAVED:/FOUND:/NOTFOUND. Skips probe if settings name NVIDIA."""
    saved, name = nvidia_from_settings()
    if saved:
        return "SAVED:" + name
    has, live = detect_nvidia_gpu()
    if has:
        return "FOUND:" + ((live or "").strip() or "NVIDIA")
    return "NOTFOUND"


def nvidia_for_install():
    """Install path: trust settings.gpu_model if it names NVIDIA; otherwise probe."""
    saved, name = nvidia_from_settings()
    if saved:
        save_app_settings({"has_nvidia": True, "gpu_model": name})
        return True, name
    return refresh_gpu_settings()


def refresh_gpu_settings():
    """
    Оновлює has_nvidia та gpu_model у settings.json. Повертає (has_nvidia, gpu_name).
    If gpu_model already names NVIDIA, a failed probe does not clear it.
    """
    saved, saved_name = nvidia_from_settings()
    has_nvidia, name = detect_nvidia_gpu()
    live = (name or "").strip()
    if has_nvidia:
        final = live or saved_name or "NVIDIA"
        save_app_settings({"has_nvidia": True, "gpu_model": final})
        return True, final
    if saved:
        save_app_settings({"has_nvidia": True, "gpu_model": saved_name})
        return True, saved_name
    save_app_settings({"has_nvidia": False, "gpu_model": live})
    return False, name


def get_saved_gpu_settings():
    """Читає збережені значення з settings.json."""
    data = load_app_settings()
    return bool(data.get("has_nvidia")), (data.get("gpu_model") or "").strip()
