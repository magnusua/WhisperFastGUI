#!/usr/bin/env python3
"""Download Facebook videos / reels / share links.

For each URL the script tries, in order:

  1. yt-dlp without cookies (public videos)
  2. yt-dlp with cookies (downloads/facebook/cookies.txt, --cookies, or --browser)
  3. https://fdownloader.net/ru in installed Chrome (Cloudflare check blocks a bare API call)

Usage:
  python scripts/download_facebook_videos.py
  python scripts/download_facebook_videos.py --browser chrome
  python scripts/download_facebook_videos.py --urls scripts/facebook_urls.txt --out downloads/facebook
  python scripts/download_facebook_videos.py URL [URL ...]

Requires: Python 3.9+, yt-dlp, ffmpeg. Step 3 also needs the playwright package
and Google Chrome; the script installs playwright on first use.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_URLS_FILE = Path(__file__).resolve().parent / "facebook_urls.txt"
DEFAULT_OUT_DIR = ROOT / "downloads" / "facebook"
DEFAULT_COOKIES = DEFAULT_OUT_DIR / "cookies.txt"
ARCHIVE_NAME = "downloaded.txt"
FDOWNLOADER_PAGE = "https://fdownloader.net/ru"
_PLAYWRIGHT_READY: Optional[bool] = None

URL_RE = re.compile(
    r"https?://(?:www\.|m\.|web\.)?(?:facebook\.com|fb\.watch|fb\.com|youtube\.com|youtu\.be)/[^\s<>\"']+",
    re.IGNORECASE,
)

TRAILING_PUNCT = ".,;:!?)>\"]'"
KEEP_QUERY = frozenset({"v", "story_fbid", "id"})


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def normalize_url(raw: str) -> str:
    raw = raw.rstrip(TRAILING_PUNCT)
    parts = urlsplit(raw)
    path = parts.path.rstrip("/") or "/"
    path_l = path.lower()
    keep_query = "watch" in path_l or path_l.endswith("story.php")
    query_pairs = []
    if keep_query:
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            if key.lower() in KEEP_QUERY:
                query_pairs.append((key, value))
    return urlunsplit(
        (
            (parts.scheme or "https").lower(),
            parts.netloc.lower(),
            path,
            urlencode(query_pairs),
            "",
        )
    )


def extract_urls(text: str) -> List[str]:
    seen = set()
    urls: List[str] = []
    for match in URL_RE.finditer(text):
        url = normalize_url(match.group(0))
        key = url.lower()
        if key in seen:
            continue
        seen.add(key)
        urls.append(url)
    return urls


def load_urls(path: Path) -> List[str]:
    return extract_urls(path.read_text(encoding="utf-8"))


def find_yt_dlp() -> List[str]:
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    return [sys.executable, "-m", "yt_dlp"]


def run(cmd: Sequence[str], check: bool = False) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(list(cmd), check=check)


def ensure_yt_dlp(update: bool) -> List[str]:
    cmd = find_yt_dlp()
    probe = subprocess.run(cmd + ["--version"], capture_output=True, text=True)
    if probe.returncode != 0:
        print("yt-dlp не найден. Ставлю пакет...", flush=True)
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-U", "yt-dlp[default]"],
            check=True,
        )
        cmd = find_yt_dlp()
        probe = subprocess.run(cmd + ["--version"], capture_output=True, text=True)
        if probe.returncode != 0:
            raise SystemExit("Не удалось запустить yt-dlp после установки.")
    version = (probe.stdout or probe.stderr).strip().splitlines()[0]
    print(f"yt-dlp {version}", flush=True)
    if update:
        print("Обновляю yt-dlp...", flush=True)
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-U", "yt-dlp[default]"],
            check=False,
        )
        probe = subprocess.run(cmd + ["--version"], capture_output=True, text=True)
        version = (probe.stdout or probe.stderr).strip().splitlines()[0]
        print(f"yt-dlp {version}", flush=True)
    return cmd


def ffmpeg_ok() -> bool:
    exe = shutil.which("ffmpeg")
    if not exe:
        print("Предупреждение: ffmpeg не найден в PATH. Склейка video+audio может не сработать.", flush=True)
        return False
    print(f"ffmpeg: {exe}", flush=True)
    return True


def impersonate_available(yt_dlp: Sequence[str], target: str = "chrome") -> bool:
    probe = subprocess.run(
        list(yt_dlp) + ["--list-impersonate-targets"],
        capture_output=True,
        text=True,
    )
    out = (probe.stdout or "") + (probe.stderr or "")
    if probe.returncode != 0:
        return False
    needle = target.lower()
    for line in out.splitlines():
        low = line.lower()
        if needle not in low:
            continue
        if "unavailable" in low:
            return False
        return True
    return False


def build_ydl_args(
    yt_dlp: Sequence[str],
    out_dir: Path,
    *,
    browser: Optional[str],
    cookies: Optional[Path],
    impersonate: Optional[str],
    extra: Sequence[str],
) -> List[str]:
    args: List[str] = [
        *yt_dlp,
        "--no-warnings",
        "--newline",
        "--ignore-errors",
        "--no-overwrites",
        "--restrict-filenames",
        "--windows-filenames",
        "--merge-output-format",
        "mp4",
        "-f",
        "bv*+ba/b",
        "--download-archive",
        str(out_dir / ARCHIVE_NAME),
        "-o",
        str(out_dir / "%(id)s_%(title).80s.%(ext)s"),
        "--print",
        "after_move:filepath",
    ]
    if impersonate:
        args.extend(["--impersonate", impersonate])
    if cookies:
        args.extend(["--cookies", str(cookies)])
    elif browser:
        args.extend(["--cookies-from-browser", browser])
    args.extend(extra)
    return args


def download_one(base_args: Sequence[str], url: str) -> Tuple[bool, str]:
    result = subprocess.run(list(base_args) + ["--", url])
    if result.returncode == 0:
        return True, "ok"
    return False, f"exit {result.returncode}"


def _facebook_url(url: str) -> bool:
    host = urlsplit(url).netloc.lower()
    return host.endswith("facebook.com") or host.endswith("fb.watch") or host.endswith("fb.com")


def _share_stem(url: str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    tail = query.get("story_fbid") or parts.path.rstrip("/").split("/")[-1] or "video"
    tail = re.sub(r"[^A-Za-z0-9._-]+", "_", tail)[:80]
    return tail or "video"


def _existing_fdown(out_dir: Path, stem: str) -> Optional[Path]:
    for path in sorted(out_dir.glob(f"{stem}_fdown_*.mp4")):
        if path.stat().st_size > 10_000:
            return path
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
    import requests

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
        ),
        "Referer": "https://fdownloader.net/",
    }
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(href, headers=headers, stream=True, timeout=180) as response:
        response.raise_for_status()
        with tmp.open("wb") as handle:
            for chunk in response.iter_content(1024 * 256):
                if chunk:
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
        print("Ставлю playwright для fdownloader.net...", flush=True)
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "playwright"],
            check=False,
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
            box = page.locator("input[placeholder*='Facebook'], input[type='text'], input[type='search']").first
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


def download_via_fdownloader(url: str, out_dir: Path) -> Tuple[bool, str]:
    stem = _share_stem(url)
    existing = _existing_fdown(out_dir, stem)
    if existing:
        print(existing, flush=True)
        return True, "fdownloader (уже есть)"
    if not _ensure_playwright():
        return False, "playwright не установлен"
    try:
        href = _fdown_href(url)
        quality = _quality_from_snap_url(href)
        dest = out_dir / f"{stem}_fdown_{quality}.mp4"
        _save_snap_file(href, dest)
        print(dest, flush=True)
        return True, "fdownloader"
    except Exception as exc:
        return False, f"fdownloader: {exc}"


def download_with_fallback(
    yt_dlp: Sequence[str],
    url: str,
    out_dir: Path,
    *,
    browser: Optional[str],
    cookies: Optional[Path],
    impersonate: Optional[str],
    use_fdown: bool,
) -> Tuple[bool, str]:
    print("  1/3 yt-dlp", flush=True)
    plain = build_ydl_args(
        yt_dlp, out_dir, browser=None, cookies=None, impersonate=impersonate, extra=[],
    )
    ok, reason = download_one(plain, url)
    if ok:
        return True, "yt-dlp"
    print(f"  не вышло ({reason})", flush=True)

    if cookies or browser:
        source = str(cookies) if cookies else f"браузер {browser}"
        print(f"  2/3 cookies ({source})", flush=True)
        with_cookies = build_ydl_args(
            yt_dlp, out_dir, browser=browser, cookies=cookies, impersonate=impersonate, extra=[],
        )
        ok, reason = download_one(with_cookies, url)
        if ok:
            return True, "cookies"
        print(f"  не вышло ({reason})", flush=True)
    else:
        print("  2/3 cookies пропущены: нет cookies.txt и браузер отключён", flush=True)

    if not use_fdown or not _facebook_url(url):
        return False, reason
    print(f"  3/3 {FDOWNLOADER_PAGE}", flush=True)
    return download_via_fdownloader(url, out_dir)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Скачать видео Facebook: yt-dlp, затем cookies, затем fdownloader.net."
        ),
    )
    parser.add_argument(
        "urls",
        nargs="*",
        help="Ссылки Facebook. Если не указаны — берётся файл --urls.",
    )
    parser.add_argument(
        "--urls-file",
        "--urls",
        dest="urls_file",
        default=str(DEFAULT_URLS_FILE),
        help=f"Файл со ссылками или логом чата (по умолчанию: {DEFAULT_URLS_FILE})",
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT_DIR),
        help=f"Папка для видео (по умолчанию: {DEFAULT_OUT_DIR})",
    )
    parser.add_argument(
        "--browser",
        choices=("chrome", "edge", "firefox", "brave", "opera", "chromium"),
        default="chrome",
        help="Браузер, в котором вы залогинены в Facebook (для cookies). По умолчанию: chrome.",
    )
    parser.add_argument(
        "--no-cookies",
        action="store_true",
        help="Не брать cookies из браузера (только публичные ролики).",
    )
    parser.add_argument(
        "--cookies",
        default=None,
        help="Путь к cookies.txt вместо --browser.",
    )
    parser.add_argument(
        "--impersonate",
        default="chrome",
        help="TLS-отпечаток для yt-dlp (по умолчанию: chrome). Пустая строка — выключить.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=2.0,
        help="Пауза в секундах между роликами (по умолчанию: 2).",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Обновить yt-dlp перед скачиванием.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать список ссылок, ничего не качать.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Скачать только первые N ссылок (0 = все).",
    )
    parser.add_argument(
        "--no-fdown",
        action="store_true",
        help="Не открывать fdownloader.net, если yt-dlp и cookies не сработали.",
    )
    return parser.parse_args(argv)


def collect_urls(args: argparse.Namespace) -> List[str]:
    urls: List[str] = []
    if args.urls:
        urls.extend(extract_urls("\n".join(args.urls)))
    else:
        path = Path(args.urls_file)
        if not path.is_file():
            raise SystemExit(f"Файл со ссылками не найден: {path}")
        urls.extend(load_urls(path))
    if not urls:
        raise SystemExit("Не найдено ни одной ссылки Facebook.")
    if args.limit and args.limit > 0:
        urls = urls[: args.limit]
    return urls


def main(argv: Optional[Sequence[str]] = None) -> int:
    _configure_stdio()
    args = parse_args(argv)
    urls = collect_urls(args)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Ссылок: {len(urls)}", flush=True)
    print(f"Папка:  {out_dir}", flush=True)
    for i, url in enumerate(urls, 1):
        print(f"  {i:02d}. {url}", flush=True)

    if args.dry_run:
        return 0

    yt_dlp = ensure_yt_dlp(update=args.update)
    ffmpeg_ok()

    impersonate = (args.impersonate or "").strip() or None
    if impersonate and not impersonate_available(yt_dlp, impersonate):
        print(
            "Impersonate недоступен (нужен пакет curl_cffi). "
            "Пробую без него. При ошибках Facebook поставьте:\n"
            f"  {sys.executable} -m pip install -U \"yt-dlp[default,curl-cffi]\"",
            flush=True,
        )
        impersonate = None

    if args.cookies:
        cookies: Optional[Path] = Path(args.cookies)
        if not cookies.is_file():
            raise SystemExit(f"Файл cookies не найден: {cookies}")
    elif not args.no_cookies and DEFAULT_COOKIES.is_file():
        cookies = DEFAULT_COOKIES
    else:
        cookies = None
    browser = None if args.no_cookies or cookies else args.browser

    print("Порядок: 1) yt-dlp  2) cookies  3) fdownloader.net", flush=True)
    if cookies:
        print(f"Cookies-файл: {cookies}", flush=True)
    elif browser:
        print(f"Cookies: браузер {browser}, если обычная загрузка не сработает.", flush=True)
    if args.no_fdown:
        print("fdownloader.net выключен (--no-fdown).", flush=True)

    ok: List[str] = []
    failed: List[Tuple[str, str]] = []

    for i, url in enumerate(urls, 1):
        print(f"\n======== [{i}/{len(urls)}] {url} ========", flush=True)
        success, reason = download_with_fallback(
            yt_dlp,
            url,
            out_dir,
            browser=browser,
            cookies=cookies,
            impersonate=impersonate,
            use_fdown=not args.no_fdown,
        )
        if success:
            ok.append(url)
            print(f"  готово: {reason}", flush=True)
        else:
            failed.append((url, reason))
        if i < len(urls) and args.sleep > 0:
            time.sleep(args.sleep)

    print("\n======== Итог ========", flush=True)
    print(f"Успешно: {len(ok)} / {len(urls)}", flush=True)
    print(f"Файлы:   {out_dir}", flush=True)
    if failed:
        print("Не скачалось:", flush=True)
        for url, reason in failed:
            print(f"  - {url}  ({reason})", flush=True)
        print(
            "\nПоложите cookies.txt в downloads/facebook "
            "(экспорт «Get cookies.txt LOCALLY») и запустите снова. "
            "Если и cookies не помогут, скрипт откроет fdownloader.net.",
            flush=True,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
