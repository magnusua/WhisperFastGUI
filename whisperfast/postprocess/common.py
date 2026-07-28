"""Спільні утиліти AI-постпроцесингу (clipboard, browser, HTTP, файли)."""
from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
import webbrowser
from typing import Any, Callable, Dict, Optional

LogFunc = Callable[..., None]


def read_text_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_text_file(path: str, content: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content if content.endswith("\n") else content + "\n")


def copy_text_to_clipboard(text: str, timeout_s: float = 2.0) -> bool:
    """Копіює текст у буфер через головний Tk root (потокобезпечно)."""
    clipboard_ok = False
    try:
        import tkinter as tk

        root = tk._default_root  # noqa: SLF001
        if root is None:
            return False
        done = threading.Event()

        def _copy():
            nonlocal clipboard_ok
            try:
                root.clipboard_clear()
                root.clipboard_append(text)
                clipboard_ok = True
            except Exception:
                clipboard_ok = False
            finally:
                done.set()

        root.after(0, _copy)
        done.wait(timeout=timeout_s)
    except Exception:
        return False
    return clipboard_ok


def open_url_in_browser(url: str) -> bool:
    try:
        webbrowser.open(url, new=2)
        return True
    except Exception:
        return False


def open_browser_fallback(
    url: str,
    clipboard_text: str,
    log_func: Optional[LogFunc],
    *,
    opened_key: str,
    copied_key: str,
    manual_key: str,
    name: str = "",
) -> bool:
    """Відкриває URL і кладе текст у буфер (ручне підтвердження в браузері)."""
    clipboard_ok = copy_text_to_clipboard(clipboard_text)
    opened = open_url_in_browser(url)
    if log_func:
        try:
            from whisperfast.i18n import t

            if opened:
                log_func(t(opened_key, name=name or url))
                if clipboard_ok:
                    log_func(t(copied_key))
                else:
                    log_func(t(manual_key))
            else:
                log_func(t("ai_browser_open_error", url=url))
        except ImportError:
            pass
    return opened


def build_transform_user_message(prompt_text: str, input_content: str) -> str:
    return (
        f"{prompt_text.strip()}\n\n"
        "-----\n"
        "Transform the document below. Reply with the FULL result as Markdown only "
        "(no commentary outside the document).\n"
        "-----\n\n"
        f"{input_content}"
    )


def build_clipboard_fallback_text(prompt_text: str, input_path: str) -> str:
    """Текст для вставки в браузерний чат (промпт + шлях до файлу)."""
    return (
        f"{prompt_text.strip()}\n\n"
        f"Input file path:\n{os.path.abspath(input_path)}\n\n"
        "Open/read that file, apply the instructions, and produce the full Markdown result."
    )


def http_json_request(
    url: str,
    payload: Dict[str, Any],
    headers: Optional[Dict[str, str]] = None,
    timeout_s: float = 180.0,
) -> Dict[str, Any]:
    """POST JSON → dict. Кидає RuntimeError з тілом відповіді при HTTP-помилці."""
    data = json.dumps(payload).encode("utf-8")
    req_headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        from whisperfast.i18n import t
        raise RuntimeError(
            t("http_request_error", code=e.code, detail=err_body or e.reason)
        ) from e
    except urllib.error.URLError as e:
        from whisperfast.i18n import t
        raise RuntimeError(t("http_request_error", code="?", detail=str(e.reason or e))) from e
