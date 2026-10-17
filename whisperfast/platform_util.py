"""Cross-platform helpers for subprocess calls."""
import os
import subprocess
import sys
import threading
import time


class keep_machine_awake:
    """While a transcription runs, ask Windows not to sleep (screen may still turn off)."""

    def __enter__(self):
        if sys.platform == "win32":
            import ctypes
            # ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001 | 0x00000040)
        return self

    def __exit__(self, exc_type, exc, tb):
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        return False


# WM_QUERYENDSESSION lParam. CLOSEAPP is Restart Manager / OS servicing.
ENDSESSION_CLOSEAPP = 0x1
ENDSESSION_CRITICAL = 0x40000000
ENDSESSION_LOGOFF = 0x80000000

_UPDATE_REBOOT_KEYS = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired",
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending",
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\PackagesPending",
)


def windows_update_reboot_pending() -> bool:
    """True when Windows is waiting to reboot for an operating-system update."""
    import winreg

    for path in _UPDATE_REBOOT_KEYS:
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
        except OSError:
            continue
        key.Close()
        return True
    parent = r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update"
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, parent)
    except OSError:
        return False
    try:
        value, _typ = winreg.QueryValueEx(key, "RebootRequired")
    except OSError:
        return False
    finally:
        key.Close()
    return bool(value)


def shutdown_should_proceed(lparam, update_pending=None) -> bool:
    """True when the listener must not veto this session end.

    A manual shutdown is vetoed. An operating-system update, Restart Manager,
    a forced shutdown, and an automatic reboot that Windows has already marked
    as an update reboot are allowed through.
    """
    flags = int(lparam or 0) & 0xFFFFFFFF
    if flags & (ENDSESSION_CLOSEAPP | ENDSESSION_CRITICAL | ENDSESSION_LOGOFF):
        return True
    if update_pending is None:
        update_pending = windows_update_reboot_pending()
    return bool(update_pending)


class ListenerPowerGuard:
    """While the Telegram listener is on, keep Windows awake and refuse a manual shutdown.

    Idle sleep is held off. A shutdown the user starts is refused until the
    listener stops. A Windows update restart, Restart Manager, and a forced
    reboot are not refused. The monitor may still turn off.
    """

    def __init__(self, reason: str):
        self.reason = (reason or "Telegram listener is on").strip()[:180]
        self._thread = None
        self._ready = threading.Event()
        self._active = False
        self._hwnd = None
        self._request = None
        self._proc = None
        self._reason_buf = None
        self._hinst = None
        self._error = ""

    def active(self) -> bool:
        thread = self._thread
        return bool(self._active and thread is not None and thread.is_alive())

    def start(self) -> bool:
        """Start the guard. False when this is not Windows or the APIs are unavailable."""
        if sys.platform != "win32":
            return False
        if self.active():
            return True
        self._ready.clear()
        self._active = False
        self._thread = threading.Thread(target=self._run, name="ftw-listener-power", daemon=True)
        self._thread.start()
        self._ready.wait(2.0)
        return self._active

    def stop(self) -> None:
        if sys.platform != "win32":
            self._active = False
            return
        hwnd = self._hwnd
        if hwnd:
            try:
                import ctypes
                from ctypes import wintypes

                user32 = ctypes.WinDLL("user32", use_last_error=True)
                user32.PostMessageW.argtypes = [
                    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                ]
                user32.PostMessageW.restype = wintypes.BOOL
                user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
            except Exception:
                pass
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(3)
        self._active = False
        self._thread = None

    def _run(self) -> None:
        try:
            self._active = self._acquire()
        except Exception:
            import traceback

            self._error = traceback.format_exc()
            self._active = False
        finally:
            self._ready.set()
        if not self._active:
            return
        try:
            self._pump()
        finally:
            self._release()
            self._active = False

    def _acquire(self) -> bool:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.SetThreadExecutionState.argtypes = [wintypes.DWORD]
        kernel32.SetThreadExecutionState.restype = wintypes.DWORD
        # ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED
        kernel32.SetThreadExecutionState(0x80000000 | 0x00000001 | 0x00000040)
        self._kernel32 = kernel32
        try:
            powered = self._open_power_request()
        except Exception:
            import traceback

            self._error = traceback.format_exc()
            powered = False
        try:
            window = self._open_block_window()
        except Exception:
            import traceback

            self._error = (self._error + "\n" + traceback.format_exc()).strip()
            window = False
        return bool(powered or window)

    def _open_power_request(self) -> bool:
        import ctypes
        from ctypes import wintypes

        class _Reason(ctypes.Union):
            _fields_ = [("SimpleReasonString", wintypes.LPWSTR)]

        class REASON_CONTEXT(ctypes.Structure):
            _fields_ = [
                ("Version", wintypes.ULONG),
                ("Flags", wintypes.DWORD),
                ("Reason", _Reason),
            ]

        self._reason_buf = ctypes.create_unicode_buffer(self.reason)
        ctx = REASON_CONTEXT()
        ctx.Version = 0
        ctx.Flags = 0x1  # POWER_REQUEST_CONTEXT_SIMPLE_STRING
        ctx.Reason.SimpleReasonString = ctypes.cast(self._reason_buf, wintypes.LPWSTR)
        self._ctx = ctx

        kernel32 = self._kernel32
        kernel32.PowerCreateRequest.argtypes = [ctypes.POINTER(REASON_CONTEXT)]
        kernel32.PowerCreateRequest.restype = wintypes.HANDLE
        kernel32.PowerSetRequest.argtypes = [wintypes.HANDLE, ctypes.c_int]
        kernel32.PowerSetRequest.restype = wintypes.BOOL
        kernel32.PowerClearRequest.argtypes = [wintypes.HANDLE, ctypes.c_int]
        kernel32.PowerClearRequest.restype = wintypes.BOOL
        self._powrprof = kernel32

        handle = kernel32.PowerCreateRequest(ctypes.byref(ctx))
        if not handle:
            return False
        self._request = handle
        # SystemRequired, AwayModeRequired, ExecutionRequired. Not the display.
        ok = False
        for kind in (1, 2, 3):
            if kernel32.PowerSetRequest(handle, kind):
                ok = True
        return ok

    def _open_block_window(self) -> bool:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = self._kernel32
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(
            LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        )

        def _proc(hwnd, msg, wparam, lparam):
            try:
                if msg == 0x0011:  # WM_QUERYENDSESSION
                    if shutdown_should_proceed(lparam):
                        self._clear_shutdown_block(hwnd)
                        return 1
                    self._block_shutdown(hwnd)
                    return 0
                if msg == 0x0016:  # WM_ENDSESSION
                    self._clear_shutdown_block(hwnd)
                    if wparam:
                        self._release_power()
                    return 0
                if msg == 0x0113:  # WM_TIMER
                    self._refresh_awake()
                    return 0
                if msg == 0x0010:  # WM_CLOSE
                    user32.DestroyWindow(hwnd)
                    return 0
                if msg == 0x0002:  # WM_DESTROY
                    user32.PostQuitMessage(0)
                    return 0
                return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
            except Exception:
                return 0

        self._proc = WNDPROC(_proc)
        user32.DefWindowProcW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        ]
        user32.DefWindowProcW.restype = LRESULT
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.DestroyWindow.restype = wintypes.BOOL
        user32.PostQuitMessage.argtypes = [ctypes.c_int]
        user32.PostQuitMessage.restype = None

        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.UINT),
                ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
                ("hIconSm", wintypes.HICON),
            ]

        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
        hinst = kernel32.GetModuleHandleW(None)
        self._hinst = hinst
        class_name = "FTWListenerPowerGuard"
        self._class_name = class_name

        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.lpszClassName = class_name
        user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
        user32.RegisterClassExW.restype = wintypes.ATOM
        atom = user32.RegisterClassExW(ctypes.byref(wc))
        if not atom and kernel32.GetLastError() not in (0, 1410):
            return False

        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        # Hidden top-level window. A block reason is registered only if the user
        # starts a shutdown; an update reboot must not see a standing block.
        hwnd = user32.CreateWindowExW(
            0x00000080 | 0x08000000,  # WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
            class_name,
            "FTW",
            0,
            0, 0, 0, 0,
            None,
            None,
            hinst,
            None,
        )
        if not hwnd:
            return False
        self._hwnd = hwnd
        self._user32 = user32
        self._bind_shutdown_block_api(user32)
        user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
        user32.SetTimer.restype = ctypes.c_size_t
        user32.SetTimer(hwnd, 1, 20000, None)
        return True

    def _bind_shutdown_block_api(self, user32) -> None:
        from ctypes import wintypes

        user32.ShutdownBlockReasonCreate.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
        user32.ShutdownBlockReasonCreate.restype = wintypes.BOOL
        user32.ShutdownBlockReasonDestroy.argtypes = [wintypes.HWND]
        user32.ShutdownBlockReasonDestroy.restype = wintypes.BOOL

    def _clear_shutdown_block(self, hwnd) -> None:
        user32 = getattr(self, "_user32", None)
        if not hwnd or user32 is None:
            return
        try:
            self._bind_shutdown_block_api(user32)
            user32.ShutdownBlockReasonDestroy(hwnd)
        except Exception:
            pass

    def _release_power(self) -> None:
        """Drop the stay-awake request when Windows is actually ending the session."""
        import ctypes
        from ctypes import wintypes

        request = self._request
        self._request = None
        kernel32 = getattr(self, "_powrprof", None) or getattr(self, "_kernel32", None)
        if request and kernel32 is not None:
            for kind in (1, 2, 3):
                try:
                    kernel32.PowerClearRequest(request, kind)
                except Exception:
                    pass
            try:
                kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
                kernel32.CloseHandle.restype = wintypes.BOOL
                kernel32.CloseHandle(request)
            except Exception:
                pass
        if kernel32 is not None:
            try:
                kernel32.SetThreadExecutionState(0x80000000)
            except Exception:
                pass

    def _block_shutdown(self, hwnd) -> bool:
        import ctypes

        user32 = getattr(self, "_user32", None) or ctypes.WinDLL("user32", use_last_error=True)
        self._bind_shutdown_block_api(user32)
        self._user32 = user32
        return bool(user32.ShutdownBlockReasonCreate(hwnd, self.reason))

    def _refresh_awake(self) -> None:
        kernel32 = getattr(self, "_kernel32", None)
        if kernel32 is not None:
            kernel32.SetThreadExecutionState(0x80000000 | 0x00000001 | 0x00000040)
        request = self._request
        powrprof = getattr(self, "_powrprof", None)
        if request and powrprof is not None:
            for kind in (1, 2, 3):
                powrprof.PowerSetRequest(request, kind)

    def _pump(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = self._user32
        user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
        ]
        user32.GetMessageW.restype = ctypes.c_int
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.TranslateMessage.restype = wintypes.BOOL
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype = ctypes.c_ssize_t
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _release(self) -> None:
        import ctypes
        from ctypes import wintypes

        hwnd = self._hwnd
        self._hwnd = None
        user32 = getattr(self, "_user32", None)
        if hwnd and user32 is not None:
            try:
                user32.ShutdownBlockReasonDestroy(hwnd)
            except Exception:
                pass
        request = self._request
        self._request = None
        powrprof = getattr(self, "_powrprof", None)
        if request and powrprof is not None:
            for kind in (1, 2, 3):
                try:
                    powrprof.PowerClearRequest(request, kind)
                except Exception:
                    pass
            closer = ctypes.WinDLL("kernel32", use_last_error=True)
            closer.CloseHandle.argtypes = [wintypes.HANDLE]
            closer.CloseHandle.restype = wintypes.BOOL
            closer.CloseHandle(request)
        kernel32 = getattr(self, "_kernel32", None)
        if kernel32 is not None:
            kernel32.SetThreadExecutionState(0x80000000)
        if user32 is not None and self._hinst is not None:
            try:
                user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
                user32.UnregisterClassW.restype = wintypes.BOOL
                user32.UnregisterClassW(getattr(self, "_class_name", "FTWListenerPowerGuard"), self._hinst)
            except Exception:
                pass


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
