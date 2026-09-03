"""
Вибір інтерпретатора Python при першому запуску.
Зберігає шлях у settings.json (ключ python_path) і за потреби перезапускає програму.
"""
import os
import re
import subprocess
import sys
import tkinter as tk
from tkinter import ttk
from typing import Optional

from whisperfast.config import BASE_DIR, RESOURCES_DIR
from whisperfast.platform_util import win_no_window_kwargs

from whisperfast.i18n import t
from whisperfast.settings import load_app_settings, save_app_settings

# Підтримуваний діапазон (як у README); 3.14+ показуємо, але позначаємо як нерекомендовані
_MIN_OK = (3, 9)
_MAX_OK = (3, 13)
_PREF_ORDER = ((3, 12), (3, 11), (3, 13), (3, 10), (3, 9))


def _creationflags():
    return win_no_window_kwargs().get("creationflags", 0)


def _norm_key(path: str) -> str:
    try:
        return os.path.normcase(os.path.realpath(path))
    except OSError:
        return os.path.normcase(os.path.abspath(path))


def _dir_key(path: str) -> str:
    return os.path.normcase(os.path.dirname(_norm_key(path)))


def _to_python_exe(path: str) -> str:
    """pythonw.exe → python.exe у тому ж каталозі (якщо є)."""
    if not path:
        return path
    base = os.path.basename(path).lower()
    if base in ("pythonw.exe", "pythonw"):
        sibling = os.path.join(os.path.dirname(path), "python.exe" if base.endswith(".exe") else "python")
        if os.path.isfile(sibling):
            return sibling
    return path


def _launch_exe_for(chosen_python: str) -> str:
    """Якщо поточний процес — pythonw, запускаємо pythonw обраної збірки."""
    chosen = _to_python_exe(chosen_python)
    cur = os.path.basename(sys.executable).lower()
    if cur in ("pythonw.exe", "pythonw"):
        name = "pythonw.exe" if chosen.lower().endswith(".exe") else "pythonw"
        candidate = os.path.join(os.path.dirname(chosen), name)
        if os.path.isfile(candidate):
            return candidate
    return chosen


def _probe_version(exe: str):
    """Повертає (major, minor, micro) або None."""
    if not exe or not os.path.isfile(exe):
        return None
    try:
        out = subprocess.check_output(
            [exe, "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"],
            stderr=subprocess.DEVNULL,
            timeout=8,
            creationflags=_creationflags(),
        )
        text = out.decode("utf-8", errors="replace").strip()
        m = re.match(r"^(\d+)\.(\d+)\.(\d+)$", text)
        if not m:
            return None
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _preference_score(ver: tuple) -> int:
    major_minor = (ver[0], ver[1])
    try:
        return _PREF_ORDER.index(major_minor)
    except ValueError:
        if major_minor < _MIN_OK:
            return 100
        return 50 + major_minor[0] * 10 + major_minor[1]


def _is_recommended(ver: tuple) -> bool:
    return _MIN_OK <= (ver[0], ver[1]) <= _MAX_OK


def _newest_recommended(candidates: list) -> Optional[dict]:
    """Найновіша версія з робочого діапазону 3.9–3.13, або None."""
    recommended = [c for c in candidates if c.get("recommended")]
    if not recommended:
        return None
    return max(recommended, key=lambda c: c["version"])


def _current_candidate(candidates: list) -> Optional[dict]:
    """Поточний інтерпретатор серед кандидатів (або зібраний з sys.executable)."""
    for c in candidates:
        if _same_install(c["path"], sys.executable):
            return c
    return _candidate(sys.executable)


def _show_no_suitable_info(version_str: str) -> None:
    """Інфо: немає підходящої версії; поточна не тестувалась."""
    try:
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        try:
            messagebox.showinfo(
                t("python_no_suitable_title"),
                t(
                    "python_no_suitable_msg",
                    version=version_str,
                    min_ver=f"{_MIN_OK[0]}.{_MIN_OK[1]}",
                    max_ver=f"{_MAX_OK[0]}.{_MAX_OK[1]}",
                ),
                parent=root,
            )
        finally:
            root.destroy()
    except Exception:
        print(t(
            "python_no_suitable_msg",
            version=version_str,
            min_ver=f"{_MIN_OK[0]}.{_MIN_OK[1]}",
            max_ver=f"{_MAX_OK[0]}.{_MAX_OK[1]}",
        ))


def _choice_on_cancel(candidates: list) -> dict:
    """
    Скасування: зберегти найновішу сумісну версію.
    Якщо сумісної немає — показати інфо і взяти поточну (непротестовану).
    """
    newest = _newest_recommended(candidates)
    if newest is not None:
        return newest

    cur = _current_candidate(candidates) or candidates[0]
    version_str = cur.get("version_str") or (
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    )
    _show_no_suitable_info(version_str)
    return cur


def _candidate(path: str, version=None):
    path = _to_python_exe(os.path.abspath(path))
    if not os.path.isfile(path):
        return None
    ver = version or _probe_version(path)
    if not ver or ver[0] < 3:
        return None
    return {
        "path": path,
        "version": ver,
        "version_str": f"{ver[0]}.{ver[1]}.{ver[2]}",
        "recommended": _is_recommended(ver),
        "label": f"Python {ver[0]}.{ver[1]}.{ver[2]} — {path}",
    }


def _add_unique(by_dir: dict, path: str, version=None):
    c = _candidate(path, version=version)
    if not c:
        return
    key = _dir_key(c["path"])
    prev = by_dir.get(key)
    if prev is None or _preference_score(c["version"]) < _preference_score(prev["version"]):
        by_dir[key] = c


def discover_pythons():
    """Знаходить встановлені інтерпретатори Python (унікальні за каталогом)."""
    by_dir = {}

    _add_unique(by_dir, sys.executable)

    venv_candidates = [
        os.path.join(BASE_DIR, ".venv", "Scripts", "python.exe"),
        os.path.join(BASE_DIR, ".venv", "bin", "python"),
        os.path.join(BASE_DIR, "venv", "Scripts", "python.exe"),
        os.path.join(BASE_DIR, "venv", "bin", "python"),
    ]
    for p in venv_candidates:
        _add_unique(by_dir, p)

    if sys.platform == "win32":
        try:
            out = subprocess.check_output(
                ["py", "-0p"],
                stderr=subprocess.DEVNULL,
                timeout=8,
                creationflags=_creationflags(),
            )
            for line in out.decode("utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # "-V:3.12 *        C:\...\python.exe" або "-3.12-64        C:\..."
                parts = line.split()
                if not parts:
                    continue
                path = parts[-1].strip('"')
                _add_unique(by_dir, path)
        except (OSError, subprocess.SubprocessError):
            pass

        for cmd in ("where", "where.exe"):
            try:
                out = subprocess.check_output(
                    [cmd, "python", "python3"],
                    stderr=subprocess.DEVNULL,
                    timeout=8,
                    creationflags=_creationflags(),
                )
                for line in out.decode("utf-8", errors="replace").splitlines():
                    p = line.strip().strip('"')
                    if p and not p.lower().endswith("windowsapps\\python.exe"):
                        _add_unique(by_dir, p)
                break
            except (OSError, subprocess.SubprocessError):
                continue

        local_root = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Python")
        if os.path.isdir(local_root):
            try:
                for name in os.listdir(local_root):
                    _add_unique(by_dir, os.path.join(local_root, name, "python.exe"))
            except OSError:
                pass
    else:
        for name in ("python3", "python", "python3.12", "python3.11", "python3.13", "python3.10", "python3.9"):
            try:
                out = subprocess.check_output(
                    ["which", "-a", name],
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
                for line in out.decode("utf-8", errors="replace").splitlines():
                    _add_unique(by_dir, line.strip())
            except (OSError, subprocess.SubprocessError):
                pass

    items = list(by_dir.values())
    items.sort(key=lambda c: (_preference_score(c["version"]), c["path"].lower()))
    return items


def _same_install(path_a: str, path_b: str) -> bool:
    if not path_a or not path_b:
        return False
    return _dir_key(_to_python_exe(path_a)) == _dir_key(_to_python_exe(path_b))


def _reexec(python_path: str) -> None:
    """Перезапуск main з обраним Python; поточний процес завершується."""
    launch = _launch_exe_for(python_path)
    script = os.path.abspath(sys.argv[0])
    args = [launch, script] + sys.argv[1:]
    env = os.environ.copy()
    env["WHISPER_PYTHON_REEXEC"] = "1"
    kwargs = {"cwd": BASE_DIR, "env": env}
    if sys.platform == "win32" and os.path.basename(launch).lower().startswith("pythonw"):
        kwargs["creationflags"] = _creationflags()
    try:
        subprocess.Popen(args, **kwargs)
    except OSError as e:
        try:
            from tkinter import messagebox
            messagebox.showerror(t("python_select_title"), t("python_reexec_failed", error=str(e)))
        except Exception:
            print(t("python_reexec_failed", error=str(e)))
        return
    sys.exit(0)


def _show_select_dialog(candidates: list) -> Optional[dict]:
    """Модальне вікно вибору. Повертає обраний candidate або None (скасування)."""
    root = tk.Tk()
    # Не показувати вікно у стандартній позиції до розрахунку центра.
    root.withdraw()
    root.title(t("python_select_title"))
    root.resizable(True, True)
    try:
        icon = os.path.join(RESOURCES_DIR, "favicon.ico")
        if os.path.isfile(icon):
            root.iconbitmap(icon)
    except Exception:
        pass

    selected = {"value": None}

    main = ttk.Frame(root, padding=12)
    main.pack(fill="both", expand=True)
    ttk.Label(main, text=t("python_select_msg"), wraplength=480).pack(anchor="w", pady=(0, 8))

    frame = ttk.Frame(main)
    frame.pack(fill="both", expand=True)
    lb = tk.Listbox(frame, height=min(12, max(4, len(candidates))), selectmode="single", font=("Segoe UI", 9))
    scroll = ttk.Scrollbar(frame)
    lb.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")
    lb.config(yscrollcommand=scroll.set)
    scroll.config(command=lb.yview)

    default_idx = 0
    for i, c in enumerate(candidates):
        suffix = "" if c["recommended"] else f"  ({t('python_select_not_recommended')})"
        lb.insert("end", c["label"] + suffix)

    for i, c in enumerate(candidates):
        if c["recommended"]:
            default_idx = i
            break

    lb.selection_set(default_idx)
    lb.see(default_idx)

    def on_ok():
        sel = lb.curselection()
        if not sel:
            return
        selected["value"] = candidates[sel[0]]
        root.destroy()

    def on_cancel():
        selected["value"] = None
        root.destroy()

    btns = ttk.Frame(main)
    btns.pack(fill="x", pady=(10, 0))
    ttk.Button(btns, text=t("python_select_ok"), command=on_ok).pack(side="right", padx=(6, 0))
    ttk.Button(btns, text=t("python_select_cancel"), command=on_cancel).pack(side="right")

    lb.bind("<Double-Button-1>", lambda _e: on_ok())
    root.protocol("WM_DELETE_WINDOW", on_cancel)

    root.update_idletasks()
    w, h = 560, 320
    # Центр основного екрана. На Windows використовуємо робочу область,
    # щоб вікно не перекривало панель задач.
    left, top = 0, 0
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            work_area = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(
                0x0030, 0, ctypes.byref(work_area), 0
            ):
                left, top = work_area.left, work_area.top
                sw = work_area.right - work_area.left
                sh = work_area.bottom - work_area.top
        except Exception:
            pass
    x = left + max(0, (sw - w) // 2)
    y = top + max(0, (sh - h) // 2)
    root.geometry(f"{w}x{h}+{x}+{y}")
    root.minsize(420, 240)
    root.deiconify()
    root.lift()
    root.attributes("-topmost", True)
    root.after(200, lambda: root.attributes("-topmost", False))
    root.mainloop()
    return selected["value"]


def _discovered_ids(candidates: list) -> list:
    return sorted(_dir_key(c["path"]) for c in candidates)


def _should_prompt_python_choice(candidates: list, saved_path: str, settings: dict) -> bool:
    """Ask when several Pythons exist and the user has not confirmed, or the set changed."""
    if len(candidates) < 2:
        return False
    current = _discovered_ids(candidates)
    previous = settings.get("python_discovered") or []
    if not isinstance(previous, list):
        previous = []
    previous = [str(x) for x in previous]
    if current != previous:
        return True
    saved_ok = bool(saved_path) and (
        os.path.isfile(saved_path)
        or any(_same_install(saved_path, c["path"]) for c in candidates)
    )
    if not saved_ok:
        return True
    return not bool(settings.get("python_path_chosen"))


def ensure_preferred_python() -> None:
    """
    При першому запуску (немає валідного python_path) — знайти інтерпретатори,
    якщо кілька — запитати користувача, зберегти вибір і за потреби перезапуститись.
    Якщо з’явився ще один Python (наприклад після встановлення 3.12) — запитати знову.
    Якщо шлях уже підтверджений і набір інсталяцій не змінився — перезапуск на збережений.
    """
    if os.environ.get("WHISPER_PYTHON_REEXEC") == "1":
        # Уже перезапущені обраним Python — не зациклюватись
        os.environ.pop("WHISPER_PYTHON_REEXEC", None)
        return

    settings = load_app_settings()
    saved = (settings.get("python_path") or "").strip()
    candidates = discover_pythons()
    discovered = _discovered_ids(candidates)

    def _commit(chosen: dict, chosen_by_user: bool) -> None:
        payload = {
            "python_path": chosen["path"],
            "python_version": chosen.get("version_str", ""),
            "python_discovered": discovered,
        }
        if chosen_by_user:
            payload["python_path_chosen"] = True
        save_app_settings(payload)
        if not _same_install(chosen["path"], sys.executable):
            _reexec(chosen["path"])

    if not candidates:
        cur = _candidate(sys.executable) or {
            "path": _to_python_exe(sys.executable),
            "version": sys.version_info[:3],
            "version_str": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "recommended": _is_recommended(sys.version_info[:2]),
        }
        if not cur.get("recommended"):
            _show_no_suitable_info(cur.get("version_str", "?"))
        _commit(cur, chosen_by_user=False)
        return

    if len(candidates) == 1:
        chosen = candidates[0]
        if not chosen.get("recommended"):
            _show_no_suitable_info(chosen.get("version_str", "?"))
        _commit(chosen, chosen_by_user=False)
        return

    if _should_prompt_python_choice(candidates, saved, settings):
        chosen = _show_select_dialog(candidates)
        if chosen is None:
            chosen = _choice_on_cancel(candidates)
        _commit(chosen, chosen_by_user=True)
        return

    if saved and os.path.isfile(saved) and not _same_install(saved, sys.executable):
        _reexec(saved)
