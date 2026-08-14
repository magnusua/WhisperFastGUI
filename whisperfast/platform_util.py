"""Cross-platform helpers for subprocess calls."""
import os
import subprocess
import sys
import time


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


def run_logged_command(cmd, log_func=None, timeout=600, line_filter=None, env=None):
    """Run a command, optionally streaming stdout/stderr lines to log_func.

    Returns the process return code (or -1 on timeout / spawn failure).
    line_filter(line) -> bool: if given, only matching lines are logged.
    """
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    merged_env.setdefault("PYTHONUNBUFFERED", "1")
    merged_env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=merged_env,
            **win_no_window_kwargs(),
        )
    except OSError as e:
        if log_func:
            log_func(str(e))
        return -1

    start = time.monotonic()
    try:
        assert proc.stdout is not None
        for raw in iter(proc.stdout.readline, ""):
            line = raw.rstrip("\r\n")
            if line and log_func and (line_filter is None or line_filter(line)):
                log_func(line)
            if timeout and (time.monotonic() - start) > timeout:
                proc.kill()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
                return -1
        return proc.wait(timeout=30)
    except Exception:
        try:
            proc.kill()
        except OSError:
            pass
        raise
