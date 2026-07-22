"""Transcription pipeline (queue processing and file export)."""
import os
import tempfile
import time
import traceback

from pydub import AudioSegment
from tkinter import messagebox

from whisperfast.config import (
    AUDIO_EXTENSIONS,
    DEFAULT_MODEL,
    FULL_VIDEO_SEGMENT_EPS_S,
    LANG_AUTO_VALUE,
    LOG_UPDATE_INTERVAL_S,
    PROGRESS_UPDATE_INTERVAL_S,
)
from whisperfast.core.document_convert import (
    ensure_markdown_for_cursor,
    is_document_file,
    needs_office_to_md,
)
from whisperfast.core.model_manager import WhisperModelSingleton
from whisperfast.i18n import t
from whisperfast.utils import (
    format_timestamp,
    format_timestamp_filename,
    format_timestamp_srt,
    get_audio_duration_seconds,
    normalize_queue_path,
    parse_timestamp_to_seconds,
)

class SegmentOffset:
    """Сегмент с полями start, end, text (для смещения времени при обработке куска файла)."""
    __slots__ = ("start", "end", "text")
    def __init__(self, start, end, text):
        self.start = start
        self.end = end
        self.text = text


def segment_file_suffix(start_sec, end_sec):
    """Suffix for segment filenames: HH-MM-SS_HH-MM-SS."""
    return "_" + format_timestamp_filename(start_sec) + "_" + format_timestamp_filename(end_sec)


def _process_document_item(app, path, opts):
    """Документ/текст: при необходимости PDF/DOC/DOCX → MD, затем опционально Cursor / DOCX."""
    out_dir = app._resolve_output_dir(path, opts)
    send_to_cursor = bool(opts.get("send_txt_to_cursor"))
    export_md_to_docx = bool(opts.get("export_md_to_docx"))
    cursor_api_key = opts.get("cursor_api_key") or ""
    src_name = os.path.basename(path)
    ext = os.path.splitext(path)[1].lower()

    app.log(t("doc_processing_start", name=src_name, ext=ext or "?"))

    if needs_office_to_md(path):
        app.log(t("doc_converting", name=src_name))

    md_path, was_converted = ensure_markdown_for_cursor(path, out_dir)
    app.queue_ctrl.register_output_paths([md_path])
    md_name = os.path.basename(md_path)

    chars = 0
    lines = 0
    try:
        with open(md_path, "r", encoding="utf-8") as f:
            text = f.read()
        chars = len(text)
        lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
    except OSError:
        pass

    app.log(t("doc_files_created", name=os.path.splitext(src_name)[0]))
    if was_converted or needs_office_to_md(path):
        app.log(t("doc_converted", src=src_name, md=md_name))
    else:
        app.log(t("doc_ready_md", name=md_name))
    app.log(t("doc_md_file"), None)
    app.log(md_path, "link")
    if chars or lines:
        app.log(t("doc_md_stats", chars=chars, lines=lines))

    # Исходный не-MD в Cursor не передаём — только Markdown.
    if was_converted or needs_office_to_md(path):
        app.log(t("doc_not_sent_to_cursor", name=src_name))

    if send_to_cursor:
        app.log(t("doc_md_sent_to_cursor", name=md_name))
        # DOCX после Cursor: экспорт каждого созданного *.md
        app._schedule_cursor_postprocess(
            md_path,
            cursor_api_key=cursor_api_key,
            export_md_to_docx=export_md_to_docx,
        )
    else:
        if not (was_converted or needs_office_to_md(path)):
            app.log(t("doc_not_sent_to_cursor", name=src_name))
        if export_md_to_docx:
            app._export_markdown_to_docx(md_path)
        app.log(t("doc_processing_done", name=src_name))

    app.root.after(0, lambda: app._set_progress_value(100))
    app.root.after(0, lambda p=path: app._mark_done_by_path(p))


def run_queue(app, mode, target_idx, options=None):
    opts = options or {}
    try:
        model = None

        def get_model():
            nonlocal model
            if model is None:
                model = WhisperModelSingleton.get(
                    app.log,
                    opts.get("device_mode", "AUTO"),
                    opts.get("whisper_model", DEFAULT_MODEL),
                )
            return model

        # Снимок очереди, чтобы индексы не выходили за границы при изменении очереди в GUI
        queue_snapshot = list(app.queue)
        if mode == "single":
            indices = [target_idx]
        elif mode == "only_new":
            indices = [i for i in range(len(queue_snapshot)) if not queue_snapshot[i].get("processed")]
        else:
            indices = list(range(len(queue_snapshot)))

        done = 0
        to_do = len(indices)
        skipped_paths = []

        for idx in indices:
            if app.cancel_requested:
                break
            if idx < 0 or idx >= len(queue_snapshot):
                continue
            row = queue_snapshot[idx]
            path = normalize_queue_path(row.get("path"))
            if not path:
                continue
            name = os.path.basename(path)
            if not os.path.isfile(path):
                app.log(f"\n{t('processing', current=done + 1, total=to_do, name=name)}")
                app.log(t("file_skipped", name=name))
                skipped_paths.append(path)
                continue
            app.log(f"\n{t('processing', current=done + 1, total=to_do, name=name)}")

            try:
                if is_document_file(path):
                    _process_document_item(app, path, opts)
                    done += 1
                    continue

                start_sec = parse_timestamp_to_seconds(row.get("start")) or 0.0
                duration = get_audio_duration_seconds(path) or 1.0
                end_sec = parse_timestamp_to_seconds(row.get("end")) or duration
                end_sec = min(end_sec, duration)
                segment_duration = end_sec - start_sec if end_sec > start_sec else duration

                audio = None
                if opts.get("save_audio_mp3"):
                    ext = os.path.splitext(path)[1].lower()
                    is_audio_source = ext in AUDIO_EXTENSIONS
                    if is_audio_source:
                        choice = [None]
                        def ask_save_mp3():
                            choice[0] = messagebox.askyesno(
                                t("save_audio_mp3"),
                                t("save_mp3_confirm", filename=os.path.basename(path))
                            )
                        app.root.after(0, ask_save_mp3)
                        while choice[0] is None and not app.cancel_requested:
                            time.sleep(0.05)
                        if choice[0]:
                            full = AudioSegment.from_file(path)
                            audio = full[int(start_sec * 1000):int(end_sec * 1000)]
                    else:
                        full = AudioSegment.from_file(path)
                        audio = full[int(start_sec * 1000):int(end_sec * 1000)]
                else:
                    full = None

                lang_val = opts.get("lang_mode", LANG_AUTO_VALUE)
                lang_param = None if lang_val == LANG_AUTO_VALUE else lang_val
                model = get_model()

                if start_sec > 0 or end_sec < duration:
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                        tmp_path = tmp.name
                    try:
                        seg_audio = AudioSegment.from_file(path)[int(start_sec * 1000):int(end_sec * 1000)]
                        seg_audio.export(tmp_path, format="wav")
                        segments_iter, _ = model.transcribe(tmp_path, language=lang_param, vad_filter=True)
                    finally:
                        try:
                            os.unlink(tmp_path)
                        except OSError:
                            pass
                else:
                    segments_iter, _ = model.transcribe(path, language=lang_param, vad_filter=True)

                res = []
                last_progress_update = [0.0]
                last_log_update = [0.0]
                segment_count = [0]
                for s in segments_iter:
                    if app.cancel_requested:
                        break
                    res.append(s)
                    segment_count[0] += 1
                    now = time.time()
                    if now - last_progress_update[0] >= PROGRESS_UPDATE_INTERVAL_S:
                        val = min(100, (s.end / segment_duration) * 100) if (segment_duration and segment_duration > 0) else 100
                        app.root.after(0, lambda v=val: app._set_progress_value(v))
                        last_progress_update[0] = now
                    if now - last_log_update[0] >= LOG_UPDATE_INTERVAL_S or segment_count[0] <= 2:
                        seg_text = (s.text or "").strip()
                        app.log(f"   [{format_timestamp(s.start)}] {seg_text}")
                        last_log_update[0] = now

                if not app.cancel_requested:
                    app.root.after(0, lambda: app._set_progress_value(100))
                    if start_sec > 0 or end_sec < duration:
                        res = [SegmentOffset(s.start + start_sec, s.end + start_sec, s.text or "") for s in res]
                    is_segment = start_sec >= FULL_VIDEO_SEGMENT_EPS_S or (duration - end_sec) >= FULL_VIDEO_SEGMENT_EPS_S
                    # Через app.save_files — единая точка (GUI-обёртка → этот модуль)
                    app.save_files(
                        path,
                        res,
                        audio_segment=audio,
                        segment_start_sec=start_sec if is_segment else None,
                        segment_end_sec=end_sec if is_segment else None,
                        output_opts=opts,
                        send_txt_to_cursor=bool(opts.get("send_txt_to_cursor")),
                        cursor_api_key=opts.get("cursor_api_key") or "",
                    )
                    app.root.after(0, lambda p=path: app._mark_done_by_path(p))
                    done += 1
            except Exception as e:
                app.log(t("file_skipped", name=name))
                app.log(t("error_occurred", error=str(e)))
                if app.queue_ctrl.notify_decode_failed(path):
                    app.root.after(0, lambda p=path: app.queue_ctrl.remove_paths([p]))
                else:
                    skipped_paths.append(path)

        if skipped_paths:
            paths_copy = list(skipped_paths)
            app.root.after(0, lambda: app._report_skipped_and_offer_remove(paths_copy))
        if app.cancel_requested:
            app.log(f"\n{t('cancelled', count=to_do - done)}")
        else:
            app.log(f"\n{t('all_tasks_complete')}")
            will_continue = (
                app.queue_ctrl.watch_pending_continue
                and any(not q.get("processed") for q in app.queue)
            )
            app._maybe_play_finish_sound(
                play_requested=bool(opts.get("play_sound_on_finish")),
                send_txt_to_cursor=bool(opts.get("send_txt_to_cursor")),
                will_continue=will_continue,
            )

    except Exception as e:
        err_msg = str(e)
        app.log(t("error_occurred", error=err_msg))
        if isinstance(e, IndexError) or "list index out of range" in err_msg.lower():
            app.log(t("error_no_audio_hint"))
        if os.environ.get("DEBUG"):
            app.log(traceback.format_exc())
    finally:
        app.root.after(0, app.reset_ui)


def save_files(app, path, segments, audio_segment=None, segment_start_sec=None, segment_end_sec=None, output_opts=None, send_txt_to_cursor=False, cursor_api_key=""):
    opts = output_opts or {}
    out = app._resolve_output_dir(path, opts)
    marker = app._processed_marker()
    base = os.path.splitext(os.path.basename(path))[0].replace(marker, "")
    if segment_start_sec is not None and segment_end_sec is not None:
        base = base + segment_file_suffix(segment_start_sec, segment_end_sec)
    txt_p = os.path.abspath(os.path.join(out, base + ".txt"))
    srt_p = os.path.abspath(os.path.join(out, base + ".srt"))
    out_paths = [txt_p, srt_p]
    mp3_out = None
    if audio_segment is not None:
        mp3_out = app._resolve_mp3_output_dir(path, opts)
        out_paths.append(os.path.abspath(os.path.join(mp3_out, base + "_audio.mp3")))
    app.queue_ctrl.register_output_paths(out_paths)

    with open(txt_p, "w", encoding="utf-8") as f:
        f.write("\n".join([(s.text or "").strip() for s in segments]))

    with open(srt_p, "w", encoding="utf-8") as f:
        for i, s in enumerate(segments, 1):
            timestamp = f"{format_timestamp_srt(s.start)} --> {format_timestamp_srt(s.end)}"
            f.write(f"{i}\n{timestamp}\n{(s.text or '').strip()}\n\n")

    app.log(t("files_created", name=base))
    app.log(t("txt_file"), None)
    app.log(txt_p, "link")
    app.log(t("srt_file"), None)
    app.log(srt_p, "link")

    if audio_segment is not None and mp3_out is not None:
        mp3_p = os.path.abspath(os.path.join(mp3_out, base + "_audio.mp3"))
        try:
            audio_segment.export(mp3_p, format="mp3")
            app.log(t("audio_mp3_file"), None)
            app.log(mp3_p, "link")
        except Exception as e:
            app.log(t("audio_mp3_error", error=str(e)))

    if send_txt_to_cursor:
        app._schedule_cursor_postprocess(
            txt_p,
            cursor_api_key=cursor_api_key,
            export_md_to_docx=bool(opts.get("export_md_to_docx")),
        )

