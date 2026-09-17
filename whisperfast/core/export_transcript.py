"""Write transcript sidecars: JSON segments and WebVTT."""
from __future__ import annotations

import json
from typing import Any, Iterable, List, Optional

from whisperfast.utils import format_timestamp_srt


def segment_to_dict(seg: Any) -> dict:
    speaker = getattr(seg, "speaker", None) or ""
    words = getattr(seg, "words", None) or []
    word_list = []
    for w in words:
        if isinstance(w, dict):
            word_list.append(w)
        else:
            word_list.append(
                {
                    "start": float(getattr(w, "start", 0) or 0),
                    "end": float(getattr(w, "end", 0) or 0),
                    "word": str(getattr(w, "word", "") or ""),
                }
            )
    text = (getattr(seg, "text", None) or "").strip()
    return {
        "start": float(getattr(seg, "start", 0) or 0),
        "end": float(getattr(seg, "end", 0) or 0),
        "text": text,
        "speaker": speaker,
        "words": word_list,
    }


def segments_to_json_obj(segments: Iterable[Any], extra: Optional[dict] = None) -> dict:
    items = [segment_to_dict(s) for s in segments]
    obj = {"segments": items}
    if extra:
        obj.update(extra)
    return obj


def write_segments_json(path: str, segments: Iterable[Any], extra: Optional[dict] = None) -> str:
    obj = segments_to_json_obj(segments, extra=extra)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path


def _cue_text(seg: Any) -> str:
    speaker = getattr(seg, "speaker", None) or ""
    text = (getattr(seg, "text", None) or "").strip()
    if speaker:
        return f"{speaker}: {text}"
    return text


def write_vtt(path: str, segments: Iterable[Any]) -> str:
    lines: List[str] = ["WEBVTT", ""]
    for i, s in enumerate(segments, 1):
        start = format_timestamp_srt(float(getattr(s, "start", 0) or 0))
        end = format_timestamp_srt(float(getattr(s, "end", 0) or 0))
        lines.append(str(i))
        lines.append(f"{start} --> {end}")
        lines.append(_cue_text(s))
        lines.append("")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
        if not lines[-1].endswith("\n"):
            f.write("\n")
    return path


def format_segment_line(seg: Any) -> str:
    speaker = getattr(seg, "speaker", None) or ""
    text = (getattr(seg, "text", None) or "").strip()
    if speaker:
        return f"{speaker}: {text}"
    return text
