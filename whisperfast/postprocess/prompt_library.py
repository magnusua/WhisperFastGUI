"""AI prompt library: one JSON file per prompt in BASE_DIR/promts/.

Only ``num``, ``name`` and ``body`` are sent to the model. Fields such as
``hint`` / ``description`` are UI-only and must never be concatenated into
the request.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from whisperfast.config import BASE_DIR
from whisperfast.open_path import open_file

PROMPTS_DIRNAME = "promts"
LogFunc = Callable[..., None]
PromptTuple = Tuple[int, str, str]

# Never sent to the model — hover text / docs in the JSON files.
_UI_ONLY_KEYS = frozenset({"hint", "description", "desc", "notes", "comment"})

_PROMPT_HEADER_RE = re.compile(
    r'^##\s*(?:Промпт|Prompt)\s*[№#]?\s*(\d+)\s*(?:"([^"]*)"|\'([^\']*)\')?\s*$',
    re.IGNORECASE | re.MULTILINE,
)


@dataclass
class PromptSpec:
    num: int
    name: str
    body: str
    hint: Dict[str, str] = field(default_factory=dict)
    path: str = ""

    def as_tuple(self) -> PromptTuple:
        return (self.num, self.name, self.body)

    def hint_for(self, lang: str) -> str:
        lang = (lang or "EN").upper()
        if self.hint.get(lang):
            return str(self.hint[lang]).strip()
        for fallback in ("EN", "UK", "RU"):
            if self.hint.get(fallback):
                return str(self.hint[fallback]).strip()
        return ""


def prompts_dir(root: Optional[str] = None) -> str:
    return os.path.join(root or BASE_DIR, PROMPTS_DIRNAME)


def ensure_prompt_library(root: Optional[str] = None) -> str:
    """Create ``promts/`` if missing. Returns the directory path."""
    path = prompts_dir(root)
    os.makedirs(path, exist_ok=True)
    return path


def _hint_from_obj(obj: dict) -> Dict[str, str]:
    raw = None
    for key in ("hint", "description", "desc"):
        if key in obj and obj[key]:
            raw = obj[key]
            break
    if raw is None:
        return {}
    if isinstance(raw, dict):
        out: Dict[str, str] = {}
        for lang, text in raw.items():
            code = str(lang).strip().upper()
            if code in ("EN", "UK", "RU") and str(text).strip():
                out[code] = str(text).strip()
        return out
    text = str(raw).strip()
    return {"EN": text, "UK": text, "RU": text} if text else {}


def _load_json_file(path: str) -> Optional[PromptSpec]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(obj, dict):
        return None
    try:
        num = int(obj.get("num"))
    except (TypeError, ValueError):
        return None
    if num <= 0:
        return None
    name = str(obj.get("name") or "").strip()
    body = str(obj.get("body") or "").strip()
    if not body:
        return None
    return PromptSpec(
        num=num,
        name=name,
        body=body,
        hint=_hint_from_obj(obj),
        path=os.path.abspath(path),
    )


def _parse_markdown_library(path: str) -> List[PromptSpec]:
    """Legacy redactor1.md — used only if ``promts/`` has no JSON yet."""
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return []
    matches = list(_PROMPT_HEADER_RE.finditer(content))
    if not matches:
        return []
    prompts: List[PromptSpec] = []
    for i, m in enumerate(matches):
        num = int(m.group(1))
        name = (m.group(2) if m.group(2) is not None else m.group(3) or "") or ""
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        text = content[start:end].strip()
        if text:
            prompts.append(PromptSpec(num=num, name=name.strip(), body=text, path=path))
    prompts.sort(key=lambda p: p.num)
    return prompts


def load_prompt_specs(root: Optional[str] = None) -> List[PromptSpec]:
    """Load prompts from ``promts/*.json``. ``hint`` / ``description`` stay UI-only."""
    directory = prompts_dir(root)
    specs: List[PromptSpec] = []
    if os.path.isdir(directory):
        try:
            names = os.listdir(directory)
        except OSError:
            names = []
        for name in names:
            if not name.lower().endswith(".json"):
                continue
            spec = _load_json_file(os.path.join(directory, name))
            if spec is not None:
                specs.append(spec)
    if specs:
        by_num: Dict[int, PromptSpec] = {}
        for spec in specs:
            prev = by_num.get(spec.num)
            if prev is None or os.path.basename(spec.path) < os.path.basename(prev.path):
                by_num[spec.num] = spec
        return sorted(by_num.values(), key=lambda p: p.num)

    legacy = os.path.join(root or BASE_DIR, "redactor1.md")
    return _parse_markdown_library(legacy)


def load_prompt_tuples(root: Optional[str] = None) -> List[PromptTuple]:
    """``(num, name, body)`` — ``hint`` is dropped, same contract as the old parser."""
    return [p.as_tuple() for p in load_prompt_specs(root)]


def prompt_spec_by_num(num: int, root: Optional[str] = None) -> Optional[PromptSpec]:
    for spec in load_prompt_specs(root):
        if spec.num == num:
            return spec
    return None


def open_prompt_file(path: str, log_func: Optional[LogFunc] = None) -> str:
    """Open one prompt JSON (or a leftover markdown file) in the system editor."""
    try:
        open_file(path)
        if log_func:
            try:
                from whisperfast.i18n import t

                log_func(t("redactor_opened", name=os.path.basename(path)))
            except ImportError:
                pass
    except OSError as e:
        if log_func:
            try:
                from whisperfast.i18n import t

                log_func(t("redactor_open_error", error=str(e)))
            except ImportError:
                pass
    return path
