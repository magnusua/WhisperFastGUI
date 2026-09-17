"""OBS-like capture filename tokens and clip duration (mm:ss)."""
from __future__ import annotations

import os
import random
import re
import string
import time
from datetime import datetime
from typing import Optional

_TOKEN_RE = re.compile(r"%[A-Za-z]")
_UNSAFE = re.compile(r'[\\/:*?"<>|]')


def sanitize_filename(name: str) -> str:
    s = _UNSAFE.sub("_", name or "")
    s = s.strip().rstrip(". ")
    s = re.sub(r"\s+", " ", s)
    return s if s else "FTW"


def parse_clip_seconds(text: str, fallback: int = 122) -> int:
    """`2:02` → 122, `122` → 122, `5` → 5. Invalid → fallback."""
    raw = (text or "").strip()
    if not raw:
        return max(0, int(fallback))
    if ":" in raw:
        parts = raw.split(":")
        try:
            nums = [int(p) for p in parts]
        except ValueError:
            return max(0, int(fallback))
        if len(nums) == 2:
            return max(0, nums[0] * 60 + nums[1])
        if len(nums) == 3:
            return max(0, nums[0] * 3600 + nums[1] * 60 + nums[2])
        return max(0, int(fallback))
    try:
        return max(0, int(float(raw.replace(",", "."))))
    except ValueError:
        return max(0, int(fallback))


def format_clip_seconds(seconds: int) -> str:
    n = max(0, int(seconds))
    m, s = divmod(n, 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def render_filename(
    template: str,
    when: Optional[datetime] = None,
    *,
    window_title: str = "FTW",
    calendar_title: str = "",
    use_calendar: bool = False,
) -> str:
    when = when or datetime.now()
    stamp = int(time.time())
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=5))
    win = sanitize_filename(window_title or "FTW")
    cal = sanitize_filename(calendar_title) if calendar_title else ""
    if use_calendar and cal:
        tmpl = (template or "").strip() or "%C %Y-%m-%d %H.%M.%S"
        if "%C" not in tmpl:
            tmpl = "%C %Y-%m-%d %H.%M.%S"
    else:
        tmpl = (template or "").strip() or "%W %Y-%m-%d %H.%M.%S"
        if use_calendar and not cal and tmpl.strip().startswith("%C"):
            tmpl = "%W %Y-%m-%d %H.%M.%S"

    mapping = {
        "%Y": when.strftime("%Y"),
        "%y": when.strftime("%y"),
        "%m": when.strftime("%m"),
        "%d": when.strftime("%d"),
        "%H": when.strftime("%H"),
        "%M": when.strftime("%M"),
        "%S": when.strftime("%S"),
        "%W": win,
        "%C": cal,
        "%R": rand,
        "%s": str(stamp),
    }

    def repl(match: re.Match) -> str:
        token = match.group(0)
        return mapping.get(token, token)

    name = _TOKEN_RE.sub(repl, tmpl)
    return sanitize_filename(name)
