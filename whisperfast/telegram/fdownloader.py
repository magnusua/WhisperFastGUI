"""Download a Facebook video via https://fdownloader.net/ru when yt-dlp fails.

Cloudflare blocks a bare HTTP call, so this opens the installed Chrome,
pastes the link, and saves the file the page offers.
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import parse_qsl, urlsplit

from whisperfast.platform_util import win_no_window_kwargs

FDOWNLOADER_PAGE = "https://fdownloader.net/ru"
_PLAYWRIGHT_READY: Optional[bool] = None


def _share_stem(url: str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    tail = query.get("story_fbid") or parts.path.rstrip("/").split("/")[-1] or "video"
    tail = re.sub(r"[^A-Za-z0-9._-]+", "_", tail)[:80]
    return tail or "video"


def _existing_fdown(out_dir: Path, stem: str) -> Optional[Path]:
    for path in sorted(out_dir.glob(f"{stem}_fdown_*.mp4")):
        try:
            if path.stat().st_size > 10_000:
                return path
        except OSError:
            continue
    return None


def _quality_from_snap_url(url: str) -> str:
    try:
        token = url.split("token=", 1)[1].split(".", 2)[1]
        payload = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
        name = str(payload.get("filename") or "")
        match = re.search(r"(\d{3,4}p)", name, re.IGNORECASE)
        if match:
            return match.group(1).lower()
    except (IndexError, ValueError, json.JSONDecodeError):
        pass
    return "mp4"


def _save_snap_file(href: str, dest: Path) -> None:
    request = urllib.request.Request(
        href,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
            ),
            "Referer": "https://fdownloader.net/",
        },
    )
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(request, timeout=180) as response, tmp.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 256)
            if not chunk:
                break
            handle.write(chunk)
    head = tmp.read_bytes()[:12]
    if b"ftyp" not in head:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("ответ fdownloader не похож на mp4")
    tmp.replace(dest)


def _ensure_playwright() -> bool:
    global _PLAYWRIGHT_READY
    if _PLAYWRIGHT_READY is not None:
        return _PLAYWRIGHT_READY
    try:
        import playwright  # noqa: F401
        _PLAYWRIGHT_READY = True
        return True
    except ImportError:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "playwright"],
            check=False,
            **win_no_window_kwargs(),
        )
        _PLAYWRIGHT_READY = result.returncode == 0
        return _PLAYWRIGHT_READY


def _fdown_href(url: str) -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel="chrome", headless=False)
        except Exception:
            browser = playwright.chromium.launch(headless=False)
        try:
            page = browser.new_page()
            page.goto(FDOWNLOADER_PAGE, wait_until="domcontentloaded", timeout=45000)
            box = page.locator(
                "input[placeholder*='Facebook'], input[type='text'], input[type='search']"
            ).first
            box.wait_for(timeout=45000)
            box.fill(url)
            page.get_by_role("button", name=re.compile(r"Скачать|Download")).first.click()
            link = page.locator("a.download-link-fb").first
            link.wait_for(timeout=45000)
            href = link.get_attribute("href") or ""
        finally:
            browser.close()
    if not href.startswith("http"):
        raise RuntimeError("fdownloader не вернул ссылку на файл")
    return href


def fetch_fdownloader(url: str, out_dir: Path) -> Tuple[Path, bool]:
    """Save one Facebook video from fdownloader.net. Raises RuntimeError on failure.

    The bool is True when a previous file was reused.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = _share_stem(url)
    existing = _existing_fdown(out_dir, stem)
    if existing:
        return existing, True
    if not _ensure_playwright():
        raise RuntimeError("playwright не установлен")
    href = _fdown_href(url)
    quality = _quality_from_snap_url(href)
    dest = out_dir / f"{stem}_fdown_{quality}.mp4"
    _save_snap_file(href, dest)
    return dest, False


def save_via_fdownloader(url: str, dest_dir) -> str:
    """Return the saved file path. Raises RuntimeError when the site does not deliver a file."""
    path, _existed = fetch_fdownloader(url, Path(dest_dir))
    return str(path)


def download_via_fdownloader(url: str, out_dir: Path) -> Tuple[bool, str]:
    """Script helper: never raises. Prints the saved path."""
    try:
        path, existed = fetch_fdownloader(url, Path(out_dir))
    except Exception as exc:
        return False, f"fdownloader: {exc}"
    print(path, flush=True)
    return True, "fdownloader (уже есть)" if existed else "fdownloader"
