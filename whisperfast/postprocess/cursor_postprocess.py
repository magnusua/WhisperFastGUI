"""Постпроцесинг TXT через Cursor SDK (з API-ключем) або відкриття Chat (без ключа)."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
from typing import Callable, List, Optional, Tuple

from whisperfast.config import BASE_DIR
from whisperfast.platform_util import win_no_window_kwargs

REDACTOR_FILENAME = "redactor1.md"
CURSOR_POSTPROCESS_DELAY_S = 5.0

_PROMPT_HEADER_RE = re.compile(
    r'^##\s*(?:Промпт|Prompt)\s*[№#]?\s*(\d+)\s*(?:"([^"]*)"|\'([^\']*)\')?\s*$',
    re.IGNORECASE | re.MULTILINE,
)

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
    sys._wf_cursor_sdk_bridge_patched = True  # type: ignore[attr-defined]


def redactor_path() -> str:
    return os.path.join(BASE_DIR, REDACTOR_FILENAME)


def ensure_redactor_file() -> str:
    """Створює шаблон redactor1.md, якщо файлу ще немає. Повертає шлях."""
    path = redactor_path()
    if not os.path.exists(path):
        template = (
            "# Redactor prompts for Whisper Fast GUI\n"
            "\n"
            "Numbered prompts below are applied in order after transcription.\n"
            "Output files use the prompt name in quotes: ## Промпт №1 \"redactor\" → *_redactor.md\n"
            "\n"
            "## Промпт №1 \"redactor\"\n"
            "\n"
            "Clean up the transcript: fix obvious punctuation and capitalization,\n"
            "remove filler words where safe, keep the original meaning and language.\n"
            "Output Markdown only (no commentary outside the document).\n"
            "\n"
            "## Промпт №2 \"summary\"\n"
            "\n"
            "Add a short title and a brief summary at the top, then keep the cleaned body.\n"
            "\n"
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(template)
    return path


def parse_redactor_prompts(path: Optional[str] = None) -> List[Tuple[int, str, str]]:
    """Парсить «## Промпт №N "name"» / «## Prompt #N "name"» → [(n, name, text), ...] за зростанням n.

    name — з лапок після номера (може бути порожнім, якщо лапок немає).
    Порожні секції (без тексту) пропускаються.
    """
    path = path or redactor_path()
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    matches = list(_PROMPT_HEADER_RE.finditer(content))
    if not matches:
        return []
    prompts: List[Tuple[int, str, str]] = []
    for i, m in enumerate(matches):
        num = int(m.group(1))
        name = (m.group(2) if m.group(2) is not None else m.group(3) or "") or ""
        name = name.strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        text = content[start:end].strip()
        if text:
            prompts.append((num, name, text))
    prompts.sort(key=lambda x: x[0])
    return prompts


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


def open_redactor_file(log_func: Optional[LogFunc] = None) -> str:
    """Гарантує наявність redactor1.md і відкриває його системним редактором."""
    path = ensure_redactor_file()
    try:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        if log_func:
            log_func(f"📝 {os.path.basename(path)}")
            log_func(path, "link")
    except OSError as e:
        if log_func:
            log_func(f"❌ {e}")
    return path


def _find_cursor_executable() -> Optional[str]:
    for name in ("cursor", "cursor.cmd", "cursor.exe"):
        found = shutil.which(name)
        if found:
            return found
    if sys.platform == "win32":
        candidates = [
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "cursor", "Cursor.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "cursor", "resources", "app", "bin", "cursor.cmd"),
            os.path.join(os.environ.get("ProgramFiles", ""), "Cursor", "Cursor.exe"),
        ]
        for c in candidates:
            if c and os.path.isfile(c):
                return c
    return None


def open_cursor_chat_fallback(
    txt_path: str,
    prompt1_text: str,
    log_func: Optional[LogFunc] = None,
) -> bool:
    """Відкриває Cursor Chat з TXT; промпт копіює в буфер (автозапуск не гарантований)."""
    txt_path = os.path.abspath(txt_path)
    cursor_bin = _find_cursor_executable()
    if not cursor_bin:
        if log_func:
            try:
                from whisperfast.i18n import t
                log_func(t("cursor_not_found"))
            except ImportError:
                log_func("❌ Cursor executable not found.")
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
        cmd = [cursor_bin, "--chat", "-n", "-g", txt_path]
        kwargs = {}
        if sys.platform == "win32":
            kwargs.update(win_no_window_kwargs())
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
                log_func(f"Cursor Chat opened for {txt_path}")
        return True
    except OSError as e:
        if log_func:
            try:
                from whisperfast.i18n import t
                log_func(t("cursor_chat_error", error=str(e)))
            except ImportError:
                log_func(f"❌ Cursor Chat error: {e}")
        return False


def _build_agent_prompt(input_path: str, output_path: str, prompt_text: str) -> str:
    return (
        f"Read the input file at:\n{input_path}\n\n"
        f"Apply the following instructions to transform its content.\n"
        f"Write the FULL result ONLY to this output file (create or overwrite):\n{output_path}\n"
        f"Do not modify any other files. Do not ask questions — just write the output file.\n\n"
        f"Instructions:\n{prompt_text}\n"
    )


def _run_sdk_one(
    input_path: str,
    output_path: str,
    prompt_text: str,
    api_key: str,
) -> None:
    _prepare_cursor_sdk()
    from cursor_sdk import Agent, AgentOptions, LocalAgentOptions

    cwd = os.path.dirname(os.path.abspath(input_path)) or BASE_DIR
    full_prompt = _build_agent_prompt(input_path, output_path, prompt_text)
    try:
        result = Agent.prompt(
            full_prompt,
            AgentOptions(
                api_key=api_key,
                model="composer-2.5",
                local=LocalAgentOptions(cwd=cwd),
            ),
        )
    except TypeError:
        # Alternate keyword signature
        result = Agent.prompt(
            full_prompt,
            api_key=api_key,
            model="composer-2.5",
            local=LocalAgentOptions(cwd=cwd),
        )
    status = getattr(result, "status", None)
    if status == "error":
        raise RuntimeError(f"Cursor agent run failed: {getattr(result, 'id', '')}")


def run_sdk_chain(
    txt_path: str,
    prompts: List[Tuple[int, str, str]],
    api_key: str,
    log_func: Optional[LogFunc] = None,
    on_file_created: Optional[Callable[[str], None]] = None,
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
                log_func("❌ No numbered prompts found in redactor1.md")
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
                log_func("❌ cursor-sdk not installed. pip install cursor-sdk")
        open_cursor_chat_fallback(txt_path, prompts[0][2], log_func)
        return created

    current_input = txt_path
    for num, name, text in prompts:
        out_path = edited_output_path(txt_path, num, name)
        label = name or f"#{num}"
        if log_func:
            try:
                from whisperfast.i18n import t
                log_func(t("cursor_processing_prompt", num=num, name=label))
            except ImportError:
                log_func(f"▶ Cursor prompt #{num} ({label})…")
        try:
            _run_sdk_one(current_input, out_path, text, api_key)
        except Exception as e:
            if log_func:
                try:
                    from whisperfast.i18n import t
                    log_func(t("cursor_prompt_error", num=num, error=str(e)))
                except ImportError:
                    log_func(f"❌ Cursor prompt #{num}: {e}")
            break
        if not os.path.isfile(out_path):
            # Якщо агент не записав файл — вважаємо крок невдалим і зупиняємо ланцюжок.
            if log_func:
                try:
                    from whisperfast.i18n import t
                    log_func(t("cursor_output_missing", path=out_path))
                except ImportError:
                    log_func(f"❌ Output file not created: {out_path}")
            break
        created.append(out_path)
        if on_file_created:
            on_file_created(out_path)
        if log_func:
            try:
                from whisperfast.i18n import t
                log_func(t("cursor_file_created", num=num, name=os.path.basename(out_path)))
            except ImportError:
                log_func(f"✅ Cursor created: {os.path.basename(out_path)}")
            log_func(out_path, "link")
        current_input = out_path
    return created


def process_txt_with_cursor(
    txt_path: str,
    api_key_from_settings: str = "",
    log_func: Optional[LogFunc] = None,
    on_file_created: Optional[Callable[[str], None]] = None,
    delay_s: float = CURSOR_POSTPROCESS_DELAY_S,
) -> None:
    """Затримка → парсинг промптів → SDK (якщо є ключ) або Chat fallback."""
    if delay_s > 0:
        if log_func:
            try:
                from whisperfast.i18n import t
                log_func(t("cursor_waiting", seconds=int(delay_s)))
            except ImportError:
                log_func(f"⏳ Waiting {int(delay_s)}s before Cursor…")
        threading.Event().wait(delay_s)

    ensure_redactor_file()
    prompts = parse_redactor_prompts()
    if not prompts:
        if log_func:
            try:
                from whisperfast.i18n import t
                log_func(t("cursor_no_prompts"))
            except ImportError:
                log_func("❌ No numbered prompts in redactor1.md")
        return

    api_key = resolve_cursor_api_key(api_key_from_settings)
    if api_key:
        run_sdk_chain(
            txt_path,
            prompts,
            api_key,
            log_func=log_func,
            on_file_created=on_file_created,
        )
    else:
        if log_func:
            try:
                from whisperfast.i18n import t
                log_func(t("cursor_no_api_key_fallback"))
            except ImportError:
                log_func("⚠ No Cursor API key — opening Chat (manual confirm).")
        open_cursor_chat_fallback(txt_path, prompts[0][2], log_func)


def start_txt_postprocess_async(
    txt_path: str,
    api_key_from_settings: str = "",
    log_func: Optional[LogFunc] = None,
    on_file_created: Optional[Callable[[str], None]] = None,
    on_complete: Optional[Callable[[], None]] = None,
    delay_s: float = CURSOR_POSTPROCESS_DELAY_S,
) -> None:
    """Запускає process_txt_with_cursor у daemon-потоці."""
    def _run():
        try:
            process_txt_with_cursor(
                txt_path,
                api_key_from_settings=api_key_from_settings,
                log_func=log_func,
                on_file_created=on_file_created,
                delay_s=delay_s,
            )
        except Exception as e:
            if log_func:
                try:
                    from whisperfast.i18n import t
                    log_func(t("cursor_unexpected_error", error=str(e)))
                except ImportError:
                    log_func(f"❌ Cursor postprocess error: {e}")
        finally:
            if on_complete:
                try:
                    on_complete()
                except Exception:
                    pass

    threading.Thread(target=_run, daemon=True).start()
