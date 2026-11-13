"""Download a YouTube, Instagram, or Facebook video from a link in a chat."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from typing import List, Sequence, Tuple
from urllib.parse import parse_qsl, urlsplit

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
_FACEBOOK_HOSTS = frozenset({"facebook.com", "fb.watch", "fb.com"})
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_TRAILING = ".,;:!?)]}>\"'"
_AUTH_HINTS = (
    "login.php",
    "registered users",
    "cookies-from-browser",
    "use --cookies",
    "only available for registered",
)
_BROWSER_COOKIE_CANDIDATES = ("chrome", "edge", "firefox")


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


_TRACKING_KEYS = frozenset({
    "si",
    "feature",
    "fbclid",
    "igsh",
    "igshid",
    "mibextid",
    "ref",
    "ref_src",
    "refid",
    "__cft__",
    "__tn__",
    "t",
    "list",
    "index",
    "pp",
    "start_radio",
    "ab_channel",
})
_MIN_SAVED_BYTES = 10_000


def social_video_key(url: str) -> str:
    """Stable id of one video. Tracking parameters and host aliases do not change it."""
    raw = str(url or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    host = _url_host(raw)
    path = parts.path or ""
    query = dict(parse_qsl(parts.query, keep_blank_values=False))
    if host == "youtu.be" or host.endswith("youtube.com") or host.endswith("youtube-nocookie.com"):
        video_id = ""
        if host == "youtu.be":
            video_id = path.strip("/").split("/")[0]
        else:
            video_id = query.get("v") or ""
            if not video_id:
                lowered = path.lower()
                for prefix in ("/shorts/", "/embed/", "/live/", "/v/"):
                    at = lowered.find(prefix)
                    if at >= 0:
                        video_id = path[at + len(prefix):].split("/")[0]
                        break
        video_id = re.sub(r"[^A-Za-z0-9_-]", "", video_id)[:11]
        if len(video_id) >= 6:
            return "yt:" + video_id
    if host == "instagram.com":
        match = re.search(r"/(?:reel|reels|p|tv)/([^/?#]+)", path, re.IGNORECASE)
        if match:
            code = re.sub(r"[^A-Za-z0-9_-]", "", match.group(1))
            if len(code) >= 5:
                return "ig:" + code
    if host in _FACEBOOK_HOSTS:
        token = query.get("story_fbid") or query.get("v") or query.get("fbid") or ""
        if host == "fb.watch":
            token = token or path.strip("/").split("/")[0]
        if not token:
            match = re.search(
                r"/(?:videos|reel|watch|posts|share/[rv]|[^/]+/videos)/([^/?#]+)",
                path,
                re.IGNORECASE,
            )
            if match:
                token = match.group(1)
        token = re.sub(r"[^A-Za-z0-9_-]", "", token)[:80]
        if len(token) >= 5:
            return "fb:" + token
    kept = []
    for key, value in parse_qsl(parts.query, keep_blank_values=False):
        low = key.lower()
        if low in _TRACKING_KEYS or low.startswith("utm_"):
            continue
        kept.append(f"{key}={value}")
    clean_path = path.rstrip("/") or "/"
    base = host + clean_path
    if kept:
        return base + "?" + "&".join(kept)
    return base


def link_keys(url: str) -> List[str]:
    """Seen-store keys: the stable video id, then the exact URL."""
    raw = str(url or "").strip()
    keys = []
    ident = social_video_key(raw)
    if ident:
        keys.append("url:" + ident)
    exact = "url:" + raw if raw else ""
    if exact and exact not in keys:
        keys.append(exact)
    return keys


def _file_is_saved(path: str, min_size: int = 1) -> bool:
    try:
        return bool(path) and os.path.isfile(path) and os.path.getsize(path) >= min_size
    except OSError:
        return False


def _key_matches(file_key: str, url: str, ident: str) -> bool:
    if not str(file_key).startswith("url:"):
        return False
    stored = file_key[4:]
    if stored == url or (ident and stored == ident):
        return True
    return bool(ident) and social_video_key(stored) == ident


def _disk_token(ident: str) -> str:
    prefix, _, token = (ident or "").partition(":")
    if prefix in ("yt", "ig", "fb") and len(token) >= 8:
        return token
    return ""


def _find_named_media(root: str, token: str) -> str:
    """A previous yt-dlp or fdownloader file named after this video id."""
    if not root or not token or not os.path.isdir(root):
        return ""
    token_cf = token.casefold()
    best = ""
    best_size = 0
    root = os.path.abspath(root)
    try:
        walker = os.walk(root)
    except OSError:
        return ""
    for dirpath, dirnames, filenames in walker:
        rel = os.path.relpath(dirpath, root)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth >= 2:
            dirnames[:] = []
        for name in filenames:
            if name.endswith(".part") or not name.lower().endswith(_MEDIA_EXTS):
                continue
            stem = os.path.splitext(name)[0].casefold()
            if stem != token_cf and not stem.startswith(token_cf + "_fdown"):
                continue
            path = os.path.join(dirpath, name)
            if not _file_is_saved(path, _MIN_SAVED_BYTES):
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if size > best_size:
                best, best_size = path, size
    return best


def find_existing_video(url: str, search_roots: Sequence[str] = ()) -> str:
    """Path of this video if it was already saved. Empty when it has to be downloaded."""
    ident = social_video_key(url)
    candidates = []
    try:
        from whisperfast.telegram.seen import _load

        for file_key, row in _load().items():
            if not isinstance(row, dict) or not _key_matches(str(file_key), url, ident):
                continue
            path = str(row.get("path") or "")
            if _file_is_saved(path):
                candidates.append(path)
    except Exception:
        pass
    for row in _load_saved_links():
        if not isinstance(row, dict):
            continue
        stored = str(row.get("url") or "")
        if stored != url and social_video_key(stored) != ident:
            continue
        path = str(row.get("path") or "")
        if _file_is_saved(path):
            candidates.append(path)
    token = _disk_token(ident)
    if token:
        seen_roots = set()
        for root in search_roots:
            if not root:
                continue
            root = os.path.abspath(root)
            if root in seen_roots:
                continue
            seen_roots.add(root)
            found = _find_named_media(root, token)
            if found:
                candidates.append(found)
    if not candidates:
        return ""

    def _size(path: str) -> int:
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    return max(candidates, key=_size)


def link_will_download(url: str, search_roots: Sequence[str] = ()) -> bool:
    """False when the video is already on disk or its results are already known."""
    if find_existing_video(url, search_roots):
        return False
    from whisperfast.telegram.seen import classify

    for key in link_keys(url):
        if classify(key)[0] != "download":
            return False
    return True


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


def _url_host(url: str) -> str:
    host = urlsplit(url).netloc.lower().split(":")[0]
    for prefix in ("www.", "m.", "web."):
        if host.startswith(prefix):
            host = host[len(prefix) :]
            break
    return host


def is_facebook_url(url: str) -> bool:
    return _url_host(url) in _FACEBOOK_HOSTS


def is_facebook_stories_url(url: str) -> bool:
    """Ephemeral Facebook Stories (/stories/...) — not supported by yt-dlp."""
    if not is_facebook_url(url):
        return False
    path = urlsplit(url).path.lower()
    return "/stories/" in path or path.rstrip("/").endswith("/stories")


def _cookies_file() -> str:
    return os.path.join(BASE_DIR, "downloads", "facebook", "cookies.txt")


def _looks_like_auth_failure(output: str) -> bool:
    low = (output or "").lower()
    return any(hint in low for hint in _AUTH_HINTS)


def _friendly_download_error(url: str, output: str) -> str:
    from whisperfast.i18n import t

    if is_facebook_stories_url(url) or (
        is_facebook_url(url) and "/stories/" in (output or "").lower()
    ):
        return t("telegram_link_facebook_stories")
    if is_facebook_url(url) and _looks_like_auth_failure(output):
        return t("telegram_link_facebook_auth")
    if "No module named" in (output or ""):
        return output[-400:]
    return (output or "").strip()[-400:] or "yt-dlp produced no file"


def _run_yt_dlp(cmd: Sequence[str], dest_dir: str, started: float) -> Tuple[str, str]:
    """Run yt-dlp once. Returns (media_path_or_empty, combined_output)."""
    try:
        proc = subprocess.Popen(
            list(cmd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **win_no_window_kwargs(),
        )
        stdout, stderr = proc.communicate(timeout=600)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        proc.communicate()
        raise RuntimeError("timed out after 10 minutes") from exc
    except OSError as exc:
        raise RuntimeError(str(exc)) from exc
    result_stdout, result_stderr = stdout or "", stderr or ""
    output = (result_stderr + "\n" + result_stdout).strip()
    path = newest_media_file(dest_dir, not_before=started - 1)
    if not path:
        for line in reversed(result_stdout.splitlines()):
            line = line.strip().strip('"')
            if line and os.path.isfile(line) and line.lower().endswith(_MEDIA_EXTS):
                path = line
                break
    if path and os.path.isfile(path):
        return path, output
    return "", output


def _base_yt_dlp_cmd(dest_dir: str, quality: str | None) -> List[str]:
    template = os.path.join(dest_dir, "%(id)s.%(ext)s")
    return [
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


def download_video(url: str, dest_dir: str, quality: str | None = None, log=None) -> str:
    """Save one video with yt-dlp. A Facebook failure then tries fdownloader.net."""
    if is_facebook_stories_url(url):
        from whisperfast.i18n import t

        raise RuntimeError(t("telegram_link_facebook_stories"))

    os.makedirs(dest_dir, exist_ok=True)
    started = time.time()
    base = _base_yt_dlp_cmd(dest_dir, quality)
    cookies = _cookies_file()
    auth_args: List[str] = []
    if os.path.isfile(cookies):
        auth_args = ["--cookies", cookies]

    output = ""
    try:
        path, output = _run_yt_dlp(base + auth_args + [url], dest_dir, started)
        if path:
            return path

        # A logged-in browser session still helps some videos. Any other
        # failure, including "Cannot parse data", goes to the site below.
        if (
            is_facebook_url(url)
            and _looks_like_auth_failure(output)
            and not auth_args
        ):
            for browser in _BROWSER_COOKIE_CANDIDATES:
                path, retry_out = _run_yt_dlp(
                    base + ["--cookies-from-browser", browser] + [url],
                    dest_dir,
                    started,
                )
                if path:
                    return path
                output = retry_out or output
    except RuntimeError as exc:
        output = str(exc)

    if is_facebook_url(url):
        from whisperfast.i18n import t
        from whisperfast.telegram.fdownloader import save_via_fdownloader

        if log is not None:
            try:
                log(t("telegram_link_fdownloader"))
            except Exception:
                pass
        try:
            return save_via_fdownloader(url, dest_dir)
        except Exception as exc:
            site_error = str(exc).strip()
            if site_error:
                output = (output + "\n" + site_error).strip()

    raise RuntimeError(_friendly_download_error(url, output))


def report_link_failure(log, exc, retry) -> None:
    """Log the download error and, in the GUI, a clickable Restart line."""
    from whisperfast.i18n import t

    log(t("telegram_link_failed", error=str(exc)))
    owner = getattr(log, "__self__", None)
    offer = getattr(owner, "offer_link_retry", None)
    if callable(offer):
        offer(retry)


def claim_link(url: str, dest_dir: str, log=None):
    """download, enqueue, wait, or send. An existing file for this video is reused."""
    from whisperfast.i18n import t
    from whisperfast.telegram.seen import classify, note_download

    keys = link_keys(url)
    roots = []
    if dest_dir:
        folder = os.path.abspath(dest_dir)
        roots.append(folder)
        roots.append(os.path.dirname(folder))
    saved = find_existing_video(url, roots)
    if saved:
        for key in keys:
            note_download(key, saved)
        if log is not None:
            try:
                log(t("telegram_link_already", name=os.path.basename(saved)))
            except Exception:
                pass
    action, known = "download", None
    for key in keys:
        action, known = classify(key)
        if action != "download":
            break
    if action == "download":
        path = download_video(url, dest_dir, log=log)
        for key in keys:
            note_download(key, path)
        return "new", path, None
    if action == "wait":
        return "wait", "", known
    if action == "enqueue":
        return "enqueue", str((known or {}).get("path") or saved or ""), known
    return "send", "", known


_own_uploads = set()


def social_videos_go_to_queue(settings) -> bool:
    return bool(settings.get("telegram_social_to_queue"))


def log_downloaded_file(log, path: str) -> None:
    """Log the saved path as a clickable link (open file / Shift+click folder)."""
    if not path:
        return
    try:
        shown = os.path.abspath(path)
    except OSError:
        shown = path
    try:
        log(shown, tag="link")
    except TypeError:
        log(shown)


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
