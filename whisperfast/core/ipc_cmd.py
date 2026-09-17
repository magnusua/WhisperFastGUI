"""Single-instance command file for CLI record/process while the GUI is running."""
from __future__ import annotations

import json
import os
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
