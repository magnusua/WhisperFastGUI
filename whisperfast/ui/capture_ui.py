"""Capture button / consent / hotkey helpers (Tkinter)."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox

from whisperfast.core.auto_detect import meeting_window_title, match_allowlist, parse_custom_exes
from whisperfast.core.auto_record import (
    START_AUTO,
    START_CALENDAR,
    STOP_EVENT_END,
    STOP_GONE,
    STOP_SILENCE,
    AutoRecordMachine,
)
from whisperfast.core.calendar import (
    collect_events,
    collect_events_async,
    overlapping_event,
    upcoming_event,
)
from whisperfast.core.capture import (
    capture_available,
    default_capture_dir,
    get_capture_session,
    recover_captures,
)
from whisperfast.core.capture_finalize import (
    capture_dir_from_settings,
    finalize_wav,
    run_on_stop_hook,
    settings_from_app,
)
from whisperfast.core.capture_names import format_clip_seconds, parse_clip_seconds
from whisperfast.core.capture_prefs import enabled_auto_apps
from whisperfast.core.ipc_cmd import take_command
from whisperfast.i18n import t
from whisperfast.ui import toolbar_icons

_machine = AutoRecordMachine()
_poll_started = False
_manual_hold = False


def ensure_consent(app) -> bool:
    if getattr(app, "capture_consent_shown", None) and app.capture_consent_shown.get():
        return True
    ok = messagebox.askokcancel(t("capture_title"), t("capture_consent"), parent=app.root)
    if not ok:
        return False
    if getattr(app, "capture_consent_shown", None) is not None:
        app.capture_consent_shown.set(True)
        persist = getattr(app, "_persist_settings", None)
        if callable(persist):
            persist()
    return True


def toggle_capture(app) -> None:
    global _manual_hold
    session = get_capture_session()
    if session.running:
        _manual_hold = False
        _stop_and_enqueue(app, session)
        return
    if not capture_available():
        messagebox.showinfo(t("capture_title"), t("capture_need_sounddevice"), parent=app.root)
        return
    if not ensure_consent(app):
        return
    _manual_hold = True
    _start_session(app, trigger="manual")


def start_capture(app, trigger: str = "manual") -> None:
    session = get_capture_session()
    if session.running:
        return
    if not capture_available():
        return
    if not ensure_consent(app):
        return
    _start_session(app, trigger=trigger)


def stop_capture(app) -> None:
    global _manual_hold
    _manual_hold = False
    session = get_capture_session()
    if session.running:
        _stop_and_enqueue(app, session)


def toggle_pause(app) -> None:
    session = get_capture_session()
    if not session.running:
        return
    logger = _capture_logger(app, getattr(session, "log_file_id", None))
    session.toggle_pause(log_func=logger)
    refresh_capture_buttons(app)


def save_clip(app) -> None:
    session = get_capture_session()
    if not session.running:
        return
    settings = settings_from_app(app)
    requested = parse_clip_seconds(
        _clip_var_text(app), fallback=int(settings.get("capture_clip_seconds") or 122)
    )
    elapsed = session.elapsed_seconds()
    if elapsed > 0:
        requested = min(requested, max(1, int(elapsed)))
    duration = format_clip_seconds(requested)
    log = getattr(app, "log", None)
    if callable(log):
        log(t("capture_clip_saving", duration=duration))
    try:
        wav = session.copy_last_seconds(float(requested))
    except Exception as e:
        if callable(log):
            log(t("capture_clip_failed"))
        messagebox.showerror(t("capture_title"), str(e), parent=app.root)
        return
    final = _finalize_and_enqueue(app, wav, settings, is_clip=True)
    _log_clip_result(app, session, final, duration)


def _log_clip_result(app, session, path: str, duration: str) -> None:
    """Always write clip outcome into the main log window (and the live recording)."""
    log = getattr(app, "log", None)
    if not path or not os.path.isfile(path):
        if callable(log):
            log(t("capture_clip_failed"))
        return
    abs_path = os.path.abspath(path)
    msg = t("capture_clip_saved", path=abs_path, duration=duration)
    if callable(log):
        log(msg, "link")
    parent_id = getattr(session, "log_file_id", None) or ""
    if parent_id:
        _capture_logger(app, parent_id)(msg)


def recover_interrupted_captures(app) -> None:
    settings = settings_from_app(app)
    dirs = []
    cap = (settings.get("capture_dir") or "").strip()
    if cap and os.path.isdir(cap):
        dirs.append(cap)
    default_dir = default_capture_dir()
    if default_dir not in dirs:
        dirs.append(default_dir)
    seen = set()
    for folder in dirs:
        for path in recover_captures(folder):
            key = os.path.normcase(path)
            if key in seen:
                continue
            seen.add(key)
            fid = _log_capture_file(app, path, "capture_recovered")
            _finalize_and_enqueue(app, path, settings, is_clip=False, log_file_id=fid)


def capture_blocks_shutdown() -> bool:
    return bool(get_capture_session().running)


def _capture_logger(app, file_id: str = ""):
    if file_id and hasattr(app, "make_file_logger"):
        return app.make_file_logger(file_id)
    return getattr(app, "log", lambda *_a, **_k: None)


def _log_capture_file(app, path: str, event_key: str) -> str:
    """File-session for a recording so the path is a clickable log link (open / folder).

    Uses the log panel only — does not create an archive job until transcription.
    """
    if not path:
        return ""
    abs_path = os.path.abspath(path)
    file_id = ""
    finder = getattr(app, "find_file_log_id", None)
    if callable(finder):
        file_id = finder(abs_path) or ""
    panel = getattr(app, "log_panel", None)
    if not file_id and panel is not None and hasattr(panel, "begin_file"):
        file_id = panel.begin_file(abs_path, name=os.path.basename(abs_path)) or ""
        if file_id:
            panel.log_file_event(t(event_key, path=abs_path), file_id=file_id)
            add = getattr(panel, "add_file_output", None)
            if callable(add):
                add("source", abs_path, file_id=file_id)
            return str(file_id)
    if not file_id and hasattr(app, "begin_file_log"):
        file_id = app.begin_file_log(abs_path, name=os.path.basename(abs_path)) or ""
    if file_id:
        app.log_file_event(t(event_key, path=abs_path), file_id=file_id)
        add = getattr(app, "add_file_output", None)
        if callable(add):
            add("source", abs_path, file_id=file_id, reindex=False)
        return str(file_id)
    app.log(t(event_key, path=abs_path), "link")
    return ""


def _update_capture_log(app, file_id: str, path: str, event_key: str) -> None:
    if not path:
        return
    abs_path = os.path.abspath(path)
    panel = getattr(app, "log_panel", None)
    if file_id and panel is not None:
        if hasattr(panel, "set_file_source"):
            panel.set_file_source(abs_path, file_id=file_id)
        add = getattr(panel, "add_file_output", None)
        if callable(add):
            add("source", abs_path, file_id=file_id)
        panel.log_file_event(t(event_key, path=abs_path), file_id=file_id)
        return
    if file_id and hasattr(app, "set_file_source"):
        try:
            app.set_file_source(file_id, abs_path, reindex=False)
        except TypeError:
            app.set_file_source(file_id, abs_path)
    if file_id and hasattr(app, "add_file_output"):
        app.add_file_output("source", abs_path, file_id=file_id, reindex=False)
    if file_id:
        app.log_file_event(t(event_key, path=abs_path), file_id=file_id)
    else:
        _log_capture_file(app, abs_path, event_key)


def _enqueue_capture(app, path: str) -> None:
    """Put a finished recording into the same processing queue as Add files."""
    if not path:
        return
    abs_path = os.path.abspath(path)
    if not os.path.isfile(abs_path):
        return
    ctrl = getattr(app, "queue_ctrl", None)
    if ctrl is None:
        add = getattr(app, "add_files_to_queue", None)
        if callable(add):
            add([abs_path])
        return
    added = 0
    try:
        added, _skipped = ctrl.add_files([abs_path])
    except Exception:
        added = 0
    if added:
        try:
            ctrl.refresh_treeview()
        except Exception:
            pass
        return
    # add_files returned 0: already queued, rejected, or a widget error.
    # If the file is valid and not already in the list, force it in.
    from whisperfast.core.input_files import is_valid_file
    from whisperfast.utils import make_queue_item, normalize_queue_path

    path_norm = normalize_queue_path(abs_path) or abs_path
    keys = set()
    try:
        keys.add(os.path.normcase(path_norm))
        keys.add(os.path.normcase(os.path.abspath(path_norm)))
    except OSError:
        keys.add(os.path.normcase(path_norm))
    for item in ctrl.queue:
        existing = str(item.get("path") or "")
        if not existing:
            continue
        try:
            if os.path.normcase(existing) in keys or os.path.normcase(
                os.path.abspath(existing)
            ) in keys:
                return
        except OSError:
            if os.path.normcase(existing) in keys:
                return
    if not is_valid_file(abs_path):
        return
    ctrl.queue.append(make_queue_item(path_norm))
    try:
        ctrl.save_to_file()
        ctrl.register_output_paths([path_norm])
        ctrl.refresh_treeview()
    except Exception:
        pass


def _start_session(app, trigger: str) -> None:
    settings = settings_from_app(app)
    auto = trigger in ("auto", "calendar")
    out_dir = capture_dir_from_settings(settings, auto=auto)
    loop_dev = None
    raw_dev = str(settings.get("capture_loopback_device") or "").strip()
    if settings.get("capture_mix_mode") == "device" and raw_dev:
        loop_dev = raw_dev
    title = ""
    attendees = []
    try:
        events = collect_events(settings)
        ev = overlapping_event(events) or upcoming_event(
            events, lead_min=int(settings.get("calendar_start_lead_min") or 2)
        )
        if ev:
            title = str(ev.get("title") or "")
            attendees = list(ev.get("attendees") or [])
    except Exception:
        pass
    session = get_capture_session()
    try:
        path = session.start(
            out_dir,
            log_func=None,
            trigger=trigger,
            include_mic=bool(settings.get("capture_include_mic", True)),
            include_system=bool(settings.get("capture_include_system", True)),
            loopback_device=loop_dev,
            calendar_title=title,
            calendar_attendees=attendees,
        )
    except Exception as e:
        messagebox.showerror(t("capture_title"), str(e), parent=app.root)
        return
    session.log_file_id = _log_capture_file(app, path or session.path, "capture_started")
    session.set_log(_capture_logger(app, session.log_file_id))
    refresh_capture_buttons(app)
    if settings.get("live_preview_enabled"):
        try:
            from whisperfast.core.live_preview import start_preview
            from whisperfast.ui.live_preview_ui import show_live_preview

            start_preview(
                session,
                model_name=str(settings.get("live_preview_model") or "tiny"),
                device_mode=str(getattr(app, "device_mode", None) and app.device_mode.get() or "CPU"),
            )
            show_live_preview(app)
        except Exception:
            pass


def _stop_and_enqueue(app, session) -> None:
    try:
        from whisperfast.core.live_preview import stop_preview

        stop_preview()
    except Exception:
        pass
    fid = getattr(session, "log_file_id", None) or ""
    path = session.stop(log_func=None)
    refresh_capture_buttons(app)
    if session.error:
        _capture_logger(app, fid)(t("capture_error", error=session.error))
    settings = settings_from_app(app)
    final = _finalize_and_enqueue(app, path, settings, is_clip=False, log_file_id=fid)
    run_on_stop_hook(str(settings.get("on_stop_hook") or ""), final or path or "", log_func=_capture_logger(app, fid))


def _finalize_and_enqueue(app, wav_path: str, settings: dict, is_clip: bool, log_file_id: str = "") -> str:
    if not wav_path or not os.path.isfile(wav_path):
        return ""
    win = "FTW"
    try:
        apps = enabled_auto_apps(settings)
        custom = parse_custom_exes(str(settings.get("auto_record_custom_exes") or ""))
        win = meeting_window_title(apps, custom, fallback="FTW") or "FTW"
    except Exception:
        win = "FTW"
    cal = ""
    session = get_capture_session()
    if settings.get("capture_filename_use_calendar"):
        cal = session.calendar_title or ""
        if not cal:
            try:
                ev = overlapping_event(collect_events(settings))
                if ev:
                    cal = str(ev.get("title") or "")
            except Exception:
                cal = ""
    logger = _capture_logger(app, log_file_id)
    final = finalize_wav(
        wav_path,
        settings,
        window_title=win,
        calendar_title=cal,
        is_clip=is_clip,
        log_func=logger,
    )
    target = final if final and os.path.isfile(final) else wav_path
    try:
        if is_clip:
            _log_capture_file(app, target, "capture_clip_saved")
        elif log_file_id:
            _update_capture_log(app, log_file_id, target, "capture_saved")
        else:
            _log_capture_file(app, target, "capture_saved")
    except Exception:
        pass
    enqueue_path = target if target and os.path.isfile(target) else wav_path
    try:
        _enqueue_capture(app, enqueue_path)
    except Exception as e:
        _capture_logger(app, log_file_id)(str(e))
    return enqueue_path or ""


def _clip_var_text(app) -> str:
    var = getattr(app, "capture_clip_var", None)
    if var is None:
        return ""
    try:
        return var.get()
    except tk.TclError:
        return ""


def sync_log_cancel_button(app) -> None:
    """Enable the log-header Stop button while a recording or a queue run is active."""
    btn = getattr(app, "cancel_btn", None)
    if btn is None:
        return
    try:
        recording = get_capture_session().running
    except Exception:
        recording = False
    lock = getattr(app, "_process_queue_lock", None)
    busy = bool(lock is not None and lock.locked())
    try:
        btn.config(state=("normal" if recording or busy else "disabled"))
    except tk.TclError:
        pass


def handle_log_cancel(app) -> None:
    """Stop button above the log: cancel transcription and stop an active recording."""
    recording = False
    try:
        recording = get_capture_session().running
    except Exception:
        recording = False
    if recording:
        stop_capture(app)
    lock = getattr(app, "_process_queue_lock", None)
    if lock is not None and lock.locked():
        app.cancel_requested = True
        log = getattr(app, "log", None)
        if callable(log):
            log(t("waiting_segment"))


def refresh_capture_buttons(app) -> None:
    session = get_capture_session()
    running = session.running
    paused = session.paused
    sync_log_cancel_button(app)
    pause_btn = getattr(app, "capture_pause_btn", None)
    clip_btn = getattr(app, "capture_clip_btn", None)
    toolbar_icons.apply_capture_state(app, running, paused)
    if pause_btn is not None:
        try:
            pause_btn.config(state=("normal" if running else "disabled"))
        except tk.TclError:
            pass
    if clip_btn is not None:
        try:
            clip_btn.config(state=("normal" if running else "disabled"))
        except tk.TclError:
            pass
    clip_entry = getattr(app, "capture_clip_entry", None)
    if clip_entry is not None:
        try:
            clip_entry.config(state="normal")
        except tk.TclError:
            pass


def bind_capture_hotkey(app) -> None:
    def on_key(event=None):
        del event
        toggle_capture(app)
        return "break"

    def on_pause(event=None):
        del event
        toggle_pause(app)
        return "break"

    try:
        app.root.bind_all("<Control-Shift-R>", on_key)
        app.root.bind_all("<Control-Shift-r>", on_key)
        app.root.bind_all("<Control-Shift-P>", on_pause)
        app.root.bind_all("<Control-Shift-p>", on_pause)
    except tk.TclError:
        pass


def start_background_polls(app) -> None:
    global _poll_started
    if _poll_started:
        return
    _poll_started = True

    def tick():
        try:
            _poll_ipc(app)
            _poll_auto(app)
            _poll_max_duration(app)
            refresh_capture_buttons(app)
        except Exception:
            pass
        try:
            app.root.after(1000, tick)
        except tk.TclError:
            pass

    try:
        app.root.after(800, tick)
    except tk.TclError:
        pass


def _poll_ipc(app) -> None:
    from whisperfast.core.ipc_cmd import take_queued_commands

    commands = []
    single = take_command()
    if single:
        commands.append(single)
    commands.extend(take_queued_commands())
    for cmd in commands:
        _dispatch_ipc(app, cmd)


def _dispatch_ipc(app, cmd) -> None:
    action = str(cmd.get("action") or "").strip().lower()
    if action == "record_start":
        start_capture(app, trigger="manual")
    elif action == "record_stop":
        stop_capture(app)
    elif action == "process":
        folder = str(cmd.get("folder") or "")
        if folder and os.path.isdir(folder):
            from whisperfast.core.input_files import get_valid_files_from_directory

            paths = get_valid_files_from_directory(folder)
            if paths:
                app.queue_ctrl.add_files(paths)
    elif action == "telegram_file":
        from whisperfast.telegram.gui_bridge import apply_telegram_command

        apply_telegram_command(app, cmd)


def _poll_max_duration(app) -> None:
    settings = settings_from_app(app)
    max_s = int(settings.get("capture_max_duration_s") or 0)
    if max_s <= 0:
        return
    session = get_capture_session()
    if session.running and session.elapsed_seconds() >= max_s:
        _stop_and_enqueue(app, session)


def _poll_auto(app) -> None:
    global _manual_hold
    settings = settings_from_app(app)
    session = get_capture_session()
    if _manual_hold or (session.running and session.trigger == "manual"):
        return
    match = None
    if settings.get("auto_record_enabled"):
        match = match_allowlist(
            enabled_auto_apps(settings),
            parse_custom_exes(str(settings.get("auto_record_custom_exes") or "")),
        )
    cal_on = any(
        settings.get(k)
        for k in ("calendar_google_enabled", "calendar_outlook_enabled", "calendar_ics_enabled")
    )
    event = None
    if cal_on:
        try:
            # Non-blocking: this runs on the 1s Tk main-thread poll tick, and
            # collect_events() itself does blocking Outlook COM / Google HTTP
            # calls on a cache miss.
            events = collect_events_async(settings)
            event = upcoming_event(
                events, lead_min=int(settings.get("calendar_start_lead_min") or 2)
            )
        except Exception:
            event = None
    action = _machine.tick(
        auto_enabled=bool(settings.get("auto_record_enabled")),
        match=match,
        calendar_event=event,
        recording=session.running,
        trigger=session.trigger if session.running else "",
        elapsed_s=session.elapsed_seconds(),
        last_sound_age_s=session.last_sound_age(),
        start_delay_s=float(settings.get("auto_record_start_delay_s") or 0),
        stop_delay_s=float(settings.get("auto_record_stop_delay_s") or 0),
        min_duration_s=float(settings.get("auto_record_min_duration_s") or 0),
        silence_stop_s=float(settings.get("auto_record_silence_stop_s") or 0),
        calendar_enabled=cal_on,
    )
    if action in (START_AUTO, START_CALENDAR):
        if not ensure_consent(app):
            return
        _start_session(app, trigger="auto" if action == START_AUTO else "calendar")
    elif action in (STOP_GONE, STOP_SILENCE, STOP_EVENT_END):
        if session.running and session.trigger != "manual":
            _stop_and_enqueue(app, session)


def normalize_clip_entry(app) -> None:
    var = getattr(app, "capture_clip_var", None)
    if var is None:
        return
    seconds = parse_clip_seconds(var.get(), fallback=122)
    session = get_capture_session()
    if session.running:
        seconds = min(seconds, max(1, int(session.elapsed_seconds() or seconds)))
    var.set(format_clip_seconds(seconds))
    if getattr(app, "capture_cfg", None) is not None:
        app.capture_cfg["capture_clip_seconds"] = seconds
