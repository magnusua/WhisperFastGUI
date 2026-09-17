"""Archive window: search past jobs, click an SRT line to hear it, cascade-delete."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox, ttk

from whisperfast.audio_player import play_range, resolve_audio_path, stop as stop_playback
from whisperfast.i18n import t
from whisperfast.library import ConversationLibrary, get_library
from whisperfast.open_path import open_file, open_file_location
from whisperfast.srt_parse import parse_srt_file
from whisperfast.ui.dialogs import center_toplevel, track_i18n_window


def show_archive_window(app):
    existing = getattr(app, "_archive_window", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.lift()
                existing.focus_force()
                refresh = getattr(existing, "_wf_refresh", None)
                if callable(refresh):
                    refresh()
                return existing
        except tk.TclError:
            app._archive_window = None

    lib: ConversationLibrary = getattr(app, "library", None) or get_library()
    dialog = tk.Toplevel(app.root)
    dialog.title(t("archive_title"))
    dialog.transient(app.root)
    dialog.minsize(860, 520)
    dialog.geometry("960x600")

    outer = ttk.Frame(dialog, padding=8)
    outer.pack(fill="both", expand=True)

    top = ttk.Frame(outer)
    top.pack(fill="x", pady=(0, 6))
    search_var = tk.StringVar()
    search_entry = ttk.Entry(top, textvariable=search_var)
    search_entry.pack(side="left", fill="x", expand=True)
    search_btn = ttk.Button(top, text=t("archive_search"))
    search_btn.pack(side="left", padx=(6, 0))

    body = ttk.Panedwindow(outer, orient="horizontal")
    body.pack(fill="both", expand=True)

    left = ttk.Frame(body)
    right = ttk.Frame(body)
    body.add(left, weight=1)
    body.add(right, weight=2)

    cols = ("when", "name", "summary")
    tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse")
    tree.heading("when", text=t("archive_col_when"))
    tree.heading("name", text=t("archive_col_name"))
    tree.heading("summary", text=t("archive_col_summary"))
    tree.column("when", width=130, minwidth=80)
    tree.column("name", width=180, minwidth=80)
    tree.column("summary", width=220, minwidth=80)
    scroll_l = ttk.Scrollbar(left, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scroll_l.set)
    tree.pack(side="left", fill="both", expand=True)
    scroll_l.pack(side="right", fill="y")

    cue_list = tk.Listbox(right, font=("Consolas", 10))
    scroll_r = ttk.Scrollbar(right, orient="vertical", command=cue_list.yview)
    cue_list.configure(yscrollcommand=scroll_r.set)
    cue_list.pack(side="left", fill="both", expand=True)
    scroll_r.pack(side="right", fill="y")

    actions = ttk.Frame(outer)
    actions.pack(fill="x", pady=(8, 0))
    open_btn = ttk.Button(actions, text=t("archive_open_txt"))
    loc_btn = ttk.Button(actions, text=t("archive_show_folder"))
    del_btn = ttk.Button(actions, text=t("archive_delete"))
    qa_btn = ttk.Button(actions, text=t("archive_ask"))
    hint_lbl = ttk.Label(actions, text=t("archive_click_hint"))
    open_btn.pack(side="left")
    loc_btn.pack(side="left", padx=4)
    del_btn.pack(side="left", padx=4)
    qa_btn.pack(side="left", padx=4)
    hint_lbl.pack(side="left", padx=10)

    state = {"jobs": {}, "cues": [], "job": None}

    def _load(query=""):
        for iid in tree.get_children():
            tree.delete(iid)
        state["jobs"] = {}
        jobs = lib.search(query, limit=300)
        for job in jobs:
            jid = job.get("id") or ""
            state["jobs"][jid] = job
            tree.insert(
                "",
                "end",
                iid=jid,
                values=(
                    (job.get("created_at") or "")[:19].replace("T", " "),
                    job.get("name") or "",
                    (job.get("summary") or "")[:80],
                ),
            )

    def _selected_job():
        sel = tree.selection()
        if not sel:
            return None
        return state["jobs"].get(sel[0])

    def _show_job(job):
        state["job"] = job
        cue_list.delete(0, tk.END)
        state["cues"] = []
        if not job:
            return
        srt = job.get("srt_path") or ""
        if srt and os.path.isfile(srt):
            try:
                cues = parse_srt_file(srt)
            except OSError:
                cues = []
            state["cues"] = cues
            for c in cues:
                start = float(c.get("start") or 0)
                h = int(start // 3600)
                m = int((start % 3600) // 60)
                s = int(start % 60)
                cue_list.insert(tk.END, f"{h:02d}:{m:02d}:{s:02d}  {c.get('text') or ''}")
            return
        txt = job.get("txt_path") or ""
        if txt and os.path.isfile(txt):
            try:
                with open(txt, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        cue_list.insert(tk.END, line.rstrip("\n"))
            except OSError:
                pass

    def _on_select(_event=None):
        _show_job(_selected_job())

    def _on_cue_activate(_event=None):
        job = state.get("job")
        if not job:
            return
        sel = cue_list.curselection()
        if not sel:
            return
        idx = int(sel[0])
        audio = resolve_audio_path(job)
        if not audio:
            messagebox.showinfo(t("archive_title"), t("archive_no_audio"), parent=dialog)
            return
        cues = state.get("cues") or []
        if idx < len(cues):
            start = float(cues[idx].get("start") or 0)
            end = float(cues[idx].get("end") or start + 2.5)
            dur = max(0.5, end - start)
        else:
            start, dur = 0.0, 3.0
        if not play_range(audio, start, dur):
            messagebox.showwarning(t("archive_title"), t("archive_play_failed"), parent=dialog)

    def _open_txt():
        job = _selected_job()
        if not job:
            return
        path = job.get("txt_path") or job.get("source") or ""
        if path and os.path.isfile(path):
            open_file(path)

    def _show_loc():
        job = _selected_job()
        if not job:
            return
        path = job.get("txt_path") or job.get("source") or ""
        if path:
            open_file_location(path)

    def _delete():
        job = _selected_job()
        if not job:
            return
        if not messagebox.askyesno(t("archive_title"), t("archive_delete_confirm"), parent=dialog):
            return
        lib.delete_job(job.get("id") or "", delete_files=True)
        _load(search_var.get())
        cue_list.delete(0, tk.END)

    def _ask():
        job = _selected_job()
        if not job:
            return
        show_archive_qa_dialog(app, job)

    def _refresh():
        _load(search_var.get())
        hint_lbl.config(text=t("archive_click_hint"))
        dialog.title(t("archive_title"))
        search_btn.config(text=t("archive_search"))
        open_btn.config(text=t("archive_open_txt"))
        loc_btn.config(text=t("archive_show_folder"))
        del_btn.config(text=t("archive_delete"))
        qa_btn.config(text=t("archive_ask"))
        tree.heading("when", text=t("archive_col_when"))
        tree.heading("name", text=t("archive_col_name"))
        tree.heading("summary", text=t("archive_col_summary"))

    search_btn.config(command=lambda: _load(search_var.get()))
    search_entry.bind("<Return>", lambda e: _load(search_var.get()))
    tree.bind("<<TreeviewSelect>>", _on_select)
    cue_list.bind("<Double-Button-1>", _on_cue_activate)
    cue_list.bind("<Return>", _on_cue_activate)
    open_btn.config(command=_open_txt)
    loc_btn.config(command=_show_loc)
    del_btn.config(command=_delete)
    qa_btn.config(command=_ask)

    def _on_close():
        stop_playback()
        try:
            dialog.destroy()
        except tk.TclError:
            pass
        if getattr(app, "_archive_window", None) is dialog:
            app._archive_window = None

    dialog._wf_refresh = _refresh
    dialog.protocol("WM_DELETE_WINDOW", _on_close)
    _load()
    center_toplevel(app, dialog)
    app._archive_window = dialog
    track_i18n_window(app, dialog, _refresh)
    return dialog


def show_archive_qa_dialog(app, job: dict):
    """Ask a question about one conversation using the current AI provider."""
    dialog = tk.Toplevel(app.root)
    dialog.title(t("archive_qa_title"))
    dialog.transient(app.root)
    dialog.minsize(520, 360)
    dialog.geometry("640x420")

    frame = ttk.Frame(dialog, padding=10)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text=job.get("name") or "", font=("Segoe UI", 9, "bold")).pack(anchor="w")
    q_var = tk.StringVar()
    row = ttk.Frame(frame)
    row.pack(fill="x", pady=(8, 4))
    entry = ttk.Entry(row, textvariable=q_var)
    entry.pack(side="left", fill="x", expand=True)
    send_btn = ttk.Button(row, text=t("archive_qa_send"))
    send_btn.pack(side="left", padx=(6, 0))
    save_var = tk.BooleanVar(value=False)
    save_chk = ttk.Checkbutton(frame, text=t("archive_qa_save"), variable=save_var)
    save_chk.pack(anchor="w")
    out = tk.Text(frame, wrap="word", height=14, font=("Segoe UI", 10))
    out.pack(fill="both", expand=True, pady=(6, 0))

    def _context() -> str:
        for key in ("txt_path",):
            p = job.get(key) or ""
            if p and os.path.isfile(p):
                try:
                    with open(p, "r", encoding="utf-8", errors="replace") as f:
                        return f.read()
                except OSError:
                    pass
        extras = job.get("extra_outputs") or []
        for o in extras:
            p = (o or {}).get("path") or ""
            if p.lower().endswith(".md") and os.path.isfile(p):
                try:
                    with open(p, "r", encoding="utf-8", errors="replace") as f:
                        return f.read()
                except OSError:
                    pass
        return ""

    def _run():
        question = (q_var.get() or "").strip()
        if not question:
            return
        ctx = _context()
        if not ctx:
            messagebox.showinfo(t("archive_qa_title"), t("archive_qa_no_text"), parent=dialog)
            return
        send_btn.config(state="disabled")
        out.delete("1.0", tk.END)
        out.insert("1.0", t("archive_qa_working"))

        def worker():
            answer = ""
            err = ""
            try:
                from whisperfast.postprocess.usage import budget_exceeded
                from whisperfast.settings import load_app_settings

                if budget_exceeded(load_app_settings()):
                    from whisperfast.i18n import t as _t

                    err = _t("ai_budget_paused")
                else:
                    answer = _ask_llm(app, question, ctx)
            except Exception as e:
                err = str(e)

            def done():
                send_btn.config(state="normal")
                out.delete("1.0", tk.END)
                out.insert("1.0", err or answer)
                if answer and save_var.get():
                    _save_qa(job, question, answer, app)

            app.root.after(0, done)

        import threading

        threading.Thread(target=worker, daemon=True).start()

    send_btn.config(command=_run)
    entry.bind("<Return>", lambda e: _run())
    center_toplevel(app, dialog)
    entry.focus_set()
    return dialog


def _ask_llm(app, question: str, transcript: str) -> str:
    from whisperfast.postprocess.providers import get_provider, normalize_provider_id
    from whisperfast.postprocess.common import build_transform_user_message
    from whisperfast.postprocess.providers.claude import call_claude_messages, resolve_anthropic_api_key, resolve_claude_model
    from whisperfast.postprocess.providers.copilot import (
        call_azure_chat,
        resolve_azure_openai_api_key,
        resolve_azure_openai_api_version,
        resolve_azure_openai_deployment,
        resolve_azure_openai_endpoint,
    )
    from whisperfast.postprocess.providers.gemini import call_gemini_generate, resolve_gemini_api_key, resolve_gemini_model
    from whisperfast.postprocess.providers.ollama import call_ollama_chat, resolve_ollama_base_url, resolve_ollama_model
    from whisperfast.postprocess.providers.openai_compat import (
        call_openai_chat,
        resolve_compat_api_key,
        resolve_compat_base_url,
        resolve_compat_model,
    )

    cred = app.ai_jobs.credentials()
    pid = normalize_provider_id(app.ai_provider.get())
    prompt = (
        "Answer the user's question using ONLY the transcript below. "
        "If the transcript does not say, say you cannot tell. "
        "Reply in the language of the question.\n\n"
        f"Question: {question}\n"
    )
    user_msg = build_transform_user_message(prompt, transcript[:120000])
    provider = get_provider(pid)
    if not provider.has_api_credentials(cred) and pid not in ("ollama",):
        from whisperfast.i18n import t as _t

        raise RuntimeError(_t("ai_test_missing_key"))
    if pid == "gemini":
        return call_gemini_generate(
            resolve_gemini_api_key(cred.get("gemini_api_key") or ""),
            resolve_gemini_model(cred.get("gemini_model") or ""),
            user_msg,
        )
    if pid == "claude":
        return call_claude_messages(
            resolve_anthropic_api_key(cred.get("anthropic_api_key") or ""),
            resolve_claude_model(cred.get("claude_model") or ""),
            user_msg,
        )
    if pid == "copilot":
        return call_azure_chat(
            resolve_azure_openai_endpoint(cred.get("azure_openai_endpoint") or ""),
            resolve_azure_openai_api_key(cred.get("azure_openai_api_key") or ""),
            resolve_azure_openai_deployment(cred.get("azure_openai_deployment") or ""),
            resolve_azure_openai_api_version(cred.get("azure_openai_api_version") or ""),
            user_msg,
        )
    if pid == "openai_compat":
        return call_openai_chat(
            resolve_compat_base_url(cred.get("openai_compatible_base_url") or ""),
            resolve_compat_api_key(cred.get("openai_compatible_api_key") or ""),
            resolve_compat_model(cred.get("openai_compatible_model") or ""),
            user_msg,
        )
    if pid == "ollama":
        return call_ollama_chat(
            resolve_ollama_base_url(cred.get("ollama_base_url") or ""),
            resolve_ollama_model(cred.get("ollama_model") or ""),
            user_msg,
        )
    from whisperfast.i18n import t as _t

    raise RuntimeError(_t("archive_qa_need_api"))


def _save_qa(job, question, answer, app):
    txt = job.get("txt_path") or ""
    if not txt:
        return
    base, _ = os.path.splitext(txt)
    path = base + "_qa.md"
    resolve = getattr(app, "resolve_output_path", None)
    if callable(resolve):
        path = resolve(path) or path
    body = f"# Q&A\n\n**Q:** {question}\n\n{answer}\n"
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
    except OSError:
        return
    jid = job.get("id") or ""
    lib = getattr(app, "library", None)
    if lib and jid:
        lib.add_output(jid, "qa", path, label="qa")
    if jid:
        try:
            app.add_file_output("qa", path, label="qa", file_id=jid)
        except Exception:
            pass
