"""Transcription pipeline (queue processing and file export).

Цей модуль не імпортує tkinter напряму: підтвердження «зберегти MP3?»
запитується через app.ask_save_mp3_confirm(filename) — метод, який реалізує
GUI (whisperfast/ui/gui.py: WhisperGUI.ask_save_mp3_confirm), той самий
duck-typed підхід, що вже застосований для app.resolve_output_path(s)/
ask_overwrite у core/output_conflict.py.

Контракт `app` зафіксовано в `whisperfast.core.host.TranscriptionHost`
(структурний Protocol; WhisperGUI не наслідує його явно). Тести черги
використовують легкий фейк замість Tkinter.
"""
from __future__ import annotations

import os
import tempfile
import time
import traceback

from pydub import AudioSegment

from whisperfast.core.host import TranscriptionHost
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


def _process_document_item(app: TranscriptionHost, path, opts, file_id=None):
    """Документ/текст: при необходимости PDF/DOC/DOCX → MD, затем опционально Cursor / DOCX."""
    out_dir = app._resolve_output_dir(path, opts)
    send_to_cursor = bool(opts.get("send_txt_to_ai", opts.get("send_txt_to_cursor")))
    export_md_to_docx = bool(opts.get("export_md_to_docx"))
    cursor_api_key = opts.get("cursor_api_key") or ""
    src_name = os.path.basename(path)
    ext = os.path.splitext(path)[1].lower()

    app.log_file_event(
        t("doc_processing_start", name=src_name, ext=ext or "?"),
        file_id=file_id,
    )

    if needs_office_to_md(path):
        app.log_file_event(t("doc_converting", name=src_name), file_id=file_id)

    intended_md = os.path.abspath(os.path.join(out_dir, os.path.splitext(src_name)[0] + ".md"))
    if hasattr(app, "resolve_output_path"):
        # Не питати, якщо цільовий MD — той самий, що й джерело (режим beside)
        if os.path.normcase(intended_md) != os.path.normcase(os.path.abspath(path)):
            intended_md = app.resolve_output_path(intended_md)
            if not intended_md:
                app.log_file_event(
                    t("file_exists_skipped", name=os.path.splitext(src_name)[0] + ".md"),
                    file_id=file_id,
                )
                if file_id:
                    app.end_file_log("skipped", file_id=file_id)
                return

    md_path, was_converted = ensure_markdown_for_cursor(path, out_dir, target_md_path=intended_md)
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

    ai_job_id = None
    if send_to_cursor and hasattr(app, "_register_ai_job"):
        ai_job_id = app._register_ai_job(
            md_path,
            export_md_to_docx=export_md_to_docx,
            log_file_id=file_id,
        )
        app.set_file_prompt_callback(
            file_id,
            lambda jid=ai_job_id: app.ai_jobs.open_prompt_dialog(jid),
        )
    if was_converted or needs_office_to_md(path):
        app.log_file_event(
            t("doc_converted", src=src_name, md=md_name),
            file_id=file_id,
        )
    else:
        app.log_file_event(t("doc_ready_md", name=md_name), file_id=file_id)
    app.add_file_output("md", md_path, file_id=file_id)
    if chars or lines:
        app.log_file_event(
            t("doc_md_stats", chars=chars, lines=lines),
            file_id=file_id,
        )

    # Исходный не-MD в Cursor не передаём — только Markdown.
    if was_converted or needs_office_to_md(path):
        app.log_file_event(
            t("doc_not_sent_to_cursor", name=src_name),
            file_id=file_id,
        )

    if send_to_cursor:
        # DOCX после Cursor: экспорт каждого созданного *.md
        app._schedule_cursor_postprocess(
            md_path,
            cursor_api_key=cursor_api_key,
            export_md_to_docx=export_md_to_docx,
            job_id=ai_job_id,
            log_file_id=file_id,
        )
    else:
        if not (was_converted or needs_office_to_md(path)):
            app.log_file_event(
                t("doc_not_sent_to_cursor", name=src_name),
                file_id=file_id,
            )
        if export_md_to_docx:
            app._export_markdown_to_docx(md_path, log_file_id=file_id)
        app.log_file_event(t("doc_processing_done", name=src_name), file_id=file_id)

    from whisperfast.core.source_relocate import finalize_source_after_processing

    # Final out dir may differ from planned if MD was renamed on conflict
    dest_dir = os.path.dirname(os.path.abspath(md_path))
    finalize_source_after_processing(app, path, dest_dir, file_id=file_id)

    app.root.after(0, lambda: app._set_progress_value(100))
    app.end_file_log("done", file_id=file_id)


def run_queue(app: TranscriptionHost, mode, target_idx, options=None):
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
                file_id = app.begin_file_log(path, name=name, current=done + 1, total=to_do)
                app.log_file_event(t("file_skipped", name=name), file_id=file_id)
                app.end_file_log("skipped", file_id=file_id)
                skipped_paths.append(path)
                continue
            file_id = app.begin_file_log(path, name=name, current=done + 1, total=to_do)

            try:
                if is_document_file(path):
                    _process_document_item(app, path, opts, file_id=file_id)
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
                        if app.ask_save_mp3_confirm(os.path.basename(path)):
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
                        app.log_file_segment(
                            format_timestamp(s.start),
                            seg_text,
                            count=segment_count[0],
                            file_id=file_id,
                        )
                        last_log_update[0] = now

                if not app.cancel_requested:
                    if res:
                        last = res[-1]
                        app.log_file_segment(
                            format_timestamp(last.start),
                            (last.text or "").strip(),
                            count=segment_count[0],
                            file_id=file_id,
                        )
                    app.root.after(0, lambda: app._set_progress_value(100))
                    if start_sec > 0 or end_sec < duration:
                        res = [SegmentOffset(s.start + start_sec, s.end + start_sec, s.text or "") for s in res]
                    is_segment = start_sec >= FULL_VIDEO_SEGMENT_EPS_S or (duration - end_sec) >= FULL_VIDEO_SEGMENT_EPS_S
                    # Через app.save_files — единая точка (GUI-обёртка → этот модуль)
                    saved = app.save_files(
                        path,
                        res,
                        audio_segment=audio,
                        segment_start_sec=start_sec if is_segment else None,
                        segment_end_sec=end_sec if is_segment else None,
                        output_opts=opts,
                        send_txt_to_cursor=bool(opts.get("send_txt_to_ai", opts.get("send_txt_to_cursor"))),
                        cursor_api_key=opts.get("cursor_api_key") or "",
                        log_file_id=file_id,
                    )
                    # mark_done / path update — внутри finalize_source_after_processing
                    if saved:
                        done += 1
                else:
                    app.end_file_log("skipped", file_id=file_id)
            except Exception as e:
                app.end_file_log("failed", error=str(e), file_id=file_id)
                # Retry через watch pending — только для медиа; документы не «декодируются»
                if is_document_file(path):
                    skipped_paths.append(path)
                elif app.queue_ctrl.notify_decode_failed(path):
                    app.root.after(0, lambda p=path: app.queue_ctrl.remove_paths([p]))
                else:
                    skipped_paths.append(path)

        if skipped_paths:
            paths_copy = list(skipped_paths)
            app.root.after(0, lambda: app._report_skipped_and_offer_remove(paths_copy))
        if app.cancel_requested:
            app.log(f"\n{t('cancelled', count=to_do - done)}")
        else:
            will_continue = (
                (
                    app.queue_ctrl.watch_pending_continue
                    or bool(opts.get("_from_watch"))
                )
                and any(not q.get("processed") for q in app.queue)
            )
            send_cursor = bool(opts.get("send_txt_to_ai", opts.get("send_txt_to_cursor")))
            app._maybe_log_all_complete(
                send_txt_to_cursor=send_cursor,
                will_continue=will_continue,
            )
            app._maybe_play_finish_sound(
                play_requested=bool(opts.get("play_sound_on_finish")),
                send_txt_to_cursor=send_cursor,
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


def save_files(app: TranscriptionHost, path, segments, audio_segment=None, segment_start_sec=None, segment_end_sec=None, output_opts=None, send_txt_to_cursor=False, cursor_api_key="", log_file_id=None):
    """Save txt/srt/mp3, relocate source beside outputs. Returns final source path, or None if skipped."""
    opts = output_opts or {}
    out = app._resolve_output_dir(path, opts)
    marker = app._processed_marker()
    base = os.path.splitext(os.path.basename(path))[0].replace(marker, "")
    if segment_start_sec is not None and segment_end_sec is not None:
        base = base + segment_file_suffix(segment_start_sec, segment_end_sec)
    txt_p = os.path.abspath(os.path.join(out, base + ".txt"))
    srt_p = os.path.abspath(os.path.join(out, base + ".srt"))
    mp3_out = None
    mp3_p = None
    if audio_segment is not None:
        mp3_out = app._resolve_mp3_output_dir(path, opts)
        mp3_p = os.path.abspath(os.path.join(mp3_out, base + "_audio.mp3"))

    resolve = getattr(app, "resolve_output_paths", None)
    if resolve:
        group = [txt_p, srt_p]
        if mp3_p:
            group.append(mp3_p)
        resolved = resolve(group)
        if not resolved or not resolved[0]:
            app.log_file_event(
                t("file_exists_skipped", name=base + ".txt"),
                file_id=log_file_id,
            )
            if log_file_id:
                app.end_file_log("skipped", file_id=log_file_id)
            return None
        txt_p, srt_p = resolved[0], resolved[1]
        if mp3_p:
            mp3_p = resolved[2]

    out_paths = [txt_p, srt_p]
    if mp3_p:
        out_paths.append(mp3_p)
    app.queue_ctrl.register_output_paths(out_paths)

    with open(txt_p, "w", encoding="utf-8") as f:
        f.write("\n".join([(s.text or "").strip() for s in segments]))

    with open(srt_p, "w", encoding="utf-8") as f:
        for i, s in enumerate(segments, 1):
            timestamp = f"{format_timestamp_srt(s.start)} --> {format_timestamp_srt(s.end)}"
            f.write(f"{i}\n{timestamp}\n{(s.text or '').strip()}\n\n")

    file_id = log_file_id
    ai_job_id = None
    if send_txt_to_cursor and hasattr(app, "_register_ai_job"):
        ai_job_id = app._register_ai_job(
            txt_p,
            export_md_to_docx=bool(opts.get("export_md_to_docx")),
            log_file_id=file_id,
        )
        app.set_file_prompt_callback(
            file_id,
            lambda jid=ai_job_id: app.ai_jobs.open_prompt_dialog(jid),
        )
    app.add_file_output("txt", txt_p, file_id=file_id)
    app.add_file_output("srt", srt_p, file_id=file_id)

    if audio_segment is not None and mp3_p is not None:
        try:
            audio_segment.export(mp3_p, format="mp3")
            app.add_file_output("mp3", mp3_p, file_id=file_id)
        except Exception as e:
            app.log_file_event(t("audio_mp3_error", error=str(e)), file_id=file_id)

    if send_txt_to_cursor:
        app._schedule_cursor_postprocess(
            txt_p,
            cursor_api_key=cursor_api_key,
            export_md_to_docx=bool(opts.get("export_md_to_docx")),
            job_id=ai_job_id,
            log_file_id=file_id,
        )

    from whisperfast.core.source_relocate import finalize_source_after_processing

    # Source follows TXT/SRT output directory
    dest_dir = os.path.dirname(os.path.abspath(txt_p))
    final_source = finalize_source_after_processing(app, path, dest_dir, file_id=file_id)

    app.end_file_log("done", file_id=file_id)
    return final_source

