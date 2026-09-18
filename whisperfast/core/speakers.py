"""Speaker display names, speakers.json, and quote-filtered LLM naming."""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Iterable, List, Tuple

SPEAKER_NEAR = "SPEAKER_00"
SPEAKER_FAR = "SPEAKER_01"
DEFAULT_YOU = "You"
DEFAULT_THEM = "Them"
MANUAL = "manual"
DEFAULT_SRC = "default"
LLM_SRC = "llm"

_QUOTE_CHARS = "\"'«»„“”"


def speakers_json_path(transcript_path: str) -> str:
    base, _ = os.path.splitext(transcript_path or "")
    if not base:
        return ""
    return base + ".speakers.json"


def default_speakers_map(user_name: str = DEFAULT_YOU, them_name: str = DEFAULT_THEM) -> Dict[str, dict]:
    you = (user_name or "").strip() or DEFAULT_YOU
    them = (them_name or "").strip() or DEFAULT_THEM
    return {
        SPEAKER_NEAR: {
            "id": SPEAKER_NEAR,
            "display": you,
            "source": DEFAULT_SRC,
            "confidence": "high",
            "quote": "",
        },
        SPEAKER_FAR: {
            "id": SPEAKER_FAR,
            "display": them,
            "source": DEFAULT_SRC,
            "confidence": "high",
            "quote": "",
        },
    }


def load_speakers(path: str) -> Dict[str, dict]:
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    raw = data.get("speakers") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        if isinstance(data, dict):
            raw = {k: v for k, v in data.items() if isinstance(v, dict)}
        else:
            return {}
    out = {}
    for key, val in raw.items():
        if not isinstance(val, dict):
            continue
        sid = str(val.get("id") or key)
        out[sid] = {
            "id": sid,
            "display": str(val.get("display") or val.get("name") or sid),
            "source": str(val.get("source") or DEFAULT_SRC),
            "confidence": str(val.get("confidence") or ""),
            "quote": str(val.get("quote") or ""),
        }
    return out


def save_speakers(path: str, speakers: Dict[str, dict]) -> str:
    if not path:
        return ""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    payload = {"speakers": dict(speakers)}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path


def merge_speakers(existing: Dict[str, dict], incoming: Dict[str, dict]) -> Dict[str, dict]:
    """Manual entries win; other keys are updated from incoming."""
    out = {k: dict(v) for k, v in (existing or {}).items()}
    for key, val in (incoming or {}).items():
        prev = out.get(key) or {}
        if str(prev.get("source") or "") == MANUAL:
            merged = dict(val)
            merged["display"] = prev.get("display") or merged.get("display")
            merged["source"] = MANUAL
            merged["quote"] = prev.get("quote") or merged.get("quote") or ""
            out[key] = merged
            continue
        out[key] = dict(val)
    return out


def display_for(speaker_id: str, speakers: Dict[str, dict], fallback: str = "") -> str:
    row = (speakers or {}).get(speaker_id) or {}
    name = str(row.get("display") or "").strip()
    return name or fallback or speaker_id


def apply_display_names(segments: List[Any], speakers: Dict[str, dict]) -> List[Any]:
    mapping = {}
    for sid, row in (speakers or {}).items():
        mapping[sid] = str(row.get("display") or sid)
        mapping[str(row.get("display") or "")] = str(row.get("display") or sid)
    for seg in segments or []:
        raw = getattr(seg, "speaker", None) or ""
        if not raw:
            continue
        if raw in mapping:
            try:
                seg.speaker = mapping[raw]
            except Exception:
                pass
    return segments


def quote_in_transcript(quote: str, transcript: str) -> bool:
    q = _normalize_quote(quote)
    if len(q) < 4:
        return False
    body = _normalize_quote(transcript or "")
    return q in body


def _normalize_quote(text: str) -> str:
    s = (text or "").strip().strip(_QUOTE_CHARS)
    s = re.sub(r"\s+", " ", s).casefold()
    return s


def parse_llm_speaker_names(text: str) -> List[dict]:
    """Extract speaker naming candidates from JSON or markdown-ish LLM output."""
    text = (text or "").strip()
    if not text:
        return []
    found: List[dict] = []
    found.extend(_parse_json_names(text))
    found.extend(_parse_line_names(text))
    dedup = {}
    for item in found:
        sid = str(item.get("id") or "").strip()
        name = str(item.get("display") or "").strip()
        if not sid or not name:
            continue
        key = sid
        prev = dedup.get(key)
        if prev and str(prev.get("source") or "") == MANUAL:
            continue
        dedup[key] = item
    return list(dedup.values())


def _parse_json_names(text: str) -> List[dict]:
    blob = text
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        blob = text[start : end + 1]
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return []
    rows = []
    if isinstance(data, dict) and isinstance(data.get("speakers"), dict):
        data = data["speakers"]
    if isinstance(data, dict):
        items = data.items()
    elif isinstance(data, list):
        items = []
        for row in data:
            if isinstance(row, dict):
                items.append((row.get("id") or row.get("speaker") or "", row))
    else:
        return []
    for key, val in items:
        if isinstance(val, str):
            val = {"display": val}
        if not isinstance(val, dict):
            continue
        sid = str(val.get("id") or val.get("speaker") or key or "").strip()
        if not sid:
            continue
        rows.append(
            {
                "id": _canon_speaker_id(sid),
                "display": str(val.get("display") or val.get("name") or "").strip(),
                "source": LLM_SRC,
                "confidence": str(val.get("confidence") or "high").strip().lower() or "high",
                "quote": str(val.get("quote") or val.get("citation") or "").strip(),
            }
        )
    return rows


_LINE_RE = re.compile(
    r"(SPEAKER[_\s-]?\d+|You|Them)\s*[:\-]\s*(.+)$",
    re.IGNORECASE,
)


def _parse_line_names(text: str) -> List[dict]:
    rows = []
    for raw in text.splitlines():
        line = raw.strip().strip("-*")
        m = _LINE_RE.match(line)
        if not m:
            continue
        sid = _canon_speaker_id(m.group(1))
        rest = m.group(2).strip()
        quote = ""
        q = re.search(r"[\"«„“']([^\"»”']{4,})[\"»”']", rest)
        if q:
            quote = q.group(1).strip()
            rest = (rest[: q.start()] + rest[q.end() :]).strip(" ,;—-")
        conf = "high"
        if re.search(r"\b(low|medium)\b", rest, re.I):
            conf = "medium" if re.search(r"\bmedium\b", rest, re.I) else "low"
        name = re.sub(r"\((high|medium|low)\)", "", rest, flags=re.I)
        name = re.sub(r"\b(high|medium|low)\b", "", name, flags=re.I)
        name = re.sub(r"\(\s*\)", "", name).strip(" ,;—-")
        if not name:
            continue
        rows.append(
            {
                "id": sid,
                "display": name,
                "source": LLM_SRC,
                "confidence": conf,
                "quote": quote,
            }
        )
    return rows


def _canon_speaker_id(value: str) -> str:
    s = (value or "").strip()
    m = re.search(r"(\d+)", s)
    if s.upper().replace(" ", "").startswith("SPEAKER") and m:
        return f"SPEAKER_{int(m.group(1)):02d}"
    if s.casefold() in ("you", "я", "я:"):
        return SPEAKER_NEAR
    if s.casefold() in ("them", "вони", "они"):
        return SPEAKER_FAR
    return s


def apply_llm_names(
    speakers: Dict[str, dict],
    candidates: Iterable[dict],
    transcript: str,
) -> Tuple[Dict[str, dict], List[str]]:
    """Keep only high-confidence names whose quote appears in the transcript. Never overwrite manual."""
    out = {k: dict(v) for k, v in (speakers or {}).items()}
    skipped = []
    for item in candidates or []:
        sid = _canon_speaker_id(str(item.get("id") or ""))
        name = str(item.get("display") or "").strip()
        conf = str(item.get("confidence") or "high").strip().lower()
        quote = str(item.get("quote") or "").strip()
        if not sid or not name:
            continue
        prev = out.get(sid) or {}
        if str(prev.get("source") or "") == MANUAL:
            skipped.append(sid)
            continue
        if conf != "high":
            skipped.append(sid)
            continue
        if not quote_in_transcript(quote, transcript):
            skipped.append(sid)
            continue
        row = dict(prev)
        row.update(
            {
                "id": sid,
                "display": name,
                "source": LLM_SRC,
                "confidence": "high",
                "quote": quote,
            }
        )
        out[sid] = row
    return out, skipped


def first_and_longest_utterances(
    segments: Iterable[Any], speaker_display: str
) -> Tuple[str, str]:
    first = ""
    longest = ""
    want = (speaker_display or "").strip().casefold()
    for seg in segments or []:
        spk = str(getattr(seg, "speaker", "") or "").strip()
        if spk.casefold() != want and spk != speaker_display:
            # also match raw ids if display not applied
            continue
        text = str(getattr(seg, "text", "") or "").strip()
        if not text:
            continue
        if not first:
            first = text
        if len(text) > len(longest):
            longest = text
    return first, longest


def utterances_for_id(
    segments: Iterable[Any], speaker_id: str, display: str
) -> Tuple[str, str]:
    first = ""
    longest = ""
    aliases = {speaker_id, display, (display or "").strip()}
    aliases = {a for a in aliases if a}
    for seg in segments or []:
        spk = str(getattr(seg, "speaker", "") or "").strip()
        if spk not in aliases:
            continue
        text = str(getattr(seg, "text", "") or "").strip()
        if not text:
            continue
        if not first:
            first = text
        if len(text) > len(longest):
            longest = text
    return first, longest


def rewrite_text_speakers(text: str, mapping: Dict[str, str]) -> str:
    if not text or not mapping:
        return text or ""
    out = text
    for src, dest in sorted(mapping.items(), key=lambda kv: len(kv[0]), reverse=True):
        if not src or src == dest:
            continue
        out = re.sub(rf"(^|\n){re.escape(src)}:", rf"\1{dest}:", out)
    return out
