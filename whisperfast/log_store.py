"""Persistent GUI log (JSON) grouped by calendar day."""
from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from whisperfast.config import BASE_DIR

LOG_FILENAME = "app_log.json"
MAX_DAYS = 60
MAX_ENTRIES_PER_DAY = 8000


def log_path() -> str:
    return os.path.join(BASE_DIR, LOG_FILENAME)


def today_key() -> str:
    return date.today().isoformat()


class LogStore:
    """Thread-safe day-keyed log persisted next to the app (`app_log.json`)."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or log_path()
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = {"days": {}}
        self.load()

    def load(self) -> None:
        with self._lock:
            if not os.path.isfile(self.path):
                self._data = {"days": {}}
                return
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                days = raw.get("days") if isinstance(raw, dict) else None
                if not isinstance(days, dict):
                    days = {}
                cleaned: Dict[str, Any] = {}
                for key, day in days.items():
                    if not isinstance(day, dict):
                        continue
                    entries = day.get("entries")
                    if not isinstance(entries, list):
                        entries = []
                    cleaned[str(key)] = {
                        "entries": [e for e in entries if isinstance(e, dict)]
                    }
                self._data = {"days": cleaned}
            except (OSError, json.JSONDecodeError, TypeError):
                self._data = {"days": {}}

    def save(self) -> None:
        with self._lock:
            self._save_unlocked()

    def _save_unlocked(self) -> None:
        try:
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def _prune_unlocked(self) -> None:
        days = self._data.get("days") or {}
        keys = sorted(days.keys())
        if len(keys) > MAX_DAYS:
            for k in keys[: len(keys) - MAX_DAYS]:
                days.pop(k, None)
        for k, day in list(days.items()):
            entries = day.get("entries") or []
            if len(entries) > MAX_ENTRIES_PER_DAY:
                day["entries"] = entries[-MAX_ENTRIES_PER_DAY:]

    def day_keys(self) -> List[str]:
        with self._lock:
            return sorted((self._data.get("days") or {}).keys())

    def get_entries(self, day_key: str) -> List[Dict[str, Any]]:
        with self._lock:
            day = (self._data.get("days") or {}).get(day_key) or {}
            return list(day.get("entries") or [])

    def append(self, text: str, tag: Optional[str] = None) -> Dict[str, Any]:
        """Append one line; returns the stored entry (with day + ts)."""
        day_key = today_key()
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "text": text if text.endswith("\n") else text + "\n",
            "tag": tag,
        }
        with self._lock:
            days = self._data.setdefault("days", {})
            day = days.setdefault(day_key, {"entries": []})
            day.setdefault("entries", []).append(entry)
            self._prune_unlocked()
            self._save_unlocked()
        entry_out = dict(entry)
        entry_out["day"] = day_key
        return entry_out

    def clear(self) -> None:
        with self._lock:
            self._data = {"days": {}}
            self._save_unlocked()
