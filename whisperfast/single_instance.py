"""Single-instance guard: PID lock file + startup dialog."""
from __future__ import annotations

import atexit
import json
import os
import signal
import sys
import time
from typing import Any, Dict, Optional

from whisperfast.config import BASE_DIR

LOCK_FILENAME = ".whisperfastgui.pid"
_owned = False


def lock_path() -> str:
    return os.path.join(BASE_DIR, LOCK_FILENAME)


def _read_lock() -> Optional[Dict[str, Any]]:
    path = lock_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        pid = int(data.get("pid") or 0)
        if pid <= 0:
            return None
        return data
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _write_lock(pid: int) -> None:
    path = lock_path()
    payload = {
        "pid": int(pid),
        "base_dir": BASE_DIR,
        "ts": time.time(),
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
        )
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        # Access denied usually means the process exists
        err = ctypes.windll.kernel32.GetLastError()
        return err == 5
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate_pid(pid: int) -> bool:
    """Force-stop process ``pid``. Returns True if terminate was issued."""
    if pid <= 0 or pid == os.getpid():
        return False
    if sys.platform == "win32":
        import ctypes

        PROCESS_TERMINATE = 0x0001
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE, False, int(pid))
        if not handle:
            return False
        try:
            ok = bool(ctypes.windll.kernel32.TerminateProcess(handle, 1))
            return ok
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(int(pid), signal.SIGTERM)
    except OSError:
        return False
    return True


def _wait_until_dead(pid: int, timeout_s: float = 4.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not pid_is_running(pid):
            return True
        time.sleep(0.1)
    if sys.platform != "win32":
        try:
            os.kill(int(pid), signal.SIGKILL)
        except OSError:
            pass
        time.sleep(0.2)
    return not pid_is_running(pid)


def find_other_instance_pid() -> Optional[int]:
    data = _read_lock()
    if not data:
        return None
    pid = int(data.get("pid") or 0)
    if pid <= 0 or pid == os.getpid():
        return None
    if not pid_is_running(pid):
        return None
    # Same install directory (ignore stale locks from other copies)
    locked_base = data.get("base_dir")
    if locked_base:
        try:
            if os.path.normcase(os.path.abspath(locked_base)) != os.path.normcase(
                os.path.abspath(BASE_DIR)
            ):
                return None
        except OSError:
            pass
    return pid


def claim_lock() -> None:
    global _owned
    _write_lock(os.getpid())
    _owned = True


def release_lock_if_owned() -> None:
    global _owned
    if not _owned:
        return
    data = _read_lock()
    try:
        if data and int(data.get("pid") or 0) == os.getpid():
            try:
                os.remove(lock_path())
            except OSError:
                pass
    finally:
        _owned = False


def _ask_already_running(other_pid: int) -> str:
    """Modal dialog. Returns: 'kill' | 'abort' | 'another'."""
    import tkinter as tk
    from tkinter import ttk

    from whisperfast.i18n import t

    result = {"value": "abort"}

    root = tk.Tk()
    root.title(t("single_instance_title"))
    root.resizable(False, False)
    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass

    frame = ttk.Frame(root, padding=16)
    frame.grid(row=0, column=0, sticky="nsew")

    msg = ttk.Label(
        frame,
        text=t("single_instance_message", pid=other_pid),
        wraplength=420,
        justify="left",
    )
    msg.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 14))

    def choose(value: str):
        result["value"] = value
        root.destroy()

    ttk.Button(
        frame,
        text=t("single_instance_kill"),
        command=lambda: choose("kill"),
    ).grid(row=1, column=0, padx=(0, 6), sticky="ew")
    ttk.Button(
        frame,
        text=t("single_instance_abort"),
        command=lambda: choose("abort"),
    ).grid(row=1, column=1, padx=6, sticky="ew")
    ttk.Button(
        frame,
        text=t("single_instance_another"),
        command=lambda: choose("another"),
    ).grid(row=1, column=2, padx=(6, 0), sticky="ew")

    frame.columnconfigure(0, weight=1)
    frame.columnconfigure(1, weight=1)
    frame.columnconfigure(2, weight=1)

    root.update_idletasks()
    w, h = root.winfo_width(), root.winfo_height()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

    root.protocol("WM_DELETE_WINDOW", lambda: choose("abort"))
    root.mainloop()
    try:
        root.destroy()
    except tk.TclError:
        pass
    return result["value"]


def ensure_single_instance() -> str:
    """Guard against parallel launches.

    Returns:
        ``primary`` — this process owns the lock.
        ``secondary`` — user chose to start another instance (lock unchanged).

    Exits the process with code 0 if the user chooses to do nothing.
    """
    atexit.register(release_lock_if_owned)

    other = find_other_instance_pid()
    if other is None:
        claim_lock()
        return "primary"

    choice = _ask_already_running(other)
    if choice == "abort":
        sys.exit(0)
    if choice == "another":
        return "secondary"
    # kill previous
    terminate_pid(other)
    _wait_until_dead(other)
    claim_lock()
    return "primary"
