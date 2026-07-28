"""Cross-platform helpers for subprocess calls."""
import subprocess
import sys


def win_no_window_kwargs():
    """Kwargs so subprocess does not flash a console window on Windows.

    CREATE_NO_WINDOW covers console apps (node.exe, cmd). STARTUPINFO/SW_HIDE
    helps when a .cmd/.bat still goes through a short-lived shell.
    """
    if sys.platform != "win32":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0  # SW_HIDE
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        "startupinfo": startupinfo,
    }
