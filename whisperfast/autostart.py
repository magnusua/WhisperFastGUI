"""Windows Startup shortcut for delayed FTW launch (FTW delayed.lnk)."""
from __future__ import annotations

import os
import subprocess
import sys

from whisperfast.config import BASE_DIR
from whisperfast.platform_util import win_no_window_kwargs

LNK_NAME = "FTW delayed.lnk"
VBS_NAME = "start_delayed.vbs"
_STARTUP_TAIL = os.path.join("Microsoft", "Windows", "Start Menu", "Programs", "Startup")
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE_NAMES = frozenset({"ftw", "whisperfastgui", "ftw delayed", "whisperfastgui delayed"})
_RUN_COMMAND_MARKERS = ("start_delayed.vbs", "run_whisper.vbs")


def default_startup_dir() -> str:
    appdata = os.environ.get("APPDATA") or ""
    return os.path.join(appdata, _STARTUP_TAIL) if appdata else ""


def shortcut_path(startup_dir: str | None = None) -> str:
    folder = startup_dir if startup_dir is not None else default_startup_dir()
    return os.path.join(folder, LNK_NAME) if folder else ""


def vbs_path(base_dir: str | None = None) -> str:
    return os.path.join(base_dir if base_dir is not None else BASE_DIR, VBS_NAME)


def is_enabled(startup_dir: str | None = None) -> bool:
    path = shortcut_path(startup_dir)
    return bool(path) and os.path.isfile(path)


def _run_value_is_ours(name: str, data: str, base_dir: str) -> bool:
    """True if an HKCU Run value would start this install alongside the Startup shortcut."""
    if (name or "").strip().lower() in _RUN_VALUE_NAMES:
        return True
    text = (data or "").lower().replace("/", "\\")
    root = os.path.abspath(base_dir).lower().replace("/", "\\").rstrip("\\")
    if root and root in text:
        return True
    return any(marker in text for marker in _RUN_COMMAND_MARKERS)


def clear_registry_run_entries(base_dir: str | None = None) -> list[str]:
    """Delete this app's values from HKCU Run. Other entries are left in place."""
    if sys.platform != "win32":
        return []
    import winreg

    root = os.path.abspath(base_dir if base_dir is not None else BASE_DIR)
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            _RUN_KEY,
            0,
            winreg.KEY_READ | winreg.KEY_SET_VALUE,
        )
    except OSError:
        return []
    removed: list[str] = []
    try:
        pending: list[str] = []
        index = 0
        while True:
            try:
                name, data, typ = winreg.EnumValue(key, index)
            except OSError:
                break
            index += 1
            if typ not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
                continue
            if _run_value_is_ours(name, str(data), root):
                pending.append(name)
        for name in pending:
            try:
                winreg.DeleteValue(key, name)
            except OSError:
                continue
            removed.append(name)
    finally:
        winreg.CloseKey(key)
    return removed


def disable(startup_dir: str | None = None, base_dir: str | None = None) -> list[str]:
    path = shortcut_path(startup_dir)
    if path:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    return clear_registry_run_entries(base_dir)


def enable(startup_dir: str | None = None, base_dir: str | None = None) -> list[str]:
    root = os.path.abspath(base_dir if base_dir is not None else BASE_DIR)
    vbs = vbs_path(root)
    if not os.path.isfile(vbs):
        raise FileNotFoundError(vbs)
    folder = startup_dir if startup_dir is not None else default_startup_dir()
    if not folder:
        raise OSError("Windows Startup folder is unknown (APPDATA is empty)")
    os.makedirs(folder, exist_ok=True)
    lnk = os.path.join(folder, LNK_NAME)
    script_dir = root.rstrip("\\/")
    if sys.platform != "win32":
        with open(lnk, "w", encoding="utf-8") as f:
            f.write(vbs)
        return clear_registry_run_entries(root)
    env = os.environ.copy()
    env["LNK_PATH"] = lnk
    env["VBS_PATH"] = vbs
    env["SCRIPT_DIR"] = script_dir
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        "$w=New-Object -ComObject WScript.Shell;"
        "$s=$w.CreateShortcut($env:LNK_PATH);"
        "$s.TargetPath=$env:VBS_PATH;"
        "$s.WorkingDirectory=$env:SCRIPT_DIR;"
        "$s.Description='FTW - start with 25s delay';"
        "$s.Save()",
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        **win_no_window_kwargs(),
    )
    if proc.returncode != 0 or not os.path.isfile(lnk):
        detail = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise OSError(detail)
    return clear_registry_run_entries(root)
