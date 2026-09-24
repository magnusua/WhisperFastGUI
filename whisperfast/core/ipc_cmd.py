"""Single-instance command file for CLI record/process while the GUI is running."""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, Optional

from whisperfast.config import BASE_DIR

CMD_FILENAME = ".ftw_cmd.json"


def cmd_path() -> str:
    return os.path.join(BASE_DIR, CMD_FILENAME)


def write_command(action: str, **payload: Any) -> str:
    path = cmd_path()
    data = {"action": action, "ts": time.time(), **payload}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
    return path


def cmd_queue_dir() -> str:
    return os.path.join(BASE_DIR, ".ftw_cmds")


_append_seq = 0
_append_lock = threading.Lock()


def append_command(action: str, **payload: Any) -> str:
    """Append one command. Unlike write_command, a later call does not erase this one."""
    global _append_seq
    folder = cmd_queue_dir()
    os.makedirs(folder, exist_ok=True)
    data = {"action": action, "ts": time.time(), **payload}
    with _append_lock:
        _append_seq += 1
        seq = _append_seq
    # time_ns() can repeat on Windows when two files arrive in the same tick.
    name = f"{time.time_ns()}_{os.getpid()}_{seq:06d}.json"
    path = os.path.join(folder, name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
    return path


def take_queued_commands() -> list:
    """Read and remove every queued command, oldest first."""
    folder = cmd_queue_dir()
    if not os.path.isdir(folder):
        return []
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith(".json"))
    except OSError:
        return []
    out = []
    for name in names:
        path = os.path.join(folder, name)
        data = None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError, TypeError):
            data = None
        try:
            os.remove(path)
        except OSError:
            pass
        if isinstance(data, dict):
            out.append(data)
    return out


def take_command() -> Optional[Dict[str, Any]]:
    path = cmd_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, TypeError):
        data = None
    try:
        os.remove(path)
    except OSError:
        pass
    return data if isinstance(data, dict) else None
