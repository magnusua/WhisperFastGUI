"""Speaker labels for segments.

Built-in path: stereo energy (left = SPEAKER_00, right = SPEAKER_01) — matches
meeting capture (mic | system). Optional pyannote if the package is installed
and diarization is enabled; otherwise mono files keep no speaker prefix.
"""
from __future__ import annotations

from typing import Any, Iterable, List, Optional


def apply_speakers(
    path: str,
    segments: List[Any],
    enabled: bool = False,
    user_name: str = "You",
    them_name: str = "Them",
) -> List[Any]:
    """Attach `.speaker` on segments when possible. Returns the same list.

    Stereo energy uses SPEAKER_00 (mic / You) and SPEAKER_01 (system / Them),
    then display names replace the raw ids in the segment text field.
    """
    if not enabled or not segments or not path:
        return segments
    labeled = _label_stereo(path, segments)
    if not labeled:
        labeled = _label_pyannote(path, segments)
    if not labeled:
        return segments
    from whisperfast.core.speakers import apply_display_names, default_speakers_map

    apply_display_names(segments, default_speakers_map(user_name, them_name))
    return segments


def _set_speaker(seg: Any, speaker: str) -> None:
    try:
        seg.speaker = speaker
    except Exception:
        pass


def _label_stereo(path: str, segments: List[Any]) -> Optional[List[Any]]:
    try:
        from pydub import AudioSegment
    except ImportError:
        return None
    try:
        audio = AudioSegment.from_file(path)
    except Exception:
        return None
    if audio.channels < 2:
        return None
    left, right = audio.split_to_mono()[:2]
    for s in segments:
        start_ms = int(max(0.0, float(getattr(s, "start", 0) or 0)) * 1000)
        end_ms = int(max(start_ms + 1, float(getattr(s, "end", 0) or 0) * 1000))
        l = left[start_ms:end_ms].rms if end_ms > start_ms else 0
        r = right[start_ms:end_ms].rms if end_ms > start_ms else 0
        _set_speaker(s, "SPEAKER_00" if l >= r else "SPEAKER_01")
    return segments


def _label_pyannote(path: str, segments: List[Any]) -> Optional[List[Any]]:
    try:
        from pyannote.audio import Pipeline  # type: ignore
    except ImportError:
        return None
    try:
        pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1")
        diarization = pipeline(path)
    except Exception:
        return None
    turns = []
    try:
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            turns.append((float(turn.start), float(turn.end), str(speaker)))
    except Exception:
        return None
    if not turns:
        return None
    for s in segments:
        mid = (float(getattr(s, "start", 0) or 0) + float(getattr(s, "end", 0) or 0)) / 2.0
        best = None
        for a, b, spk in turns:
            if a <= mid <= b:
                best = spk
                break
        if best:
            _set_speaker(s, best)
    return segments
