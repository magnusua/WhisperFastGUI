"""SQLite archive of processed conversations (transcripts, outputs, FTS search).

Lives next to the app as `library.sqlite`. Does not replace settings.json or
app_log.json — those stay as-is. Job ids match log file-session ids when the
GUI records a transcription.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Any, Dict, Iterable, List, Optional, Sequence

from whisperfast.config import BASE_DIR

LIBRARY_FILENAME = "library.sqlite"
SCHEMA_VERSION = 1

_CREATE_JOBS = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    source TEXT,
    name TEXT,
    status TEXT,
    language TEXT,
    model TEXT,
    summary TEXT,
    category TEXT,
    tags TEXT,
    txt_path TEXT,
    srt_path TEXT,
    mp3_path TEXT,
    json_path TEXT,
    vtt_path TEXT,
    extra_outputs TEXT
);
"""

_CREATE_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS jobs_fts USING fts5(
    job_id UNINDEXED, name, summary, tags, transcript
);
"""


def library_path() -> str:
    return os.path.join(BASE_DIR, LIBRARY_FILENAME)


def _norm_path(path: Optional[str]) -> str:
    if not path:
        return ""
    return os.path.normpath(os.path.abspath(path))


class ConversationLibrary:
    """Thread-safe SQLite index + FTS5 over transcripts."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or library_path()
        self._lock = threading.Lock()
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute(_CREATE_JOBS)
            cur.execute(_CREATE_FTS)
            cur.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
            )
            cur.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    def upsert_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
        """Insert or update a job. `id` is required."""
        job_id = (job.get("id") or "").strip()
        if not job_id:
            raise ValueError("job id is required")
        existing = self.get_job(job_id) or {}
        merged = dict(existing)
        merged.update({k: v for k, v in job.items() if v is not None})
        merged["id"] = job_id
        tags = merged.get("tags") or []
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except json.JSONDecodeError:
                tags = [t.strip() for t in tags.split(",") if t.strip()]
        extra = merged.get("extra_outputs") or []
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except json.JSONDecodeError:
                extra = []
        row = {
            "id": job_id,
            "created_at": merged.get("created_at") or "",
            "source": _norm_path(merged.get("source") or ""),
            "name": merged.get("name") or "",
            "status": merged.get("status") or "",
            "language": merged.get("language") or "",
            "model": merged.get("model") or "",
            "summary": merged.get("summary") or "",
            "category": merged.get("category") or "",
            "tags": json.dumps(list(tags), ensure_ascii=False),
            "txt_path": _norm_path(merged.get("txt_path") or ""),
            "srt_path": _norm_path(merged.get("srt_path") or ""),
            "mp3_path": _norm_path(merged.get("mp3_path") or ""),
            "json_path": _norm_path(merged.get("json_path") or ""),
            "vtt_path": _norm_path(merged.get("vtt_path") or ""),
            "extra_outputs": json.dumps(list(extra), ensure_ascii=False),
        }
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO jobs (
                    id, created_at, source, name, status, language, model,
                    summary, category, tags, txt_path, srt_path, mp3_path,
                    json_path, vtt_path, extra_outputs
                ) VALUES (
                    :id, :created_at, :source, :name, :status, :language, :model,
                    :summary, :category, :tags, :txt_path, :srt_path, :mp3_path,
                    :json_path, :vtt_path, :extra_outputs
                )
                ON CONFLICT(id) DO UPDATE SET
                    created_at=excluded.created_at,
                    source=excluded.source,
                    name=excluded.name,
                    status=excluded.status,
                    language=excluded.language,
                    model=excluded.model,
                    summary=excluded.summary,
                    category=excluded.category,
                    tags=excluded.tags,
                    txt_path=excluded.txt_path,
                    srt_path=excluded.srt_path,
                    mp3_path=excluded.mp3_path,
                    json_path=excluded.json_path,
                    vtt_path=excluded.vtt_path,
                    extra_outputs=excluded.extra_outputs
                """,
                row,
            )
            self._conn.commit()
        self._reindex_fts(job_id)
        return self.get_job(job_id) or merged

    def _reindex_fts(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if not job:
            return
        transcript = ""
        txt = job.get("txt_path") or ""
        if txt and os.path.isfile(txt):
            try:
                with open(txt, "r", encoding="utf-8", errors="replace") as f:
                    transcript = f.read()
            except OSError:
                transcript = ""
        tags = job.get("tags") or []
        tags_s = ", ".join(tags) if isinstance(tags, list) else str(tags)
        with self._lock:
            self._conn.execute("DELETE FROM jobs_fts WHERE job_id=?", (job_id,))
            self._conn.execute(
                "INSERT INTO jobs_fts(job_id, name, summary, tags, transcript) VALUES (?,?,?,?,?)",
                (
                    job_id,
                    job.get("name") or "",
                    job.get("summary") or "",
                    tags_s,
                    transcript,
                ),
            )
            self._conn.commit()

    def add_output(self, job_id: str, role: str, path: str, label: Optional[str] = None) -> None:
        path_n = _norm_path(path)
        if not job_id or not path_n:
            return
        job = self.get_job(job_id)
        if job is None:
            self.upsert_job({"id": job_id, "created_at": "", "status": "running"})
            job = self.get_job(job_id) or {}
        updates: Dict[str, Any] = {"id": job_id}
        if role == "txt":
            updates["txt_path"] = path_n
        elif role == "srt":
            updates["srt_path"] = path_n
        elif role == "mp3":
            updates["mp3_path"] = path_n
        elif role == "json":
            updates["json_path"] = path_n
        elif role == "vtt":
            updates["vtt_path"] = path_n
        elif role == "source":
            updates["source"] = path_n
        else:
            extra = list(job.get("extra_outputs") or [])
            item = {"role": role, "path": path_n}
            if label:
                item["label"] = label
            replaced = False
            for i, o in enumerate(extra):
                if o.get("role") == role and _norm_path(o.get("path")) == path_n:
                    extra[i] = item
                    replaced = True
                    break
            if not replaced:
                extra.append(item)
            updates["extra_outputs"] = extra
        updates.setdefault("source", job.get("source"))
        updates.setdefault("name", job.get("name"))
        updates.setdefault("created_at", job.get("created_at"))
        updates.setdefault("status", job.get("status"))
        updates.setdefault("txt_path", job.get("txt_path"))
        updates.setdefault("srt_path", job.get("srt_path"))
        updates.setdefault("mp3_path", job.get("mp3_path"))
        updates.setdefault("json_path", job.get("json_path"))
        updates.setdefault("vtt_path", job.get("vtt_path"))
        updates.setdefault("summary", job.get("summary"))
        updates.setdefault("category", job.get("category"))
        updates.setdefault("tags", job.get("tags"))
        updates.setdefault("language", job.get("language"))
        updates.setdefault("model", job.get("model"))
        if "extra_outputs" not in updates:
            updates["extra_outputs"] = job.get("extra_outputs") or []
        self.upsert_job(updates)

    def set_meta(
        self,
        job_id: str,
        *,
        summary: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[Sequence[str]] = None,
        status: Optional[str] = None,
    ) -> None:
        job = self.get_job(job_id)
        if not job:
            return
        if summary is not None:
            job["summary"] = summary
        if category is not None:
            job["category"] = category
        if tags is not None:
            job["tags"] = list(tags)
        if status is not None:
            job["status"] = status
        self.upsert_job(job)

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        if not job_id:
            return None
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def list_jobs(self, limit: int = 200) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def search(self, query: str, limit: int = 200) -> List[Dict[str, Any]]:
        q = (query or "").strip()
        if not q:
            return self.list_jobs(limit=limit)
        # Prefer FTS; fall back to LIKE if the query is not valid FTS syntax
        try:
            with self._lock:
                rows = self._conn.execute(
                    """
                    SELECT jobs.* FROM jobs
                    JOIN jobs_fts ON jobs.id = jobs_fts.job_id
                    WHERE jobs_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (q, int(limit)),
                ).fetchall()
            if rows:
                return [self._row_to_job(r) for r in rows]
        except sqlite3.OperationalError:
            pass
        like = f"%{q}%"
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM jobs
                WHERE name LIKE ? OR summary LIKE ? OR source LIKE ? OR tags LIKE ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (like, like, like, like, int(limit)),
            ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def all_paths(self, job: Dict[str, Any]) -> List[str]:
        """Every known file belonging to a job (source + outputs)."""
        paths: List[str] = []
        for key in ("source", "txt_path", "srt_path", "mp3_path", "json_path", "vtt_path"):
            p = job.get(key) or ""
            if p:
                paths.append(p)
        for o in job.get("extra_outputs") or []:
            p = (o or {}).get("path") or ""
            if p:
                paths.append(p)
        seen = set()
        out = []
        for p in paths:
            n = _norm_path(p)
            if n and n not in seen:
                seen.add(n)
                out.append(n)
        return out

    def delete_job(self, job_id: str, *, delete_files: bool = True) -> List[str]:
        """Remove the index row and optionally delete files. Returns deleted paths."""
        job = self.get_job(job_id)
        deleted: List[str] = []
        if job and delete_files:
            for path in self.all_paths(job):
                try:
                    if os.path.isfile(path):
                        os.remove(path)
                        deleted.append(path)
                except OSError:
                    pass
        with self._lock:
            self._conn.execute("DELETE FROM jobs_fts WHERE job_id=?", (job_id,))
            self._conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            self._conn.commit()
        return deleted

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        tags = d.get("tags") or "[]"
        extra = d.get("extra_outputs") or "[]"
        try:
            d["tags"] = json.loads(tags) if isinstance(tags, str) else list(tags or [])
        except json.JSONDecodeError:
            d["tags"] = []
        try:
            d["extra_outputs"] = json.loads(extra) if isinstance(extra, str) else list(extra or [])
        except json.JSONDecodeError:
            d["extra_outputs"] = []
        return d


_default_library: Optional[ConversationLibrary] = None
_default_lock = threading.Lock()


def get_library(path: Optional[str] = None) -> ConversationLibrary:
    global _default_library
    if path:
        return ConversationLibrary(path)
    with _default_lock:
        if _default_library is None:
            _default_library = ConversationLibrary()
        return _default_library
