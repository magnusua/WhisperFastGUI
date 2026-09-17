"""Capture settings dialog: sources, codec, auto-record, calendar, extras."""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from whisperfast.core.calendar import (
    CLIENT_ID_ENV,
    GOOGLE_CALENDAR_API_URL,
    GOOGLE_CLOUD_CREDENTIALS_URL,
    GoogleLoopbackAuth,
    clear_google_session,
    resolve_google_client_id,
    store_google_session,
)
from whisperfast.postprocess.common import copy_text_to_clipboard, open_url_in_browser
from whisperfast.secrets_store import unprotect_string
from whisperfast.core.capture import default_capture_dir, list_loopback_devices
from whisperfast.core.capture_encode import BITRATE_CHOICES
from whisperfast.core.capture_prefs import AUTO_RECORD_PRESETS, capture_defaults
from whisperfast.core.capture_names import format_clip_seconds, parse_clip_seconds
from whisperfast.i18n import t
from whisperfast.ui.dialogs import center_toplevel, track_i18n_window


def show_capture_settings_dialog(app):
    existing = getattr(app, "_capture_settings_window", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.lift()
                existing.focus_force()
                return existing
        except tk.TclError:
            app._capture_settings_window = None

    cfg = dict(capture_defaults())
    cfg.update(getattr(app, "capture_cfg", None) or {})

    dialog = tk.Toplevel(app.root)
    dialog.title(t("capture_settings"))
    dialog.transient(app.root)
    dialog.minsize(560, 480)
    dialog.geometry("640x560")

    nb = ttk.Notebook(dialog)
    nb.pack(fill="both", expand=True, padx=8, pady=8)

    vars_map = {}

    def _bool(key, default=False):
        v = tk.BooleanVar(value=bool(cfg.get(key, default)))
        vars_map[key] = v
        return v

    def _str(key, default=""):
        v = tk.StringVar(value=str(cfg.get(key, default) or default))
        vars_map[key] = v
        return v

    def _int(key, default=0):
        v = tk.StringVar(value=str(cfg.get(key, default)))
        vars_map[key] = v
        return v

    # --- Sources ---
    src = ttk.Frame(nb, padding=8)
    nb.add(src, text=t("capture_tab_sources"))
    mix = _str("capture_mix_mode", "all")
    ttk.Label(src, text=t("capture_mix_mode")).pack(anchor="w")
    mix_row = ttk.Frame(src)
    mix_row.pack(fill="x", pady=(0, 6))
    ttk.Radiobutton(mix_row, text=t("capture_mix_all"), value="all", variable=mix).pack(side="left")
    ttk.Radiobutton(
        mix_row, text=t("capture_mix_process"), value="process_allowlist", variable=mix
    ).pack(side="left", padx=8)
    ttk.Radiobutton(mix_row, text=t("capture_mix_device"), value="device", variable=mix).pack(
        side="left"
    )
    ttk.Label(src, text=t("capture_mix_process_hint"), wraplength=580).pack(anchor="w", pady=(0, 6))
    include_mic = _bool("capture_include_mic", True)
    include_sys = _bool("capture_include_system", True)
    ttk.Checkbutton(src, text=t("capture_include_mic"), variable=include_mic).pack(anchor="w")
    ttk.Checkbutton(src, text=t("capture_include_system"), variable=include_sys).pack(anchor="w")
    ttk.Label(src, text=t("capture_loopback_device")).pack(anchor="w", pady=(8, 0))
    loop_var = _str("capture_loopback_device", "")
    devices = list_loopback_devices()
    combo_vals = [""] + [f"{i}|{lab}" for i, lab in devices]
    loop_combo = ttk.Combobox(src, textvariable=loop_var, values=[d[1] for d in devices], width=60)
    loop_combo.pack(fill="x")
    allow = _str("capture_source_allowlist", "")
    deny = _str("capture_source_denylist", "")
    ttk.Label(src, text=t("capture_source_allowlist")).pack(anchor="w", pady=(8, 0))
    ttk.Entry(src, textvariable=allow).pack(fill="x")
    ttk.Label(src, text=t("capture_source_denylist")).pack(anchor="w", pady=(4, 0))
    deny_e = ttk.Entry(src, textvariable=deny)
    deny_e.pack(fill="x")

    def _sync_deny(*_a):
        deny_e.config(state=("normal" if mix.get() == "process_allowlist" else "disabled"))

    mix.trace_add("write", _sync_deny)
    _sync_deny()

    # --- Codec ---
    codec_f = ttk.Frame(nb, padding=8)
    nb.add(codec_f, text=t("capture_tab_codec"))
    codec = _str("capture_codec", "opus")
    ttk.Label(codec_f, text=t("capture_codec")).pack(anchor="w")
    ttk.Combobox(
        codec_f, textvariable=codec, values=("opus", "aac", "mp3", "wav"), state="readonly", width=12
    ).pack(anchor="w")
    bitrate = _str("capture_bitrate_kbit", "24")
    ttk.Label(codec_f, text=t("capture_bitrate")).pack(anchor="w", pady=(8, 0))
    ttk.Combobox(
        codec_f,
        textvariable=bitrate,
        values=[str(b) for b in BITRATE_CHOICES],
        width=8,
    ).pack(anchor="w")
    channels = _str("capture_channels", "mono")
    ttk.Label(codec_f, text=t("capture_channels")).pack(anchor="w", pady=(8, 0))
    ch_row = ttk.Frame(codec_f)
    ch_row.pack(anchor="w")
    ttk.Radiobutton(ch_row, text=t("capture_channels_mono"), value="mono", variable=channels).pack(
        side="left"
    )
    ttk.Radiobutton(
        ch_row, text=t("capture_channels_stereo"), value="stereo", variable=channels
    ).pack(side="left", padx=8)
    ttk.Label(codec_f, text=t("capture_channels_hint"), wraplength=580).pack(anchor="w", pady=(6, 0))

    # --- Auto-record ---
    auto_f = ttk.Frame(nb, padding=8)
    nb.add(auto_f, text=t("capture_tab_auto"))
    auto_on = _bool("auto_record_enabled", False)
    ttk.Checkbutton(auto_f, text=t("auto_record_enabled"), variable=auto_on).pack(anchor="w")
    for key in AUTO_RECORD_PRESETS:
        _bool(f"auto_record_{key}", False)
        ttk.Checkbutton(
            auto_f, text=t(f"auto_record_app_{key}"), variable=vars_map[f"auto_record_{key}"]
        ).pack(anchor="w")
    custom = _str("auto_record_custom_exes", "")
    ttk.Label(auto_f, text=t("auto_record_custom_exes")).pack(anchor="w", pady=(6, 0))
    ttk.Entry(auto_f, textvariable=custom).pack(fill="x")
    delays = ttk.Frame(auto_f)
    delays.pack(fill="x", pady=8)
    for key, label in (
        ("auto_record_start_delay_s", "auto_record_start_delay"),
        ("auto_record_stop_delay_s", "auto_record_stop_delay"),
        ("auto_record_min_duration_s", "auto_record_min_duration"),
        ("auto_record_silence_stop_s", "auto_record_silence_stop"),
    ):
        _int(key, cfg.get(key, 0))
        row = ttk.Frame(delays)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text=t(label), width=36).pack(side="left")
        ttk.Entry(row, textvariable=vars_map[key], width=8).pack(side="left")

    # --- Schedule ---
    cal_f = ttk.Frame(nb, padding=8)
    nb.add(cal_f, text=t("capture_tab_calendar"))
    g_on = _bool("calendar_google_enabled", False)
    o_on = _bool("calendar_outlook_enabled", False)
    i_on = _bool("calendar_ics_enabled", False)
    ttk.Checkbutton(cal_f, text=t("calendar_google"), variable=g_on).pack(anchor="w")
    ttk.Label(cal_f, text=t("calendar_google_hint"), wraplength=580, justify="left").pack(
        anchor="w", pady=(2, 6)
    )
    gid = _str("google_calendar_client_id", "")
    gsec = _str("google_calendar_client_secret", "")
    gmail = _str("google_calendar_email", "")
    gids = _str("google_calendar_ids", "primary")
    ttk.Label(cal_f, text=t("calendar_google_client_id")).pack(anchor="w")
    ttk.Entry(cal_f, textvariable=gid).pack(fill="x")
    ttk.Label(cal_f, text=t("calendar_google_secret_optional")).pack(anchor="w", pady=(6, 0))
    ttk.Entry(cal_f, textvariable=gsec, show="*").pack(fill="x")
    ttk.Label(cal_f, text=t("calendar_google_ids")).pack(anchor="w", pady=(6, 0))
    ttk.Entry(cal_f, textvariable=gids).pack(fill="x")
    google_status = ttk.Label(cal_f, text="", wraplength=580, justify="left")
    google_status.pack(anchor="w", pady=(6, 2))
    google_busy = {"on": False, "session": None}

    def _google_signed_in() -> bool:
        email = (gmail.get() or "").strip()
        token = unprotect_string(str(cfg.get("google_calendar_refresh_token") or ""))
        if not token:
            token = unprotect_string(
                str((getattr(app, "capture_cfg", None) or {}).get("google_calendar_refresh_token") or "")
            )
        return bool(email or token)

    def _refresh_google_status(extra: str = ""):
        if extra:
            google_status.configure(text=extra)
            return
        if _google_signed_in():
            who = (gmail.get() or "").strip()
            google_status.configure(text=t("calendar_google_signed_in", email=who or "Google"))
        else:
            google_status.configure(text="")

    def _persist_google_now():
        live = dict(getattr(app, "capture_cfg", None) or cfg)
        live["google_calendar_refresh_token"] = cfg.get("google_calendar_refresh_token") or ""
        live["google_calendar_email"] = cfg.get("google_calendar_email") or gmail.get()
        live["google_calendar_client_id"] = gid.get().strip()
        live["google_calendar_client_secret"] = gsec.get()
        live["calendar_google_enabled"] = bool(g_on.get())
        app.capture_cfg = live
        persist = getattr(app, "_persist_settings", None)
        if callable(persist):
            persist()

    def _close_google_session():
        session = google_busy.get("session")
        google_busy["session"] = None
        google_busy["on"] = False
        if session is not None:
            try:
                session.close()
            except Exception:
                pass

    def do_google_login():
        if google_busy["on"]:
            return
        client_id = resolve_google_client_id(gid.get())
        if not client_id:
            copy_text_to_clipboard("http://127.0.0.1")
            open_url_in_browser(GOOGLE_CALENDAR_API_URL)
            open_url_in_browser(GOOGLE_CLOUD_CREDENTIALS_URL)
            messagebox.showinfo(
                t("capture_settings"),
                t("calendar_google_need_client", env=CLIENT_ID_ENV),
                parent=dialog,
            )
            return
        gid.set(client_id)
        try:
            session = GoogleLoopbackAuth(client_id, gsec.get().strip())
            session.open_browser()
        except Exception as e:
            if "session" in locals():
                try:
                    session.close()
                except Exception:
                    pass
            messagebox.showerror(t("capture_settings"), str(e), parent=dialog)
            return
        google_busy["on"] = True
        google_busy["session"] = session
        login_btn.configure(state="disabled")
        _refresh_google_status(t("calendar_google_waiting"))

        def worker():
            err = ""
            token = None
            try:
                token = session.wait(180)
            except Exception as e:
                err = str(e)
            finally:
                try:
                    session.close()
                except Exception:
                    pass

            def done():
                google_busy["session"] = None
                google_busy["on"] = False
                try:
                    if login_btn.winfo_exists():
                        login_btn.configure(state="normal")
                except tk.TclError:
                    return
                if token:
                    refresh = token.get("refresh_token") or unprotect_string(
                        str(cfg.get("google_calendar_refresh_token") or "")
                    )
                    if not refresh:
                        messagebox.showerror(
                            t("capture_settings"), t("calendar_google_no_refresh"), parent=dialog
                        )
                        _refresh_google_status()
                        return
                    email = str(token.get("email") or "").strip()
                    store_google_session(cfg, refresh, email)
                    gmail.set(email)
                    g_on.set(True)
                    _persist_google_now()
                    _refresh_google_status()
                    messagebox.showinfo(
                        t("capture_settings"),
                        t("calendar_google_ok", email=email or "Google"),
                        parent=dialog,
                    )
                    return
                if err == "timeout":
                    messagebox.showinfo(
                        t("capture_settings"), t("calendar_google_timeout"), parent=dialog
                    )
                elif err:
                    messagebox.showerror(t("capture_settings"), err, parent=dialog)
                _refresh_google_status()

            try:
                dialog.after(0, done)
            except tk.TclError:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def do_google_logout():
        _close_google_session()
        clear_google_session(cfg)
        gmail.set("")
        g_on.set(False)
        _persist_google_now()
        _refresh_google_status()

    g_btns = ttk.Frame(cal_f)
    g_btns.pack(fill="x", pady=4)
    login_btn = ttk.Button(g_btns, text=t("calendar_google_login"), command=do_google_login)
    login_btn.pack(side="left")
    ttk.Button(g_btns, text=t("calendar_google_logout"), command=do_google_logout).pack(
        side="left", padx=6
    )
    _refresh_google_status()
    ttk.Checkbutton(cal_f, text=t("calendar_outlook"), variable=o_on).pack(anchor="w", pady=(8, 0))
    ttk.Checkbutton(cal_f, text=t("calendar_ics"), variable=i_on).pack(anchor="w")
    ics = _str("calendar_ics_path", "")
    ics_row = ttk.Frame(cal_f)
    ics_row.pack(fill="x")
    ttk.Entry(ics_row, textvariable=ics).pack(side="left", fill="x", expand=True)

    def pick_ics():
        path = filedialog.askopenfilename(
            parent=dialog, filetypes=[("ICS", "*.ics"), (t("all_files_type"), "*.*")]
        )
        if path:
            ics.set(path)

    ttk.Button(ics_row, text="…", width=3, command=pick_ics).pack(side="left", padx=4)
    lead = _int("calendar_start_lead_min", 2)
    ttk.Label(cal_f, text=t("calendar_start_lead")).pack(anchor="w", pady=(8, 0))
    ttk.Entry(cal_f, textvariable=lead, width=8).pack(anchor="w")
    filt = _str("calendar_event_filter", "all")
    ttk.Label(cal_f, text=t("calendar_event_filter")).pack(anchor="w", pady=(6, 0))
    ttk.Combobox(
        cal_f,
        textvariable=filt,
        values=("all", "meeting_url", "keywords"),
        state="readonly",
        width=16,
    ).pack(anchor="w")
    kws = _str("calendar_keywords", "")
    ttk.Label(cal_f, text=t("calendar_keywords")).pack(anchor="w")
    ttk.Entry(cal_f, textvariable=kws).pack(fill="x")

    # --- Other ---
    other = ttk.Frame(nb, padding=8)
    nb.add(other, text=t("capture_tab_other"))
    cap_dir = _str("capture_dir", "")
    ttk.Label(other, text=t("capture_dir")).pack(anchor="w")
    dir_row = ttk.Frame(other)
    dir_row.pack(fill="x")
    ttk.Entry(dir_row, textvariable=cap_dir).pack(side="left", fill="x", expand=True)

    def pick_dir():
        path = filedialog.askdirectory(parent=dialog, initialdir=default_capture_dir())
        if path:
            cap_dir.set(path)

    ttk.Button(dir_row, text="…", width=3, command=pick_dir).pack(side="left", padx=4)
    tmpl = _str("capture_filename_template", "%W %Y-%m-%d %H.%M.%S")
    ttk.Label(other, text=t("capture_filename_template")).pack(anchor="w", pady=(8, 0))
    ttk.Entry(other, textvariable=tmpl).pack(fill="x")
    ttk.Label(other, text=t("capture_filename_hint"), wraplength=580, justify="left").pack(
        anchor="w", pady=(4, 6)
    )
    use_cal = _bool("capture_filename_use_calendar", False)
    ttk.Checkbutton(other, text=t("capture_filename_use_calendar"), variable=use_cal).pack(anchor="w")
    date_sub = _bool("capture_auto_date_subdir", True)
    ttk.Checkbutton(other, text=t("capture_auto_date_subdir"), variable=date_sub).pack(anchor="w")
    keep = _bool("keep_audio", True)
    ttk.Checkbutton(other, text=t("keep_audio"), variable=keep).pack(anchor="w")
    user_n = _str("user_name", "You")
    them_n = _str("them_name", "Them")
    names = ttk.Frame(other)
    names.pack(fill="x", pady=6)
    ttk.Label(names, text=t("user_name")).pack(side="left")
    ttk.Entry(names, textvariable=user_n, width=16).pack(side="left", padx=4)
    ttk.Label(names, text=t("them_name")).pack(side="left")
    ttk.Entry(names, textvariable=them_n, width=16).pack(side="left", padx=4)
    clip = _str("capture_clip_seconds", format_clip_seconds(int(cfg.get("capture_clip_seconds") or 122)))
    clip.set(format_clip_seconds(parse_clip_seconds(clip.get(), 122)))
    ttk.Label(other, text=t("capture_clip_duration")).pack(anchor="w", pady=(6, 0))
    ttk.Entry(other, textvariable=clip, width=10).pack(anchor="w")
    maxd = _int("capture_max_duration_s", 0)
    ttk.Label(other, text=t("capture_max_duration")).pack(anchor="w", pady=(6, 0))
    ttk.Entry(other, textvariable=maxd, width=10).pack(anchor="w")
    live = _bool("live_preview_enabled", False)
    ttk.Checkbutton(other, text=t("live_preview_enabled"), variable=live).pack(anchor="w", pady=(8, 0))
    live_m = _str("live_preview_model", "tiny")
    ttk.Combobox(other, textvariable=live_m, values=("tiny", "base"), state="readonly", width=10).pack(
        anchor="w"
    )
    hook = _str("on_stop_hook", "")
    ttk.Label(other, text=t("on_stop_hook")).pack(anchor="w", pady=(8, 0))
    ttk.Entry(other, textvariable=hook).pack(fill="x")
    ttk.Button(
        other,
        text=t("capture_consent_reset"),
        command=lambda: app.capture_consent_shown.set(False),
    ).pack(anchor="w", pady=10)

    def apply_and_close():
        _close_google_session()
        out = dict(getattr(app, "capture_cfg", None) or capture_defaults())
        for key, var in vars_map.items():
            if isinstance(var, tk.BooleanVar):
                out[key] = bool(var.get())
            else:
                out[key] = var.get()
        try:
            out["capture_bitrate_kbit"] = int(out.get("capture_bitrate_kbit") or 24)
        except (TypeError, ValueError):
            out["capture_bitrate_kbit"] = 24
        for int_key in (
            "auto_record_start_delay_s",
            "auto_record_stop_delay_s",
            "auto_record_min_duration_s",
            "auto_record_silence_stop_s",
            "calendar_start_lead_min",
            "capture_max_duration_s",
        ):
            try:
                out[int_key] = int(float(out.get(int_key) or 0))
            except (TypeError, ValueError):
                out[int_key] = 0
        out["capture_clip_seconds"] = parse_clip_seconds(
            str(out.get("capture_clip_seconds") or "2:02"), 122
        )
        if out.get("capture_mix_mode") == "device":
            label = str(out.get("capture_loopback_device") or "")
            for idx, lab in devices:
                if lab == label or label.startswith(str(idx)):
                    out["capture_loopback_device"] = str(idx)
                    break
        out["google_calendar_refresh_token"] = (
            cfg.get("google_calendar_refresh_token")
            or out.get("google_calendar_refresh_token")
            or ""
        )
        out["google_calendar_email"] = (gmail.get() or "").strip()
        app.capture_cfg = out
        clip_var = getattr(app, "capture_clip_var", None)
        if clip_var is not None:
            clip_var.set(format_clip_seconds(int(out["capture_clip_seconds"])))
        persist = getattr(app, "_persist_settings", None)
        if callable(persist):
            persist()
        dialog.destroy()
        if getattr(app, "_capture_settings_window", None) is dialog:
            app._capture_settings_window = None

    btns = ttk.Frame(dialog)
    btns.pack(fill="x", padx=8, pady=(0, 8))

    def _on_close():
        _close_google_session()
        try:
            dialog.destroy()
        except tk.TclError:
            pass
        if getattr(app, "_capture_settings_window", None) is dialog:
            app._capture_settings_window = None

    ttk.Button(btns, text=t("ok"), command=apply_and_close).pack(side="right")
    ttk.Button(btns, text=t("cancel"), command=_on_close).pack(side="right", padx=6)

    dialog.protocol("WM_DELETE_WINDOW", _on_close)
    center_toplevel(app, dialog)
    app._capture_settings_window = dialog
    track_i18n_window(app, dialog, lambda: dialog.title(t("capture_settings")))
    del combo_vals
    return dialog
