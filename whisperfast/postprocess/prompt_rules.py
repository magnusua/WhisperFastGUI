"""Match which AI prompts auto-run after a file is transcribed."""
from __future__ import annotations

import fnmatch
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

MATCH_ALWAYS = "always"
MATCH_NEVER = "never"
MATCH_WATCH_DIR = "watch_dir"
MATCH_FILENAME = "filename"
VALID_MATCH = (MATCH_ALWAYS, MATCH_NEVER, MATCH_WATCH_DIR, MATCH_FILENAME)


def normalize_prompt_rules(value: Any) -> List[Dict[str, Any]]:
    """Sanitize a list of rule dicts from settings.json."""
    if not isinstance(value, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        match = str(item.get("match") or MATCH_ALWAYS).strip().lower()
        if match not in VALID_MATCH:
            match = MATCH_ALWAYS
        nums: List[int] = []
        seen = set()
        raw_nums = item.get("prompt_nums")
        if isinstance(raw_nums, list):
            for n in raw_nums:
                try:
                    i = int(n)
                except (TypeError, ValueError):
                    continue
                if i > 0 and i not in seen:
                    seen.add(i)
                    nums.append(i)
        out.append(
            {
                "match": match,
                "pattern": str(item.get("pattern") or ""),
                "prompt_nums": nums,
                "skip_dialog": bool(item.get("skip_dialog", True)),
            }
        )
    return out


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path))) if path else ""


def rule_matches(rule: Dict[str, Any], source_path: str, watch_dirs: Optional[Sequence[str]] = None) -> bool:
    kind = (rule.get("match") or MATCH_ALWAYS).lower()
    if kind == MATCH_NEVER:
        return False
    if kind == MATCH_ALWAYS:
        return True
    src = source_path or ""
    if kind == MATCH_FILENAME:
        pattern = (rule.get("pattern") or "").strip() or "*"
        return fnmatch.fnmatch(os.path.basename(src), pattern)
    if kind == MATCH_WATCH_DIR:
        pattern = (rule.get("pattern") or "").strip()
        candidates = []
        if pattern:
            candidates.append(pattern)
        if watch_dirs:
            candidates.extend(watch_dirs)
        src_n = _norm(src)
        for d in candidates:
            if not d:
                continue
            dn = _norm(d)
            if src_n == dn or src_n.startswith(dn + os.sep) or src_n.startswith(dn + os.path.sep):
                return True
            # also accept a substring match on the raw pattern (watch folder name)
            if pattern and pattern.lower() in src.replace("\\", "/").lower():
                return True
        return False
    return False


def first_matching_rule(
    rules: Sequence[Dict[str, Any]],
    source_path: str,
    watch_dirs: Optional[Sequence[str]] = None,
) -> Optional[Dict[str, Any]]:
    for rule in rules or []:
        if rule_matches(rule, source_path, watch_dirs=watch_dirs):
            return dict(rule)
    return None


def select_prompts(
    prompts: Sequence[Tuple[int, str, str]],
    nums: Sequence[int],
) -> List[Tuple[int, str, str]]:
    want = set(int(n) for n in nums)
    return [p for p in prompts if p[0] in want]
