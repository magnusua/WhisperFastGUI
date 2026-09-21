"""Open a file or reveal it in the system file manager."""
import os
import shutil
import subprocess
import sys
from typing import List, Optional, Tuple

from whisperfast.platform_util import win_no_window_kwargs


def open_file(path: str) -> bool:
    """Open path with the default application. Returns True if launched."""
    if not path or not os.path.exists(path):
        return False
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", path], check=False)
    else:
        subprocess.run(["xdg-open", path], check=False)
    return True


def waitable_editor_command(path: str) -> Tuple[Optional[List[str]], dict]:
    """Command that opens ``path`` and blocks until the editor/tab closes."""
    abs_path = os.path.abspath(path)
    if sys.platform == "win32":
        cursor = _find_cursor_cli()
        if cursor:
            kwargs = win_no_window_kwargs() if cursor.lower().endswith((".cmd", ".bat")) else {}
            return [cursor, "--wait", abs_path], kwargs
        code = shutil.which("code.cmd") or shutil.which("code")
        if code:
            kwargs = win_no_window_kwargs() if code.lower().endswith((".cmd", ".bat")) else {}
            return [code, "--wait", abs_path], kwargs
        notepad = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "notepad.exe")
        if os.path.isfile(notepad):
            return [notepad, abs_path], {}
        return None, {}
    if sys.platform == "darwin":
        return ["open", "-W", "-t", abs_path], {}
    for name, extra in (("cursor", ["--wait"]), ("code", ["--wait"]), ("gedit", ["--wait"])):
        found = shutil.which(name)
        if found:
            return [found, *extra, abs_path], {}
    return None, {}


def open_file_and_wait(path: str) -> bool:
    """Open ``path`` and block until the editor process (or tab) closes.

    Returns False if no waitable editor is available (does not fire-and-forget).
    """
    if not path or not os.path.exists(path):
        return False
    cmd, kwargs = waitable_editor_command(path)
    if not cmd:
        return False
    try:
        subprocess.run(cmd, check=False, **kwargs)
        return True
    except OSError:
        return False


def _find_cursor_cli() -> Optional[str]:
    for name in ("cursor.cmd", "cursor.exe", "cursor"):
        found = shutil.which(name)
        if found:
            return found
    if sys.platform != "win32":
        return None
    bundled = os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "Programs",
        "cursor",
        "resources",
        "app",
        "bin",
        "cursor.cmd",
    )
    return bundled if os.path.isfile(bundled) else None


def open_file_location(path: str) -> bool:
    """Reveal path in the file manager (select file when supported).

    Important (Windows): do NOT use CREATE_NO_WINDOW / SW_HIDE — that hides Explorer.
    Prefer ShellExecuteW — list-form ``explorer /select,`` is unreliable on Win10/11.
    """
    if not path:
        return False
    path = os.path.abspath(os.path.normpath(path.strip().strip('"')))
    if not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent and os.path.isdir(parent):
            path = parent
        else:
            return False

    if sys.platform == "win32":
        return _win_reveal_in_explorer(path)

    if sys.platform == "darwin":
        if os.path.isdir(path):
            subprocess.run(["open", path], check=False)
        else:
            subprocess.run(["open", "-R", path], check=False)
        return True

    target = path if os.path.isdir(path) else (os.path.dirname(path) or path)
    subprocess.run(["xdg-open", target], check=False)
    return True


def _win_reveal_in_explorer(path: str) -> bool:
    """Show Explorer and select ``path`` (or open folder). Visible window, SW_SHOWNORMAL."""
    import ctypes

    SW_SHOWNORMAL = 1
    shell32 = ctypes.windll.shell32
    if os.path.isdir(path):
        rc = shell32.ShellExecuteW(None, "open", path, None, None, SW_SHOWNORMAL)
    else:
        # One argument string: /select,"C:\path\file.ext"
        params = f'/select,"{path}"'
        rc = shell32.ShellExecuteW(
            None, "open", "explorer.exe", params, None, SW_SHOWNORMAL
        )
    # ShellExecute success: return value > 32
    if int(rc) > 32:
        return True
    # Fallback if ShellExecute fails
    try:
        if os.path.isdir(path):
            subprocess.Popen(["explorer", path])
        else:
            subprocess.Popen(f'explorer /select,"{path}"', shell=True)
        return True
    except OSError:
        return False
