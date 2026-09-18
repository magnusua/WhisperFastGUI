"""Detect call apps (window title + optional pycaw audio session) for auto-record."""
from __future__ import annotations

import os
import sys
from typing import Dict, Iterable, List, Optional, Set, Tuple

PRESETS: Dict[str, dict] = {
    "zoom": {
        "exes": ("zoom.exe", "cpthost.exe"),
        "titles": ("zoom meeting", "zoom"),
        "need_title_if_browser": False,
    },
    "teams": {
        "exes": ("ms-teams.exe", "teams.exe"),
        "titles": ("microsoft teams", "meeting | microsoft teams", "teams"),
        "need_title_if_browser": False,
    },
    "meet": {
        "exes": ("chrome.exe", "msedge.exe", "firefox.exe"),
        "titles": ("meet -", "google meet", "meet.google.com"),
        "need_title_if_browser": True,
    },
    "telegram": {
        "exes": ("telegram.exe",),
        "titles": ("telegram",),
        "need_title_if_browser": False,
    },
    "viber": {
        "exes": ("viber.exe",),
        "titles": ("viber",),
        "need_title_if_browser": False,
    },
    "phone_link": {
        "exes": ("phoneexperiencehost.exe", "yourphone.exe"),
        "titles": ("phone link", "your phone", "связь с телефоном", "зв’язок із телефоном"),
        "need_title_if_browser": False,
    },
    "whatsapp": {
        "exes": ("whatsapp.exe", "chrome.exe", "msedge.exe"),
        "titles": ("whatsapp",),
        "need_title_if_browser": True,
    },
}

_BROWSER_EXES = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe"}


def _tuple_exes(value) -> Tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    return tuple(value or ())


def parse_custom_exes(text: str) -> List[str]:
    out = []
    for part in (text or "").replace(";", ",").split(","):
        name = part.strip().lower()
        if not name:
            continue
        if not name.endswith(".exe"):
            name += ".exe"
        out.append(name)
    return out


def list_windows() -> List[dict]:
    """Return [{hwnd, title, pid, exe}] for visible top-level windows."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    results: List[dict] = []
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def _exe_for_pid(pid: int) -> str:
        PROCESS_QUERY_LIMITED = 0x1000
        PROCESS_VM_READ = 0x0010
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED | PROCESS_VM_READ, False, pid)
        if not handle:
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED, False, pid)
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            if psapi.GetModuleFileNameExW(handle, None, buf, 1024):
                return os.path.basename(buf.value).lower()
        except Exception:
            return ""
        finally:
            kernel32.CloseHandle(handle)
        return ""

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value or ""
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        results.append(
            {
                "hwnd": int(hwnd),
                "title": title,
                "pid": int(pid.value),
                "exe": _exe_for_pid(int(pid.value)),
            }
        )
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    return results


def foreground_window() -> dict:
    if sys.platform != "win32":
        return {"title": "FTW", "exe": "", "pid": 0}
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return {"title": "FTW", "exe": "", "pid": 0}
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = ""
    for w in list_windows():
        if w.get("hwnd") == int(hwnd):
            exe = w.get("exe") or ""
            break
    return {"title": buf.value or "FTW", "exe": exe, "pid": int(pid.value), "hwnd": int(hwnd)}


class RenderingNames(set):
    """Exe names plus session display names. A plain set cannot hold attributes."""

    def __init__(self, exes=(), display_names=()):
        super().__init__(exes)
        self.display_names = set(display_names or ())


def pycaw_rendering_exes() -> Set[str]:
    """Process names that currently render audio (optional pycaw)."""
    names: Set[str] = set()
    display: Set[str] = set()
    try:
        from pycaw.pycaw import AudioUtilities  # type: ignore
    except ImportError:
        return RenderingNames()
    try:
        sessions = AudioUtilities.GetAllSessions()
    except Exception:
        return RenderingNames()
    for session in sessions or []:
        try:
            vol = session.SimpleAudioVolume
            if vol is not None and float(vol.GetMasterVolume()) <= 0:
                continue
        except Exception:
            pass
        proc = getattr(session, "Process", None)
        if proc is not None:
            try:
                name = os.path.basename(str(proc.name() or "")).lower()
                if name:
                    names.add(name)
            except Exception:
                pass
        try:
            dn = str(session.DisplayName or "").strip()
            if dn:
                display.add(dn.casefold())
        except Exception:
            pass
    return RenderingNames(names, display)


def _title_matches(title: str, needles: Iterable[str]) -> bool:
    t = (title or "").casefold()
    return any(n.casefold() in t for n in needles if n)


def match_allowlist(
    enabled_apps: Iterable[str],
    custom_exes: Iterable[str],
    windows: Optional[List[dict]] = None,
    rendering: Optional[Set[str]] = None,
) -> Optional[dict]:
    """Return the best matching window if an allowlisted call is active.

    Combines window title/process with optional pycaw rendering confirmation.
    Meet/WhatsApp Web require a title match (not any Chrome).
    """
    windows = windows if windows is not None else list_windows()
    rendering = rendering if rendering is not None else pycaw_rendering_exes()
    render_display = getattr(rendering, "display_names", set()) or set()
    custom = {e.lower() for e in custom_exes or []}
    enabled = [a for a in enabled_apps or [] if a in PRESETS]

    def rendering_ok(exe: str, preset_key: str) -> bool:
        if not rendering:
            return True  # no pycaw → windows-only
        exe_l = (exe or "").lower()
        if exe_l in rendering:
            return True
        titles = PRESETS.get(preset_key, {}).get("titles") or ()
        if any(t.casefold() in render_display for t in titles):
            return True
        return False

    for w in windows:
        exe = (w.get("exe") or "").lower()
        title = w.get("title") or ""
        if exe in custom:
            if not rendering or exe in rendering:
                return {"app": "custom", "window": w, "title": title}
        for key in enabled:
            spec = PRESETS[key]
            exes = tuple(x.lower() for x in _tuple_exes(spec.get("exes")))
            if exe not in exes:
                continue
            need_title = bool(spec.get("need_title_if_browser")) and exe in _BROWSER_EXES
            title_ok = _title_matches(title, spec.get("titles") or ())
            if need_title and not title_ok:
                continue
            if not need_title and not title_ok and exe in _BROWSER_EXES:
                continue
            if not rendering_ok(exe, key):
                continue
            return {"app": key, "window": w, "title": title}
    return None


def meeting_window_title(
    enabled_apps: Iterable[str],
    custom_exes: Iterable[str],
    fallback: str = "FTW",
) -> str:
    hit = match_allowlist(enabled_apps, custom_exes)
    if hit and hit.get("title"):
        return str(hit["title"])
    fg = foreground_window()
    return str(fg.get("title") or fallback) or fallback
