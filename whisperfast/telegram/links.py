"""Download a YouTube, Instagram, or Facebook video from a link in a chat."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from typing import List
from urllib.parse import urlsplit

from whisperfast.config import AUDIO_EXTENSIONS, BASE_DIR, VIDEO_EXTENSIONS
from whisperfast.platform_util import win_no_window_kwargs

_MEDIA_EXTS = tuple(ext.lower() for ext in VIDEO_EXTENSIONS + AUDIO_EXTENSIONS)

_HOSTS = (
    "youtube.com",
    "youtu.be",
    "instagram.com",
    "facebook.com",
    "fb.watch",
    "fb.com",
)
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_TRAILING = ".,;:!?)]}>\"'"


def extract_video_urls(text: str) -> List[str]:
    """Public video links. Other URLs in the same message are ignored."""
    found = []
    seen = set()
    for raw in _URL_RE.findall(text or ""):
        url = raw.rstrip(_TRAILING)
        host = urlsplit(url).netloc.lower().split(":")[0]
        if host.startswith("www.") or host.startswith("m."):
            host = host.split(".", 1)[1]
        if host not in _HOSTS or url in seen:
            continue
        seen.add(url)
        found.append(url)
    return found


def newest_media_file(dest_dir: str, not_before: float = 0) -> str:
    """Newest audio/video written into dest_dir. Empty string when there is none."""
    best = ""
    best_mtime = not_before
    try:
        names = os.listdir(dest_dir)
    except OSError:
        return ""
    for name in names:
        if name.endswith(".part") or not name.lower().endswith(_MEDIA_EXTS):
            continue
        path = os.path.join(dest_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if mtime >= best_mtime:
            best_mtime = mtime
            best = path
    return best


def normalize_social_quality(value) -> str:
    """best, 720, or 360. Anything else is the highest available quality."""
    text = str(value or "").strip().lower().rstrip("p")
    if text in ("720", "360"):
        return text
    return "best"


def download_format(quality) -> str:
    """yt-dlp format: highest available, or the best stream that is not taller than the cap."""
    chosen = normalize_social_quality(quality)
    if chosen == "best":
        return "bv*+ba/b"
    return f"bv*[height<={chosen}]+ba/b[height<={chosen}]"


_LINKS_FILE = os.path.join(BASE_DIR, ".ftw_social_links.json")


def _load_saved_links() -> list:
    try:
        with open(_LINKS_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _write_saved_links(rows: list) -> None:
    tmp = _LINKS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(rows, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, _LINKS_FILE)


def remember_link(url: str, path: str = "") -> None:
    """Keep every received social link, and the saved file once it exists."""
    url = str(url or "").strip()
    if not url:
        return
    rows = _load_saved_links()
    saved_path = os.path.abspath(path) if path else ""
    seen_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    for row in rows:
        if isinstance(row, dict) and row.get("url") == url:
            if saved_path:
                row["path"] = saved_path
            row["seen_at"] = seen_at
            _write_saved_links(rows)
            return
    rows.append({"url": url, "path": saved_path, "seen_at": seen_at})
    _write_saved_links(rows)


def _saved_quality() -> str:
    from whisperfast.settings import load_app_settings

    return normalize_social_quality(load_app_settings().get("telegram_social_quality"))


def download_video(url: str, dest_dir: str, quality: str | None = None) -> str:
    """Save one video with yt-dlp. Raises RuntimeError when the download fails."""
    os.makedirs(dest_dir, exist_ok=True)
    started = time.time()
    template = os.path.join(dest_dir, "%(id)s.%(ext)s")
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-playlist",
        "-f",
        download_format(quality if quality is not None else _saved_quality()),
        "--merge-output-format",
        "mp4",
        "-o",
        template,
        "--print",
        "after_move:filepath",
    ]
    cookies = os.path.join(BASE_DIR, "downloads", "facebook", "cookies.txt")
    if os.path.isfile(cookies):
        cmd.extend(["--cookies", cookies])
    cmd.append(url)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            **win_no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(str(exc)) from exc
    output = ((result.stderr or "") + "\n" + (result.stdout or "")).strip()
    path = newest_media_file(dest_dir, not_before=started - 1)
    if not path:
        for line in reversed((result.stdout or "").splitlines()):
            line = line.strip().strip('"')
            if line and os.path.isfile(line) and line.lower().endswith(_MEDIA_EXTS):
                path = line
                break
    if path and os.path.isfile(path):
        return path
    if "No module named" in output:
        raise RuntimeError(output[-400:])
    raise RuntimeError(output[-400:] or "yt-dlp produced no file")


def claim_link(url: str, dest_dir: str):
    """download, enqueue, wait, or send. A new download is stored under the URL."""
    from whisperfast.telegram.seen import classify, note_download

    key = "url:" + url
    action, known = classify(key)
    if action == "download":
        path = download_video(url, dest_dir)
        note_download(key, path)
        return "new", path, None
    if action == "wait":
        return "wait", "", known
    if action == "enqueue":
        return "enqueue", str(known.get("path") or ""), known
    return "send", "", known


_own_uploads = set()


def social_videos_go_to_queue(settings) -> bool:
    return bool(settings.get("telegram_social_to_queue"))


def log_downloaded_file(log, path: str) -> None:
    """Log the saved video so the program log opens it like a document link."""
    if not path:
        return
    try:
        log(path, tag="link")
    except TypeError:
        log(path)


def mark_own_upload(chat_id: int, path: str) -> None:
    """The video we send back must not be picked up as a new queue item."""
    name = os.path.basename(path or "").casefold()
    if name:
        _own_uploads.add((int(chat_id), name))


def consume_own_upload(chat_id: int, filename: str) -> bool:
    key = (int(chat_id), os.path.basename(filename or "").casefold())
    if key not in _own_uploads:
        return False
    _own_uploads.discard(key)
    return True


def remember_wait(url: str, chat_id: int, message_id: int) -> None:
    from whisperfast.telegram.seen import add_target

    add_target("url:" + url, chat_id, message_id)
