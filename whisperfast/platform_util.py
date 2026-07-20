"""Cross-platform helpers for subprocess calls."""
import subprocess
import sys


def win_no_window_kwargs():
    """Kwargs so subprocess does not flash a console window on Windows."""
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}
