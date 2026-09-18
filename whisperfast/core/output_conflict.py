"""Resolve output path conflicts: overwrite, timed name, or skip write."""
from __future__ import annotations

import os
from datetime import datetime
from typing import Callable, List, Optional, Sequence

AskOverwriteFn = Callable[[str, str], Optional[bool]]
# ask(path, alt_name) -> True overwrite, False use alt, None skip write


def make_timed_alt_path(path: str) -> str:
    """name.ext → name_HHMM.ext (hour+minute). If taken, use HHMMSS."""
    path = os.path.abspath(path)
    base, ext = os.path.splitext(path)
    stamp = datetime.now().strftime("%H%M")
    candidate = f"{base}_{stamp}{ext}"
    if not os.path.exists(candidate):
        return candidate
    stamp = datetime.now().strftime("%H%M%S")
    candidate = f"{base}_{stamp}{ext}"
    n = 1
    while os.path.exists(candidate):
        candidate = f"{base}_{stamp}_{n}{ext}"
        n += 1
    return candidate


def apply_time_suffix_to_paths(paths: Sequence[str], stamp: Optional[str] = None) -> List[str]:
    """Apply the same _HHMM (or given stamp) to every path."""
    stamp = stamp or datetime.now().strftime("%H%M")
    result: List[str] = []
    for path in paths:
        if not path:
            result.append(path)
            continue
        path = os.path.abspath(path)
        base, ext = os.path.splitext(path)
        candidate = f"{base}_{stamp}{ext}"
        if os.path.exists(candidate):
            stamp_s = datetime.now().strftime("%H%M%S")
            candidate = f"{base}_{stamp_s}{ext}"
            n = 1
            while os.path.exists(candidate):
                candidate = f"{base}_{stamp_s}_{n}{ext}"
                n += 1
        result.append(candidate)
    return result


def resolve_output_paths(
    paths: Sequence[str],
    ask_overwrite: AskOverwriteFn,
) -> List[str]:
    """
    If any path already exists, ask once (first existing file).
    Yes → keep paths (overwrite). No → append _HHMM to all paths in the group.
    Skip → empty strings (caller must not write).
    """
    normalized = [os.path.abspath(p) if p else p for p in paths]
    existing = [p for p in normalized if p and os.path.isfile(p)]
    if not existing:
        return normalized

    sample = existing[0]
    alt = make_timed_alt_path(sample)
    overwrite = ask_overwrite(sample, os.path.basename(alt))
    if overwrite is None:
        return [""] * len(normalized)
    if overwrite:
        return normalized

    # Same clock stamp for the whole group (txt+srt+mp3)
    stamp = datetime.now().strftime("%H%M")
    # Prefer alt's stamp if it matches pattern
    base_sample, _ = os.path.splitext(sample)
    alt_base, _ = os.path.splitext(alt)
    if alt_base.startswith(base_sample + "_"):
        stamp = alt_base[len(base_sample) + 1 :]
    return apply_time_suffix_to_paths(normalized, stamp=stamp)


def resolve_single_output_path(path: str, ask_overwrite: AskOverwriteFn) -> str:
    return resolve_output_paths([path], ask_overwrite)[0]


def ai_prompts_dialog_is_open(app) -> bool:
    """True if a «Промты» window is open (do not stack another modal over it)."""
    ai_jobs = getattr(app, "ai_jobs", None)
    if ai_jobs is None:
        return False
    if hasattr(ai_jobs, "has_open_prompt_dialog"):
        try:
            return bool(ai_jobs.has_open_prompt_dialog())
        except Exception:
            pass
    return bool(getattr(ai_jobs, "_prompt_dialog_job_id", None))


# ask_overwrite_via_tk() (builds a Tk dialog) lives in whisperfast/ui/dialogs.py —
# core/ stays free of Tkinter. See docs/INTERNAL-ARCHITECTURE.uk.md.
