"""Archive dialog to rename stereo speakers (You / Them)."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk

from whisperfast.core.speakers import (
    DEFAULT_THEM,
    DEFAULT_YOU,
    MANUAL,
    SPEAKER_FAR,
    SPEAKER_NEAR,
    first_and_longest_utterances,
    load_speakers,
    save_speakers,
    speakers_json_path,
    utterances_for_id,
)
from whisperfast.i18n import t
from whisperfast.ui.dialogs import center_toplevel


class _Seg:
    def __init__(self, start, end, text, speaker=""):
        self.start = start
        self.end = end
        self.text = text
        self.speaker = speaker


def _segments_from_job(job: dict):
    segs = []
    json_path = job.get("json_path") or ""
    if json_path and os.path.isfile(json_path):
        import json

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for row in data.get("segments") or []:
                segs.append(
                    _Seg(
                        float(row.get("start") or 0),
                        float(row.get("end") or 0),
                        str(row.get("text") or ""),
                        str(row.get("speaker") or ""),
                    )
                )
            return segs
        except Exception:
            segs = []
    srt = job.get("srt_path") or ""
    if srt and os.path.isfile(srt):
        from whisperfast.srt_parse import parse_srt_file

        try:
            for c in parse_srt_file(srt):
                text = str(c.get("text") or "")
                speaker = ""
                if ":" in text:
                    maybe, rest = text.split(":", 1)
                    if len(maybe) < 40:
                        speaker, text = maybe.strip(), rest.strip()
                segs.append(_Seg(float(c.get("start") or 0), float(c.get("end") or 0), text, speaker))
        except OSError:
            pass
    return segs


def show_speaker_rename_dialog(app, job: dict):
    dialog = tk.Toplevel(app.root)
    dialog.title(t("archive_rename_speakers"))
    dialog.transient(app.root)
    dialog.minsize(480, 360)
    dialog.geometry("560x420")
    frame = ttk.Frame(dialog, padding=10)
    frame.pack(fill="both", expand=True)

    txt = job.get("txt_path") or job.get("json_path") or ""
    sp_path = ""
    extra = job.get("extra_outputs") or []
    if isinstance(extra, list):
        for item in extra:
            if isinstance(item, dict) and str(item.get("role") or "") == "speakers":
                sp_path = item.get("path") or ""
    if not sp_path and txt:
        sp_path = speakers_json_path(txt if txt.endswith(".txt") else os.path.splitext(txt)[0] + ".txt")
        # also try next to json
        if not os.path.isfile(sp_path):
            base = os.path.splitext(txt)[0]
            sp_path = base + ".speakers.json"

    cfg = getattr(app, "capture_cfg", None) or {}
    speakers = load_speakers(sp_path) if sp_path else {}
    if SPEAKER_NEAR not in speakers:
        speakers[SPEAKER_NEAR] = {
            "id": SPEAKER_NEAR,
            "display": cfg.get("user_name") or DEFAULT_YOU,
            "source": "default",
            "quote": "",
        }
    if SPEAKER_FAR not in speakers:
        speakers[SPEAKER_FAR] = {
            "id": SPEAKER_FAR,
            "display": cfg.get("them_name") or DEFAULT_THEM,
            "source": "default",
            "quote": "",
        }

    segs = _segments_from_job(job)
    vars_name = {}
    for sid in (SPEAKER_NEAR, SPEAKER_FAR):
        row = speakers.get(sid) or {}
        display = str(row.get("display") or sid)
        first, longest = utterances_for_id(segs, sid, display)
        if not first:
            first, longest = first_and_longest_utterances(segs, display)
        box = ttk.LabelFrame(frame, text=sid, padding=6)
        box.pack(fill="x", pady=6)
        ttk.Label(box, text=t("archive_speaker_first"), wraplength=500).pack(anchor="w")
        ttk.Label(box, text=first or "—", wraplength=500).pack(anchor="w")
        ttk.Label(box, text=t("archive_speaker_longest"), wraplength=500).pack(anchor="w", pady=(4, 0))
        ttk.Label(box, text=longest or "—", wraplength=500).pack(anchor="w")
        name_var = tk.StringVar(value=display)
        vars_name[sid] = name_var
        ttk.Entry(box, textvariable=name_var).pack(fill="x", pady=(6, 0))

    def save():
        mapping = {}
        for sid, var in vars_name.items():
            name = (var.get() or "").strip() or sid
            mapping[speakers.get(sid, {}).get("display") or sid] = name
            speakers[sid] = dict(speakers.get(sid) or {})
            speakers[sid]["id"] = sid
            speakers[sid]["display"] = name
            speakers[sid]["source"] = MANUAL
        if sp_path:
            save_speakers(sp_path, speakers)
        # rewrite txt/srt labels
        from whisperfast.core.speakers import rewrite_text_speakers

        for key in ("txt_path", "srt_path", "vtt_path"):
            path = job.get(key) or ""
            if path and os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        body = f.read()
                    body = rewrite_text_speakers(body, mapping)
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(body)
                except OSError:
                    pass
        json_path = job.get("json_path") or ""
        if json_path and os.path.isfile(json_path):
            import json

            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for row in data.get("segments") or []:
                    spk = str(row.get("speaker") or "")
                    if spk in mapping:
                        row["speaker"] = mapping[spk]
                    for sid, var in vars_name.items():
                        if spk == sid:
                            row["speaker"] = var.get().strip()
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                    f.write("\n")
            except Exception:
                pass
        dialog.destroy()

    ttk.Button(frame, text=t("ok"), command=save).pack(anchor="e", pady=(8, 0))
    center_toplevel(app, dialog)
    return dialog
