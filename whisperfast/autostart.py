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
_LEGACY_LNK_NAMES = frozenset(
    {
        "whisper fast gui (delayed).lnk",
        "whisper fast gui delayed.lnk",
        "whisperfastgui delayed.lnk",
        "whisperfastgui (delayed).lnk",
        "whisper fast gui.lnk",
        "whisperfastgui.lnk",
    }
)


def default_startup_dir() -> str:
    appdata = os.environ.get("APPDATA") or ""
    return os.path.join(appdata, _STARTUP_TAIL) if appdata else ""


def shortcut_path(startup_dir: str | None = None) -> str:
    folder = startup_dir if startup_dir is not None else default_startup_dir()
    return os.path.join(folder, LNK_NAME) if folder else ""


def vbs_path(base_dir: str | None = None) -> str:
    return os.path.join(base_dir if base_dir is not None else BASE_DIR, VBS_NAME)


def _contains_marker(data: bytes, text: str) -> bool:
    folded = data.lower()
    plain = text.casefold().encode("ascii", "ignore")
    wide = text.casefold().encode("utf-16le")
    return (plain and plain in folded) or (wide and wide in folded)


def shortcut_is_ours(path: str, base_dir: str | None = None) -> bool:
    """True for the current shortcut, a known old name, or a link to this install."""
    name = os.path.basename(path).casefold()
    if name == LNK_NAME.casefold() or name in _LEGACY_LNK_NAMES:
        return True
    if not name.endswith(".lnk"):
        return False
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return False
    if any(_contains_marker(data, marker) for marker in _RUN_COMMAND_MARKERS):
        return True
    root = os.path.abspath(base_dir if base_dir is not None else BASE_DIR).rstrip("\\/")
    looks_named = "ftw" in name or "whisper" in name
    return bool(looks_named and len(root) >= 8 and _contains_marker(data, root))


def our_shortcut_paths(startup_dir: str | None = None, base_dir: str | None = None) -> list[str]:
    folder = startup_dir if startup_dir is not None else default_startup_dir()
    if not folder or not os.path.isdir(folder):
        return []
    found: list[str] = []
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    for name in names:
        path = os.path.join(folder, name)
        if os.path.isfile(path) and shortcut_is_ours(path, base_dir):
            found.append(path)
    return found


def _shortcut_to_keep(paths: list[str]) -> str:
    for path in paths:
        if os.path.basename(path).casefold() == LNK_NAME.casefold():
            return path
    return max(paths, key=lambda path: os.path.getmtime(path))


def dedupe_shortcuts(startup_dir: str | None = None, base_dir: str | None = None) -> list[str]:
    """Leave one FTW Startup shortcut. Prefer FTW delayed.lnk when it is present."""
    paths = our_shortcut_paths(startup_dir, base_dir)
    if len(paths) <= 1:
        return []
    keep = os.path.normcase(_shortcut_to_keep(paths))
    removed: list[str] = []
    for path in paths:
        if os.path.normcase(path) == keep:
            continue
        try:
            os.remove(path)
        except FileNotFoundError:
            continue
        except OSError:
            continue
        removed.append(os.path.basename(path))
    return removed


_APPROVED_FOLDER_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder"


def _is_live_startup(startup_dir: str | None) -> bool:
    real = default_startup_dir()
    if not real:
        return False
    if startup_dir is None:
        return True
    return os.path.normcase(startup_dir) == os.path.normcase(real)


def _approval_name_is_ours(name: str) -> bool:
    folded = (name or "").casefold()
    if folded == LNK_NAME.casefold() or folded in _LEGACY_LNK_NAMES:
        return True
    return folded.endswith(".lnk") and ("ftw" in folded or "whisper" in folded)


def clear_stale_startup_approvals(
    startup_dir: str | None = None, base_dir: str | None = None
) -> list[str]:
    """Drop Startup approvals for FTW shortcuts that are no longer on disk."""
    if sys.platform != "win32" or not _is_live_startup(startup_dir):
        return []
    import winreg

    kept = {os.path.basename(path).casefold() for path in our_shortcut_paths(startup_dir, base_dir)}
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            _APPROVED_FOLDER_KEY,
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
                name, _data, _typ = winreg.EnumValue(key, index)
            except OSError:
                break
            index += 1
            if name.casefold() in kept or not _approval_name_is_ours(name):
                continue
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


def _task_identity(task: dict) -> tuple[str, str]:
    return ((task.get("path") or "\\").casefold(), (task.get("name") or "").casefold())


def _task_is_ours(task: dict, base_dir: str) -> bool:
    blob = "{0} {1}".format(task.get("execute") or "", task.get("arguments") or "")
    return _run_value_is_ours(str(task.get("name") or ""), blob, base_dir)


def _task_to_keep(tasks: list[dict]) -> dict:
    preferred = {"ftw delayed", "ftw", "whisperfastgui delayed", "whisperfastgui"}
    for task in tasks:
        if (task.get("name") or "").strip().casefold() in preferred:
            return task
    return sorted(tasks, key=lambda task: _task_identity(task))[0]


def _query_scheduled_tasks() -> list[dict]:
    if sys.platform != "win32":
        return []
    script = (
        "Get-ScheduledTask | ForEach-Object { $t=$_; foreach ($a in @($t.Actions)) { "
        "[PSCustomObject]@{ Name=$t.TaskName; Path=$t.TaskPath; "
        "Execute=[string]$a.Execute; Arguments=[string]$a.Arguments } } } | "
        "ConvertTo-Json -Compress"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            **win_no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    raw = (proc.stdout or "").strip()
    if proc.returncode != 0 or not raw:
        return []
    import json

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _unregister_scheduled_task(task: dict) -> bool:
    name = str(task.get("name") or "").replace("'", "''")
    path = str(task.get("path") or "\\").replace("'", "''")
    if not name:
        return False
    script = (
        "Unregister-ScheduledTask -TaskName '{0}' -TaskPath '{1}' -Confirm:$false"
    ).format(name, path)
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            **win_no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def dedupe_scheduled_tasks(
    base_dir: str | None = None,
    tasks: list[dict] | None = None,
    unregister=None,
    keep_one: bool = True,
) -> list[str]:
    """Leave one scheduled task that starts this install. Other programs stay."""
    root = os.path.abspath(base_dir if base_dir is not None else BASE_DIR)
    found = list(tasks) if tasks is not None else _query_scheduled_tasks()
    ours = [task for task in found if _task_is_ours(task, root)]
    if len(ours) <= 1 and keep_one:
        return []
    keep = _task_identity(_task_to_keep(ours)) if keep_one and ours else None
    drop = unregister or _unregister_scheduled_task
    removed: list[str] = []
    for task in ours:
        if keep is not None and _task_identity(task) == keep:
            continue
        if not drop(task):
            continue
        removed.append(str(task.get("name") or ""))
    return removed


def cleanup_extra_autostart(startup_dir: str | None = None, base_dir: str | None = None) -> list[str]:
    """One Startup shortcut and one scheduled task for this install."""
    removed = dedupe_shortcuts(startup_dir, base_dir)
    removed.extend(clear_stale_startup_approvals(startup_dir, base_dir))
    removed.extend(dedupe_scheduled_tasks(base_dir))
    return removed


def is_enabled(startup_dir: str | None = None, base_dir: str | None = None) -> bool:
    return bool(our_shortcut_paths(startup_dir, base_dir))


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
    for path in our_shortcut_paths(startup_dir, base_dir):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError:
            continue
    if _is_live_startup(startup_dir):
        clear_stale_startup_approvals(startup_dir, base_dir)
        dedupe_scheduled_tasks(base_dir, keep_one=False)
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
        dedupe_shortcuts(folder, root)
        if _is_live_startup(folder):
            clear_stale_startup_approvals(folder, root)
            dedupe_scheduled_tasks(root)
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
    dedupe_shortcuts(folder, root)
    if _is_live_startup(folder):
        clear_stale_startup_approvals(folder, root)
        dedupe_scheduled_tasks(root)
    return clear_registry_run_entries(root)
