"""Parse SRT subtitle files into timed cue dicts."""
from __future__ import annotations

import re
from typing import Dict, List

from whisperfast.utils import parse_timestamp_to_seconds

_ARROW = re.compile(r"\s*-->\s*")


def parse_srt(text: str) -> List[Dict[str, object]]:
    """Return [{index, start, end, text}, ...] from SRT content."""
    cues: List[Dict[str, object]] = []
    if not text or not str(text).strip():
        return cues
    blocks = re.split(r"\r?\n\r?\n", text.strip())
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip() != "" or True]
        lines = block.splitlines()
        if not lines:
            continue
        idx = 0
        time_line = ""
        text_lines: List[str] = []
        if lines[0].strip().isdigit():
            idx = int(lines[0].strip())
            rest = lines[1:]
        else:
            rest = lines
        if not rest:
            continue
        time_line = rest[0]
        text_lines = rest[1:]
        parts = _ARROW.split(time_line, maxsplit=1)
        if len(parts) != 2:
            continue
        start = parse_timestamp_to_seconds(parts[0].strip())
        end = parse_timestamp_to_seconds(parts[1].strip())
        if start is None:
            start = 0.0
        if end is None:
            end = start
        body = "\n".join(text_lines).strip()
        cues.append(
            {
                "index": idx or (len(cues) + 1),
                "start": float(start),
                "end": float(end),
                "text": body,
            }
        )
    return cues


def parse_srt_file(path: str) -> List[Dict[str, object]]:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return parse_srt(f.read())
