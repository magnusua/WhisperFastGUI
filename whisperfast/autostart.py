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


def disable(startup_dir: str | None = None) -> None:
    path = shortcut_path(startup_dir)
    if not path:
        return
    try:
        os.remove(path)
    except FileNotFoundError:
        return


def enable(startup_dir: str | None = None, base_dir: str | None = None) -> None:
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
        return
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
