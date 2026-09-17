"""Drop exact far-end duplicates of near-end phrases (speaker echo, not DSP)."""
from __future__ import annotations

import re
from typing import Any, List

from whisperfast.core.speakers import SPEAKER_FAR, SPEAKER_NEAR


def _norm(text: str) -> str:
    s = (text or "").strip().casefold()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^\w\s]+", "", s, flags=re.UNICODE)
    return s


def filter_echo_segments(
    segments: List[Any],
    near_id: str = SPEAKER_NEAR,
    far_id: str = SPEAKER_FAR,
    near_names: tuple = (),
    far_names: tuple = (),
) -> List[Any]:
    near_aliases = {near_id, *(near_names or ())}
    far_aliases = {far_id, *(far_names or ())}
    seen_near = set()
    out: List[Any] = []
    for seg in segments or []:
        spk = str(getattr(seg, "speaker", "") or "")
        text = _norm(getattr(seg, "text", "") or "")
        if spk in near_aliases:
            if text:
                seen_near.add(text)
            out.append(seg)
            continue
        if spk in far_aliases and text and text in seen_near:
            continue
        out.append(seg)
    return out
