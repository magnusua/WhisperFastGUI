"""AI prompt library: one JSON file per prompt in BASE_DIR/promts/.

Only ``num``, ``name`` and ``body`` are sent to the model. Fields such as
``hint`` / ``description`` are UI-only and must never be concatenated into
the request.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple

from whisperfast.config import BASE_DIR
from whisperfast.open_path import open_file, open_file_and_wait

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


_EDIT_LOCK = threading.Lock()
_EDITING_JSON: Set[str] = set()


def _log(log_func: Optional[LogFunc], key: str, **kwargs) -> None:
    if not log_func:
        return
    try:
        from whisperfast.i18n import t

        log_func(t(key, **kwargs))
    except ImportError:
        pass


def _safe_prompt_stem(name: str) -> str:
    text = re.sub(r'[<>:"/\\|?*]+', "", str(name or "")).strip().replace(" ", "_")
    text = text.strip("._")
    return (text[:40] or "prompt")


def _unlink_quiet(path: str) -> None:
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass


def prompt_temp_markdown_path(json_path: str, name: str = "", num: int = 0) -> str:
    """Stable temp Markdown path for one prompt JSON (not inside ``promts/``)."""
    stem = _safe_prompt_stem(name) if name else _safe_prompt_stem(
        os.path.splitext(os.path.basename(json_path))[0]
    )
    prefix = f"{int(num):02d}-" if int(num or 0) > 0 else ""
    return os.path.join(tempfile.gettempdir(), f"ftw-prompt-{prefix}{stem}.md")


def export_prompt_body_to_markdown(json_path: str, md_path: Optional[str] = None) -> str:
    """Copy the JSON ``body`` into a UTF-8 Markdown file. Returns the MD path."""
    json_path = os.path.abspath(json_path)
    with open(json_path, "r", encoding="utf-8") as f:
        obj = json.load(f)
    if not isinstance(obj, dict):
        raise ValueError("prompt JSON must be an object")
    body = str(obj.get("body") or "")
    try:
        num = int(obj.get("num") or 0)
    except (TypeError, ValueError):
        num = 0
    name = str(obj.get("name") or "").strip()
    target = md_path or prompt_temp_markdown_path(json_path, name=name, num=num)
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as f:
        f.write(body.replace("\r\n", "\n").replace("\r", "\n"))
        if body and not body.endswith("\n"):
            f.write("\n")
    return target


def import_prompt_body_from_markdown(md_path: str, json_path: str) -> bool:
    """Write Markdown contents back into JSON ``body``. Other fields stay intact.

    Returns False if the Markdown is missing or empty (JSON is left unchanged).
    """
    json_path = os.path.abspath(json_path)
    try:
        with open(md_path, "r", encoding="utf-8") as f:
            body = f.read().replace("\r\n", "\n").replace("\r", "\n").rstrip()
    except OSError:
        return False
    if not body:
        return False
    with open(json_path, "r", encoding="utf-8") as f:
        obj = json.load(f)
    if not isinstance(obj, dict):
        raise ValueError("prompt JSON must be an object")
    obj["body"] = body
    directory = os.path.dirname(json_path) or "."
    fd, tmp_path = tempfile.mkstemp(prefix="ftw-prompt-", suffix=".json.tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp_path, json_path)
    except Exception:
        _unlink_quiet(tmp_path)
        raise
    return True


def edit_prompt_as_markdown(
    json_path: str,
    log_func: Optional[LogFunc] = None,
    *,
    background: bool = True,
    wait_open: Optional[Callable[[str], bool]] = None,
) -> str:
    """Copy prompt text to a temp MD file, wait for the editor to close, save JSON, delete MD."""
    json_path = os.path.abspath(json_path)

    def _run() -> None:
        md_path = ""
        key = json_path
        with _EDIT_LOCK:
            if key in _EDITING_JSON:
                _log(log_func, "redactor_prompt_busy", name=os.path.basename(json_path))
                return
            _EDITING_JSON.add(key)
        try:
            md_path = export_prompt_body_to_markdown(json_path)
            _log(log_func, "redactor_prompt_editing", name=os.path.basename(md_path))
            opener = wait_open or open_file_and_wait
            opened = opener(md_path)
            if opened is False:
                _log(log_func, "redactor_open_error", error=os.path.basename(md_path))
            elif import_prompt_body_from_markdown(md_path, json_path):
                _log(log_func, "redactor_prompt_saved", name=os.path.basename(json_path))
            else:
                _log(log_func, "redactor_prompt_unchanged", name=os.path.basename(json_path))
        except (OSError, ValueError, json.JSONDecodeError, UnicodeError) as e:
            _log(log_func, "redactor_open_error", error=str(e))
        finally:
            _unlink_quiet(md_path)
            with _EDIT_LOCK:
                _EDITING_JSON.discard(key)

    if background:
        threading.Thread(target=_run, daemon=True).start()
        return json_path
    _run()
    return json_path


def open_prompt_file(path: str, log_func: Optional[LogFunc] = None) -> str:
    """Edit a prompt JSON via temp Markdown, or open a leftover file/folder as-is."""
    if path and os.path.isfile(path) and path.lower().endswith(".json"):
        return edit_prompt_as_markdown(path, log_func=log_func)
    try:
        open_file(path)
        _log(log_func, "redactor_opened", name=os.path.basename(path))
    except OSError as e:
        _log(log_func, "redactor_open_error", error=str(e))
    return path
