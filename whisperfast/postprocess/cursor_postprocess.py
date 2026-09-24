"""Постпроцесинг TXT через Cursor SDK (з API-ключем) або відкриття Chat (без ключа)."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Callable, List, Optional, Tuple

from whisperfast.config import BASE_DIR
from whisperfast.platform_util import win_no_window_kwargs
from whisperfast.postprocess.prompt_library import (
    ensure_prompt_library,
    load_prompt_tuples,
    open_prompt_file,
)

CURSOR_POSTPROCESS_DELAY_S = 5.0
CURSOR_SDK_NETWORK_ATTEMPTS = 5
CURSOR_SDK_NETWORK_RETRY_DELAY_S = 2.0
CURSOR_SDK_BRIDGE_RETRY_DELAY_S = 0.5

LogFunc = Callable[..., None]


def _ensure_os_blocking_compat() -> None:
    """cursor-sdk викликає os.get_blocking/set_blocking (на Windows є з Python 3.12+).

    На Python 3.10/3.11 Windows також перетворюємо EINVAL від non-blocking
    os.read у BlockingIOError, як очікує cursor_sdk._bridge.
    """
    if getattr(os, "_wf_blocking_compat", False):
        return

    need_get_set = not (hasattr(os, "get_blocking") and hasattr(os, "set_blocking"))
    need_read_wrap = sys.platform == "win32" and sys.version_info < (3, 12)
    if not need_get_set and not need_read_wrap:
        return
    if sys.platform != "win32":
        return

    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetNamedPipeHandleState.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.SetNamedPipeHandleState.restype = wintypes.BOOL

    PIPE_WAIT = 0x00000000
    PIPE_NOWAIT = 0x00000001
    _state: dict[int, bool] = {}

    if need_get_set:
        def get_blocking(fd: int) -> bool:
            return _state.get(fd, True)

        def set_blocking(fd: int, blocking: bool) -> None:
            handle = msvcrt.get_osfhandle(fd)
            mode = wintypes.DWORD(PIPE_NOWAIT if not blocking else PIPE_WAIT)
            if not kernel32.SetNamedPipeHandleState(handle, ctypes.byref(mode), None, None):
                err = ctypes.get_last_error()
                if err not in (0, 1, 87):
                    raise OSError(err, f"SetNamedPipeHandleState failed ({err})")
            _state[fd] = bool(blocking)

        os.get_blocking = get_blocking  # type: ignore[attr-defined]
        os.set_blocking = set_blocking  # type: ignore[attr-defined]
    else:
        # Є нативні get/set_blocking, але все одно відстежуємо стан для os.read shim.
        _orig_get = os.get_blocking
        _orig_set = os.set_blocking

        def get_blocking(fd: int) -> bool:
            val = _orig_get(fd)
            _state[fd] = bool(val)
            return val

        def set_blocking(fd: int, blocking: bool) -> None:
            _orig_set(fd, blocking)
            _state[fd] = bool(blocking)

        os.get_blocking = get_blocking  # type: ignore[attr-defined]
        os.set_blocking = set_blocking  # type: ignore[attr-defined]

    if need_read_wrap:
        _orig_read = os.read

        def read(fd: int, n: int) -> bytes:
            try:
                return _orig_read(fd, n)
            except OSError as e:
                # Python < 3.12: порожній non-blocking pipe часто дає EINVAL замість BlockingIOError
                if (not _state.get(fd, True)) and e.errno in (11, 22, 35):
                    raise BlockingIOError(e.errno, e.strerror) from None
                raise

        os.read = read  # type: ignore[assignment]

    os._wf_blocking_compat = True  # type: ignore[attr-defined]


def _windows_direct_bridge_argv() -> Optional[List[str]]:
    """node.exe + bridge.js замість cursor-sdk-bridge.cmd (без вікна cmd)."""
    try:
        from cursor_sdk._vendor import resolve_bridge_path
    except ImportError:
        return None
    launcher = os.path.abspath(resolve_bridge_path())
    if not launcher.lower().endswith(".cmd"):
        return None
    bin_dir = os.path.dirname(launcher)
    node = os.path.join(bin_dir, "node.exe")
    js = os.path.join(bin_dir, "..", "dist", "bin", "cursor-sdk-bridge.js")
    js = os.path.normpath(js)
    if os.path.isfile(node) and os.path.isfile(js):
        return [node, js]
    return None


def _drain_bridge_stderr(process: subprocess.Popen) -> None:
    """Читає stderr bridge у фоні, щоб pipe не забивався після discovery."""
    stream = getattr(process, "stderr", None)
    if stream is None:
        return
    try:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                break
    except (OSError, ValueError):
        pass


def _exception_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}".lower()


def _is_bridge_connection_error(exc: BaseException) -> bool:
    text = _exception_text(exc)
    markers = (
        "10061",
        "connection refused",
        "actively refused",
        "connecterror",
        "failed to establish a new connection",
        "connectionreset",
        "connection aborted",
    )
    return any(m in text for m in markers)


def _is_network_request_failed(exc: BaseException) -> bool:
    return "network request failed" in _exception_text(exc)


def _is_auth_error(exc: BaseException) -> bool:
    text = _exception_text(exc)
    markers = (
        "unauthorized",
        "unauthenticated",
        "authentication",
        "invalid api key",
        "invalid_api_key",
        "401",
        "403",
        "forbidden",
    )
    return any(marker in text for marker in markers)


def _sdk_retry_plan(
    exc: BaseException,
    attempt: int,
    max_network_attempts: int = CURSOR_SDK_NETWORK_ATTEMPTS,
) -> Optional[Tuple[str, float]]:
    """attempt — 1-based номер невдалої спроби. None = більше не повторювати."""
    if _is_auth_error(exc) or attempt >= max_network_attempts:
        return None
    if _is_bridge_connection_error(exc) and not _is_network_request_failed(exc):
        return ("bridge", CURSOR_SDK_BRIDGE_RETRY_DELAY_S)
    return ("network", CURSOR_SDK_NETWORK_RETRY_DELAY_S)


def _prepare_cursor_sdk() -> None:
    """Імпорт cursor_sdk + Windows-сумісність (blocking API і pipe discovery без selectors)."""
    _ensure_os_blocking_compat()
    if sys.platform != "win32":
        return
    if getattr(sys, "_wf_cursor_sdk_bridge_patched", False):
        return

    import codecs
    import time
    from typing import Any, Mapping

    import cursor_sdk._bridge as bridge

    def _read_discovery_windows(process, timeout: float) -> Mapping[str, Any]:
        if process.stderr is None:
            raise bridge.CursorSDKError("Bridge process stderr is unavailable")
        stderr_fd = process.stderr.fileno()
        was_blocking = os.get_blocking(stderr_fd)
        os.set_blocking(stderr_fd, False)
        try:
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            deadline = time.monotonic() + timeout
            stderr_lines: list[str] = []
            pending = ""

            def drain_available():
                nonlocal pending
                while True:
                    try:
                        chunk = os.read(stderr_fd, 8192)
                    except BlockingIOError:
                        return None
                    if not chunk:
                        final_text = decoder.decode(b"", final=True)
                        if final_text:
                            pending += final_text
                        if pending:
                            line = pending
                            pending = ""
                            stderr_lines.append(line)
                            return bridge.parse_discovery_line(line)
                        return None
                    pending += decoder.decode(chunk)
                    while "\n" in pending:
                        line, pending = pending.split("\n", 1)
                        line += "\n"
                        stderr_lines.append(line)
                        discovery = bridge.parse_discovery_line(line)
                        if discovery is not None:
                            return discovery

            while time.monotonic() < deadline:
                discovery = drain_available()
                if discovery is not None:
                    return discovery
                exit_code = process.poll()
                if exit_code is not None:
                    discovery = drain_available()
                    if discovery is not None:
                        return discovery
                    raise bridge.CursorSDKError(
                        f"Bridge exited before discovery with status {exit_code}: "
                        + "".join(stderr_lines)
                        + pending
                    )
                time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
            raise bridge.CursorSDKError("Timed out waiting for bridge discovery")
        finally:
            os.set_blocking(stderr_fd, was_blocking)

    bridge._read_discovery = _read_discovery_windows  # type: ignore[assignment]

    # Bridge.launch spawns node via .cmd without CREATE_NO_WINDOW → console stays open.
    _orig_popen = bridge.subprocess.Popen

    def _popen_no_window(*args, **kwargs):
        no_win = win_no_window_kwargs()
        flags = kwargs.get("creationflags", 0) | no_win.get("creationflags", 0)
        if flags:
            kwargs["creationflags"] = flags
        if "startupinfo" not in kwargs and "startupinfo" in no_win:
            kwargs["startupinfo"] = no_win["startupinfo"]
        return _orig_popen(*args, **kwargs)

    bridge.subprocess.Popen = _popen_no_window  # type: ignore[assignment]

    _orig_launch = bridge.Bridge.launch

    @classmethod
    def _launch_no_console(cls, command=None, **kwargs):
        if command is None:
            direct = _windows_direct_bridge_argv()
            if direct:
                command = direct
        launched = _orig_launch.__func__(cls, command, **kwargs)
        proc = getattr(launched, "process", None)
        if proc is not None and getattr(proc, "stderr", None) is not None:
            threading.Thread(
                target=_drain_bridge_stderr,
                args=(proc,),
                name="cursor-sdk-stderr-drain",
                daemon=True,
            ).start()
        return launched

    bridge.Bridge.launch = _launch_no_console  # type: ignore[assignment]
    sys._wf_cursor_sdk_bridge_patched = True  # type: ignore[attr-defined]


def ensure_redactor_file() -> str:
    """Сумісність: створює ``promts/``, якщо каталогу ще немає."""
    return ensure_prompt_library()


def default_checked_prompt_nums(
    prompts: List[Tuple[int, str, str]],
    stored_nums: Optional[List[int]] = None,
) -> set:
    """Номери промптів, які мають бути позначені за замовчуванням.

    Збережений порожній список = жоден. Якщо збережені номери не збігаються
    з наявними промптами — перший промпт (як раніше).
    """
    from whisperfast.settings import normalize_default_prompt_nums

    stored = (
        [1]
        if stored_nums is None
        else normalize_default_prompt_nums(stored_nums)
    )
    existing = {p[0] for p in prompts}
    selected = {n for n in stored if n in existing}
    if selected:
        return selected
    if stored:
        return {prompts[0][0]} if prompts else set()
    return set()


def parse_redactor_prompts(path: Optional[str] = None) -> List[Tuple[int, str, str]]:
    """``(num, name, body)`` з ``promts/*.json``. Поле ``hint`` у запит не входить.

    ``path``: ``None`` — каталог програми; каталог з ``promts/``; або сам ``promts``.
    """
    if path is None:
        return load_prompt_tuples()
    if os.path.isdir(path):
        base = os.path.basename(os.path.normpath(path))
        if base == "promts":
            return load_prompt_tuples(os.path.dirname(path))
        return load_prompt_tuples(path)
    return []


def prompt_label_from_output_path(path: str, source_path: str = "") -> str:
    """Prompt name from ``stem_one_liner.md`` (names may contain underscores)."""
    stem = os.path.splitext(os.path.basename(path or ""))[0]
    src_stem = os.path.splitext(os.path.basename(source_path or ""))[0]
    if src_stem:
        prefix = src_stem + "_"
        if stem.startswith(prefix) and len(stem) > len(prefix):
            return stem[len(prefix) :]
        if stem.lower().startswith(prefix.lower()) and len(stem) > len(prefix):
            return stem[len(src_stem) + 1 :]
    if "_" in stem:
        return stem.rsplit("_", 1)[-1]
    return stem


def is_one_liner_output(path: str, label: str = "") -> bool:
    """True for the archive-brief prompt, even if the label was split on '_'."""
    lab = (label or "").lower().replace("-", "_")
    stem = os.path.splitext(os.path.basename(path or ""))[0].lower().replace("-", "_")
    return lab in ("one_liner", "oneliner") or stem.endswith("_one_liner") or stem == "one_liner"


def sanitize_prompt_filename(name: str) -> str:
    """Готує ім'я промпта для використання у назві файлу."""
    name = (name or "").strip()
    if not name:
        return ""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    name = re.sub(r"\s+", "_", name)
    name = name.strip("._")
    return name


def resolve_cursor_api_key(settings_key: str = "") -> str:
    """Спочатку CURSOR_API_KEY з env, потім ключ з settings.json."""
    env_key = (os.environ.get("CURSOR_API_KEY") or "").strip()
    if env_key:
        return env_key
    return (settings_key or "").strip()


def plan_prompt_outputs(
    txt_path: str,
    prompts: List[Tuple[int, str, str]],
    resolve_output_path: Optional[Callable[[str], str]] = None,
    log_func: Optional[LogFunc] = None,
) -> List[Tuple[int, str, str, str]]:
    """Pick every output path before any model call. A skipped file stops the chain."""
    planned: List[Tuple[int, str, str, str]] = []
    for num, name, text in prompts:
        intended = edited_output_path(txt_path, num, name)
        out_path = intended
        if resolve_output_path:
            out_path = resolve_output_path(intended) or ""
        if not out_path:
            if log_func:
                try:
                    from whisperfast.i18n import t
                    log_func(t("file_exists_skipped", name=os.path.basename(intended)))
                except ImportError:
                    pass
            break
        planned.append((num, name, text, out_path))
    return planned


def edited_output_path(txt_path: str, prompt_num: int, prompt_name: str = "") -> str:
    """name.txt + промпт «TW_core» → name_TW_core.md.

    Якщо імені немає: №1 → name_edited.md, №N → name_edited_N.md.
    """
    base, _ = os.path.splitext(os.path.abspath(txt_path))
    safe = sanitize_prompt_filename(prompt_name)
    if safe:
        return base + f"_{safe}.md"
    if prompt_num <= 1:
        return base + "_edited.md"
    return base + f"_edited_{prompt_num}.md"


def open_redactor_file(log_func: Optional[LogFunc] = None, path: Optional[str] = None) -> str:
    """Редагує JSON-промпт через тимчасовий Markdown або відкриває каталог ``promts/``."""
    target = path or ensure_prompt_library()
    return open_prompt_file(target, log_func=log_func)


def _find_cursor_gui_exe() -> Optional[str]:
    """Шлях до Cursor.exe (GUI), без cursor.cmd."""
    if sys.platform != "win32":
        return None
    candidates = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "cursor", "Cursor.exe"),
        os.path.join(os.environ.get("ProgramFiles", ""), "Cursor", "Cursor.exe"),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    for name in ("cursor.cmd", "cursor"):
        found = shutil.which(name)
        if not found:
            continue
        # …/resources/app/bin/cursor.cmd → …/Cursor.exe
        bin_dir = os.path.dirname(os.path.abspath(found))
        gui = os.path.normpath(os.path.join(bin_dir, "..", "..", "..", "Cursor.exe"))
        if os.path.isfile(gui):
            return gui
    return None


def _find_cursor_executable() -> Optional[str]:
    gui = _find_cursor_gui_exe()
    if gui:
        return gui
    for name in ("cursor", "cursor.cmd", "cursor.exe"):
        found = shutil.which(name)
        if found:
            return found
    if sys.platform == "win32":
        cmd = os.path.join(
            os.environ.get("LOCALAPPDATA", ""),
            "Programs",
            "cursor",
            "resources",
            "app",
            "bin",
            "cursor.cmd",
        )
        if os.path.isfile(cmd):
            return cmd
    return None


def _cursor_cli_popen_args(cli_args: List[str]) -> Tuple[List[str], dict]:
    """Команда + kwargs для Cursor CLI без вікна консолі.

    cursor.cmd залишає чорне вікно; на Windows запускаємо Cursor.exe + cli.js
    напряму з ELECTRON_RUN_AS_NODE=1 і CREATE_NO_WINDOW.
    """
    kwargs: dict = {}
    env = os.environ.copy()
    if sys.platform == "win32":
        kwargs.update(win_no_window_kwargs())
        gui = _find_cursor_gui_exe()
        if gui:
            cli_js = os.path.join(os.path.dirname(gui), "resources", "app", "out", "cli.js")
            if os.path.isfile(cli_js):
                env["ELECTRON_RUN_AS_NODE"] = "1"
                env["VSCODE_DEV"] = env.get("VSCODE_DEV", "")
                kwargs["env"] = env
                kwargs["stdout"] = subprocess.DEVNULL
                kwargs["stderr"] = subprocess.DEVNULL
                return [gui, cli_js, *cli_args], kwargs
    cursor_bin = _find_cursor_executable()
    if not cursor_bin:
        from whisperfast.i18n import t
        raise FileNotFoundError(t("cursor_not_found"))
    kwargs["env"] = env
    if sys.platform == "win32":
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    return [cursor_bin, *cli_args], kwargs


def open_cursor_chat_fallback(
    txt_path: str,
    prompt1_text: str,
    log_func: Optional[LogFunc] = None,
) -> bool:
    """Відкриває Cursor Chat з TXT; промпт копіює в буфер (автозапуск не гарантований)."""
    txt_path = os.path.abspath(txt_path)
    if not _find_cursor_executable():
        if log_func:
            try:
                from whisperfast.i18n import t
                log_func(t("cursor_not_found"))
            except ImportError:
                pass
        return False

    clipboard_ok = False
    try:
        import tkinter as tk
        root = tk._default_root  # noqa: SLF001
        if root is not None:
            done = threading.Event()

            def _copy():
                nonlocal clipboard_ok
                try:
                    root.clipboard_clear()
                    root.clipboard_append(prompt1_text)
                    clipboard_ok = True
                except Exception:
                    clipboard_ok = False
                finally:
                    done.set()

            root.after(0, _copy)
            done.wait(timeout=2.0)
    except Exception:
        clipboard_ok = False

    try:
        cmd, kwargs = _cursor_cli_popen_args(["--chat", "-n", "-g", txt_path])
        subprocess.Popen(cmd, **kwargs)
        if log_func:
            try:
                from whisperfast.i18n import t
                log_func(t("cursor_chat_opened", name=os.path.basename(txt_path)))
                if clipboard_ok:
                    log_func(t("cursor_chat_prompt_copied"))
                else:
                    log_func(t("cursor_chat_manual_hint"))
            except ImportError:
                pass
        return True
    except (OSError, FileNotFoundError) as e:
        if log_func:
            try:
                from whisperfast.i18n import t
                log_func(t("cursor_chat_error", error=str(e)))
            except ImportError:
                pass
        return False


def _build_agent_prompt(input_path: str, output_path: str, prompt_text: str) -> str:
    return (
        f"Read the input file at:\n{input_path}\n\n"
        f"Apply the following instructions to transform its content.\n"
        f"Write the FULL result ONLY to this output file (create or overwrite):\n{output_path}\n"
        f"Do not modify any other files. Do not ask questions — just write the output file.\n\n"
        f"Instructions:\n{prompt_text}\n"
    )


def _log_sdk_retry(
    log_func: Optional[LogFunc],
    prompt_num: int,
    err: BaseException,
    next_attempt: int,
    total: int,
    delay_s: float,
) -> None:
    if not log_func:
        return
    try:
        from whisperfast.i18n import t
        log_func(
            t(
                "cursor_prompt_retry",
                num=prompt_num,
                error=str(err),
                attempt=next_attempt,
                total=total,
                seconds=int(delay_s) if delay_s == int(delay_s) else delay_s,
            )
        )
    except ImportError:
        pass


def _run_sdk_one(
    input_path: str,
    output_path: str,
    prompt_text: str,
    api_key: str,
    log_func: Optional[LogFunc] = None,
    prompt_num: int = 0,
) -> None:
    _prepare_cursor_sdk()
    from cursor_sdk import Agent, AgentOptions, Client, LocalAgentOptions
    from cursor_sdk._client import close_default_client

    cwd = os.path.dirname(os.path.abspath(input_path)) or BASE_DIR
    full_prompt = _build_agent_prompt(input_path, output_path, prompt_text)
    local = LocalAgentOptions(cwd=cwd)
    options = AgentOptions(
        api_key=api_key,
        model="composer-2.5",
        local=local,
    )

    def _prompt_once():
        # Окремий bridge на промпт (не глобальний singleton) — уникаємо WinError 10061
        # після «мертвого» default client у довгій GUI-сесії.
        client = Client.launch_bridge(workspace=cwd, local=local)
        try:
            return Agent.prompt(full_prompt, options, client=client)
        finally:
            client.close()

    result = None
    last_err: Optional[BaseException] = None
    for attempt in range(1, CURSOR_SDK_NETWORK_ATTEMPTS + 1):
        try:
            result = _prompt_once()
            if getattr(result, "status", None) == "error":
                from whisperfast.i18n import t

                raise RuntimeError(
                    t("cursor_agent_failed", run_id=str(getattr(result, "id", "") or ""))
                )
            last_err = None
            break
        except Exception as err:
            last_err = err
            plan = _sdk_retry_plan(err, attempt)
            if plan is None:
                raise
            kind, delay_s = plan
            if kind == "bridge":
                try:
                    close_default_client()
                except Exception:
                    pass
            _log_sdk_retry(
                log_func,
                prompt_num,
                err,
                next_attempt=attempt + 1,
                total=CURSOR_SDK_NETWORK_ATTEMPTS,
                delay_s=delay_s,
            )
            time.sleep(delay_s)
    if last_err is not None:
        raise last_err
    if result is None:
        raise RuntimeError("Cursor SDK returned no result")


def run_sdk_chain(
    txt_path: str,
    prompts: List[Tuple[int, str, str]],
    api_key: str,
    log_func: Optional[LogFunc] = None,
    on_file_created: Optional[Callable[[str], None]] = None,
    resolve_output_path: Optional[Callable[[str], str]] = None,
) -> List[str]:
    """Послідовно виконує промпти SDK. Повертає список створених шляхів."""
    created: List[str] = []
    txt_path = os.path.abspath(txt_path)
    if not prompts:
        if log_func:
            try:
                from whisperfast.i18n import t
                log_func(t("cursor_no_prompts"))
            except ImportError:
                pass
        return created

    planned = plan_prompt_outputs(txt_path, prompts, resolve_output_path, log_func)
    if not planned:
        return created

    try:
        _prepare_cursor_sdk()
        import cursor_sdk  # noqa: F401
    except ImportError:
        if log_func:
            try:
                from whisperfast.i18n import t
                log_func(t("cursor_sdk_missing"))
            except ImportError:
                pass
        open_cursor_chat_fallback(txt_path, prompts[0][2], log_func)
        return created

    current_input = txt_path
    for num, name, text, out_path in planned:
        label = name or f"#{num}"
        if log_func:
            try:
                from whisperfast.i18n import t
                log_func(t("cursor_processing_prompt", num=num, name=label))
            except ImportError:
                pass
        try:
            _run_sdk_one(
                current_input,
                out_path,
                text,
                api_key,
                log_func=log_func,
                prompt_num=num,
            )
        except Exception as e:
            if log_func:
                try:
                    from whisperfast.i18n import t
                    log_func(t("cursor_prompt_error", num=num, error=str(e)))
                except ImportError:
                    pass
            break
        if not os.path.isfile(out_path):
            # Якщо агент не записав файл — вважаємо крок невдалим і зупиняємо ланцюжок.
            if log_func:
                try:
                    from whisperfast.i18n import t
                    log_func(t("cursor_output_missing", path=out_path))
                except ImportError:
                    pass
            break
        created.append(out_path)
        if on_file_created:
            on_file_created(out_path)
        if log_func:
            try:
                from whisperfast.i18n import t
                log_func(t("cursor_file_created", num=num, name=os.path.basename(out_path)))
            except ImportError:
                pass
        current_input = out_path
    return created


