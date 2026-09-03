#!/usr/bin/env python3
"""Download Facebook videos / reels / share links with yt-dlp.

Usage:
  python scripts/download_facebook_videos.py
  python scripts/download_facebook_videos.py --browser chrome
  python scripts/download_facebook_videos.py --urls scripts/facebook_urls.txt --out downloads/facebook
  python scripts/download_facebook_videos.py URL [URL ...]

Share-links (facebook.com/share/r/..., /share/v/...) almost always need a logged-in
Facebook session. Pass --browser chrome|edge|firefox (the browser where you are
logged into facebook.com). Without cookies, most of these URLs fail with
"Cannot parse data" or a login wall.

Requires: Python 3.9+, yt-dlp, ffmpeg.
"""
from __future__ import annotations

import argparse
import os
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
ARCHIVE_NAME = "downloaded.txt"

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


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Скачать видео Facebook (share / reel / watch) через yt-dlp.",
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

    browser = None if args.no_cookies or args.cookies else args.browser
    cookies = Path(args.cookies) if args.cookies else None
    if cookies and not cookies.is_file():
        raise SystemExit(f"Файл cookies не найден: {cookies}")

    if browser:
        print(
            f"Cookies: из браузера {browser} "
            "(закройте браузер, если yt-dlp не сможет прочитать профиль).",
            flush=True,
        )

    extra: List[str] = []
    ok: List[str] = []
    failed: List[Tuple[str, str]] = []

    for i, url in enumerate(urls, 1):
        print(f"\n======== [{i}/{len(urls)}] {url} ========", flush=True)
        base = build_ydl_args(
            yt_dlp,
            out_dir,
            browser=browser,
            cookies=cookies,
            impersonate=impersonate,
            extra=extra,
        )
        success, reason = download_one(base, url)
        if success:
            ok.append(url)
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
            "\nЕсли Facebook требует вход: откройте facebook.com в Chrome, "
            "залогиньтесь и запустите снова с --browser chrome.\n"
            "Альтернатива — экспорт cookies.txt расширением «Get cookies.txt LOCALLY» "
            "и флаг --cookies путь\\к\\cookies.txt",
            flush=True,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
