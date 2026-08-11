"""Persistent GUI log (JSON) grouped by calendar day.

Entries are either plain lines (`kind=line`) or one object per processed file
(`kind=file`) that accumulates status, segment summary, outputs and events.
Disk writes are batched (dirty + timer), not on every append.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional

from whisperfast.config import BASE_DIR

LOG_FILENAME = "app_log.json"
MAX_DAYS = 60
MAX_ENTRIES_PER_DAY = 2000
FLUSH_DELAY_S = 1.0
MAX_SEGMENT_PREVIEWS = 3
MAX_FILE_EVENTS = 50
LOG_VERSION = 2

KIND_LINE = "line"
KIND_FILE = "file"


def log_path() -> str:
    return os.path.join(BASE_DIR, LOG_FILENAME)


def today_key() -> str:
    return date.today().isoformat()


def _now_ts() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex


def _norm_path(path: Optional[str]) -> str:
    if not path:
        return ""
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


class LogStore:
    """Thread-safe day-keyed log persisted next to the app (`app_log.json`)."""

    def __init__(self, path: Optional[str] = None, on_schedule_flush: Optional[Callable[[float], None]] = None):
        self.path = path or log_path()
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = {"version": LOG_VERSION, "days": {}}
        self._dirty = False
        self._flush_scheduled = False
        self._on_schedule_flush = on_schedule_flush
        self._flush_timer: Optional[threading.Timer] = None
        self.load()

    def set_flush_scheduler(self, callback: Optional[Callable[[float], None]]) -> None:
        """Optional UI-thread scheduler: callback(delay_s) should call flush() later."""
        self._on_schedule_flush = callback

    def load(self) -> None:
        with self._lock:
            if not os.path.isfile(self.path):
                self._data = {"version": LOG_VERSION, "days": {}}
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
                        "entries": [
                            self._normalize_entry(e)
                            for e in entries
                            if isinstance(e, dict)
                        ]
                    }
                self._data = {"version": LOG_VERSION, "days": cleaned}
                self._dirty = False
            except (OSError, json.JSONDecodeError, TypeError):
                self._data = {"version": LOG_VERSION, "days": {}}
                self._dirty = False

    @staticmethod
    def _normalize_entry(e: Dict[str, Any]) -> Dict[str, Any]:
        """Migrate legacy flat lines and ensure ids/kinds."""
        kind = e.get("kind")
        if kind == KIND_FILE:
            out = dict(e)
            out.setdefault("id", _new_id())
            out.setdefault("status", "done")
            out.setdefault("events", [])
            out.setdefault("outputs", [])
            segs = out.get("segments")
            if not isinstance(segs, dict):
                out["segments"] = {"count": 0, "last": []}
            else:
                out["segments"] = {
                    "count": int(segs.get("count") or 0),
                    "last": list(segs.get("last") or [])[-MAX_SEGMENT_PREVIEWS:],
                }
            return out
        # Legacy or plain line
        return {
            "kind": KIND_LINE,
            "id": e.get("id") or _new_id(),
            "ts": e.get("ts") or _now_ts(),
            "text": e.get("text") or "",
            "tag": e.get("tag"),
        }

    def flush(self) -> None:
        with self._lock:
            self._cancel_timer_unlocked()
            self._flush_scheduled = False
            if not self._dirty:
                return
            self._save_unlocked()
            self._dirty = False

    def _mark_dirty_unlocked(self) -> None:
        self._dirty = True
        if self._flush_scheduled:
            return
        self._flush_scheduled = True
        delay = FLUSH_DELAY_S
        if self._on_schedule_flush is not None:
            try:
                self._on_schedule_flush(delay)
                return
            except Exception:
                pass
        self._flush_timer = threading.Timer(delay, self.flush)
        self._flush_timer.daemon = True
        self._flush_timer.start()

    def _cancel_timer_unlocked(self) -> None:
        t = self._flush_timer
        self._flush_timer = None
        if t is not None:
            try:
                t.cancel()
            except Exception:
                pass

    def _save_unlocked(self) -> None:
        try:
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
                f.write("\n")
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

    def count_file_entries(self, day_key: str) -> int:
        """Кількість file-сесій у дні (для заголовка логу)."""
        with self._lock:
            day = (self._data.get("days") or {}).get(day_key) or {}
            n = 0
            for e in day.get("entries") or []:
                if isinstance(e, dict) and e.get("kind") == KIND_FILE:
                    n += 1
            return n

    def prune_days_without_files(self, *, keep_today: bool = True) -> int:
        """Видалити дні без жодної file-сесії (сьогодні залишаємо). Повертає скільки днів прибрано."""
        today = today_key()
        removed = 0
        with self._lock:
            days = self._data.get("days") or {}
            for key in list(days.keys()):
                if keep_today and key == today:
                    continue
                entries = days[key].get("entries") or []
                has_file = any(
                    isinstance(e, dict) and e.get("kind") == KIND_FILE for e in entries
                )
                if not has_file:
                    days.pop(key, None)
                    removed += 1
            if removed:
                self._mark_dirty_unlocked()
        if removed:
            self.flush()
        return removed

    def get_entries(self, day_key: str) -> List[Dict[str, Any]]:
        with self._lock:
            day = (self._data.get("days") or {}).get(day_key) or {}
            return [dict(e) if isinstance(e, dict) else e for e in (day.get("entries") or [])]

    def get_entry(self, entry_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            found = self._find_entry_unlocked(entry_id)
            return dict(found) if found else None

    def _find_entry_unlocked(self, entry_id: str) -> Optional[Dict[str, Any]]:
        if not entry_id:
            return None
        for day in (self._data.get("days") or {}).values():
            for e in day.get("entries") or []:
                if isinstance(e, dict) and e.get("id") == entry_id:
                    return e
        return None

    def _day_bucket_unlocked(self, day_key: Optional[str] = None) -> Dict[str, Any]:
        key = day_key or today_key()
        days = self._data.setdefault("days", {})
        return days.setdefault(key, {"entries": []})

    def append_line(self, text: str, tag: Optional[str] = None) -> Dict[str, Any]:
        """Append one plain log line; returns the stored entry (with day)."""
        day_key = today_key()
        entry = {
            "kind": KIND_LINE,
            "id": _new_id(),
            "ts": _now_ts(),
            "text": text if text.endswith("\n") else text + "\n",
            "tag": tag,
        }
        with self._lock:
            self._day_bucket_unlocked(day_key).setdefault("entries", []).append(entry)
            self._prune_unlocked()
            self._mark_dirty_unlocked()
        out = dict(entry)
        out["day"] = day_key
        return out

    # Back-compat alias used by older callers
    def append(self, text: str, tag: Optional[str] = None) -> Dict[str, Any]:
        return self.append_line(text, tag=tag)

    def begin_file(
        self,
        source: str,
        name: Optional[str] = None,
        current: Optional[int] = None,
        total: Optional[int] = None,
    ) -> Dict[str, Any]:
        day_key = today_key()
        src = os.path.abspath(source) if source else ""
        entry: Dict[str, Any] = {
            "kind": KIND_FILE,
            "id": _new_id(),
            "ts": _now_ts(),
            "ts_end": None,
            "source": src,
            "name": name or (os.path.basename(src) if src else ""),
            "status": "running",
            "index": None,
            "events": [],
            "segments": {"count": 0, "last": []},
            "outputs": [],
            "error": None,
        }
        if current is not None and total is not None:
            entry["index"] = {"current": int(current), "total": int(total)}
        with self._lock:
            self._day_bucket_unlocked(day_key).setdefault("entries", []).append(entry)
            self._prune_unlocked()
            self._mark_dirty_unlocked()
        out = dict(entry)
        out["day"] = day_key
        return out

    def update_file(self, file_id: str, mutator: Callable[[Dict[str, Any]], None]) -> Optional[Dict[str, Any]]:
        with self._lock:
            entry = self._find_entry_unlocked(file_id)
            if entry is None or entry.get("kind") != KIND_FILE:
                return None
            mutator(entry)
            self._mark_dirty_unlocked()
            out = dict(entry)
        out["day"] = today_key()
        # Prefer day of the entry if we can resolve it
        with self._lock:
            for dk, day in (self._data.get("days") or {}).items():
                for e in day.get("entries") or []:
                    if isinstance(e, dict) and e.get("id") == file_id:
                        out["day"] = dk
                        break
        return out

    def add_file_event(
        self,
        file_id: str,
        text: str,
        tag: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        text_n = text if text.endswith("\n") else text + "\n"

        def _mut(e: Dict[str, Any]) -> None:
            events = e.setdefault("events", [])
            events.append({"ts": _now_ts(), "text": text_n, "tag": tag})
            if len(events) > MAX_FILE_EVENTS:
                e["events"] = events[-MAX_FILE_EVENTS:]

        return self.update_file(file_id, _mut)

    def set_file_segment(
        self,
        file_id: str,
        t: str,
        text: str,
        count: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        preview = {"t": t, "text": (text or "").strip()}

        def _mut(e: Dict[str, Any]) -> None:
            segs = e.setdefault("segments", {"count": 0, "last": []})
            if count is not None:
                segs["count"] = int(count)
            else:
                segs["count"] = int(segs.get("count") or 0) + 1
            last = list(segs.get("last") or [])
            last.append(preview)
            segs["last"] = last[-MAX_SEGMENT_PREVIEWS:]

        return self.update_file(file_id, _mut)

    def set_file_source(self, file_id: str, path: str) -> Optional[Dict[str, Any]]:
        """Update source path after relocating the original media/document file."""
        src = os.path.abspath(path) if path else ""

        def _mut(e: Dict[str, Any]) -> None:
            e["source"] = src

        return self.update_file(file_id, _mut)

    def add_file_output(
        self,
        file_id: str,
        role: str,
        path: str,
        label: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        item: Dict[str, Any] = {
            "role": role,
            "path": os.path.abspath(path) if path else "",
        }
        if label:
            item["label"] = label

        def _mut(e: Dict[str, Any]) -> None:
            outs = e.setdefault("outputs", [])
            norm = _norm_path(item["path"])
            # role=source: single slot (path may change after move)
            if role == "source":
                for i, o in enumerate(outs):
                    if o.get("role") == "source":
                        outs[i] = item
                        return
                outs.insert(0, item)
                return
            # Replace same role+path if already present
            for i, o in enumerate(outs):
                if o.get("role") == role and _norm_path(o.get("path")) == norm:
                    outs[i] = item
                    return
            outs.append(item)

        return self.update_file(file_id, _mut)

    def end_file(
        self,
        file_id: str,
        status: str = "done",
        error: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        def _mut(e: Dict[str, Any]) -> None:
            e["status"] = status
            e["ts_end"] = _now_ts()
            if error:
                e["error"] = error

        out = self.update_file(file_id, _mut)
        # Persist completed file promptly
        self.flush()
        return out

    def get_file(self, file_id: str) -> Optional[Dict[str, Any]]:
        """Повертає копію file-сесії за id або None."""
        if not file_id:
            return None
        with self._lock:
            for day_key, day in (self._data.get("days") or {}).items():
                for e in day.get("entries") or []:
                    if isinstance(e, dict) and e.get("id") == file_id and e.get("kind") == KIND_FILE:
                        out = dict(e)
                        out["day"] = day_key
                        return out
        return None

    def find_file_by_source(self, path: str) -> Optional[Dict[str, Any]]:
        norm = _norm_path(path)
        if not norm:
            return None
        with self._lock:
            # Prefer newest match (scan reverse)
            for day_key in reversed(sorted((self._data.get("days") or {}).keys())):
                entries = (self._data["days"][day_key].get("entries") or [])
                for e in reversed(entries):
                    if (
                        isinstance(e, dict)
                        and e.get("kind") == KIND_FILE
                        and _norm_path(e.get("source")) == norm
                    ):
                        out = dict(e)
                        out["day"] = day_key
                        return out
        return None

    def find_file_by_output(self, path: str) -> Optional[Dict[str, Any]]:
        norm = _norm_path(path)
        if not norm:
            return None
        with self._lock:
            for day_key in reversed(sorted((self._data.get("days") or {}).keys())):
                entries = (self._data["days"][day_key].get("entries") or [])
                for e in reversed(entries):
                    if not isinstance(e, dict) or e.get("kind") != KIND_FILE:
                        continue
                    for o in e.get("outputs") or []:
                        if _norm_path(o.get("path")) == norm:
                            out = dict(e)
                            out["day"] = day_key
                            return out
        return None

    def clear(self) -> None:
        with self._lock:
            self._cancel_timer_unlocked()
            self._flush_scheduled = False
            self._data = {"version": LOG_VERSION, "days": {}}
            self._dirty = True
            self._save_unlocked()
            self._dirty = False
