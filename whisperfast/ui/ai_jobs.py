"""Черга AI-постпроцесингу: вибір промптів, провайдер, відкладений звук/лог."""

from __future__ import annotations

import os
import threading
import uuid
import tkinter as tk
from tkinter import messagebox

from whisperfast.i18n import t
from whisperfast.postprocess.ai_postprocess import start_ai_postprocess_async
from whisperfast.postprocess.cursor_postprocess import (
    ensure_redactor_file,
    open_redactor_file,
    parse_redactor_prompts,
)
from whisperfast.postprocess.providers import PROVIDER_CURSOR, normalize_provider_id
from whisperfast.setup.installer import install_dependencies
from whisperfast.ui import dialogs as ui_dialogs
from whisperfast.utils import play_finish_sound


class AiJobQueue:
    """Оркестрація AI після TXT/MD: діалог промптів, API/fallback, finish hooks."""

    def __init__(self, app):
        self.app = app
        self._lock = threading.Lock()
        self._pending = 0
        self._finish_sound_deferred = False
        self._all_complete_deferred = False
        self._jobs = {}
        self._prompt_queue = []
        self._prompt_dialog_job_id = None

    def edit_redactor_file(self):
        open_redactor_file(log_func=self.app.log)

    def maybe_log_all_complete(self, send_txt_to_ai, will_continue):
        """Лог «всі завдання виконано» лише після черги і (за потреби) після AI."""
        if will_continue:
            return
        if send_txt_to_ai:
            with self._lock:
                if self._pending > 0:
                    self._all_complete_deferred = True
                    return
        self.app.log(f"\n{t('all_tasks_complete')}")

    def maybe_play_finish_sound(self, play_requested, send_txt_to_ai, will_continue):
        """Звук лише після всієї транскрибації і (якщо увімкнено) усієї AI-постобробки."""
        if not play_requested or will_continue:
            return
        if send_txt_to_ai:
            with self._lock:
                if self._pending > 0:
                    self._finish_sound_deferred = True
                    return
        play_finish_sound()

    def _job_begin(self):
        with self._lock:
            self._pending += 1

    def _job_end(self):
        play_now = False
        log_complete = False
        with self._lock:
            self._pending = max(0, self._pending - 1)
            if self._pending == 0:
                if self._all_complete_deferred:
                    self._all_complete_deferred = False
                    log_complete = True
                if self._finish_sound_deferred:
                    self._finish_sound_deferred = False
                    play_now = True
        if log_complete:
            self.app.log(f"\n{t('all_tasks_complete')}")
        if play_now:
            will_continue = (
                self.app.queue_ctrl.watch_pending_continue
                and any(not q.get("processed") for q in self.app.queue)
            )
            if not will_continue:
                play_finish_sound()

    def schedule_postprocess(self, txt_path, cursor_api_key="", export_md_to_docx=None):
        """Після TXT/MD: клікабельний «Передаю в AI» + вікно вибору промптів/провайдера."""
        del cursor_api_key  # сумісність виклику; ключі з settings / env
        app = self.app
        do_export = (
            app.export_md_to_docx.get()
            if export_md_to_docx is None
            else bool(export_md_to_docx)
        )
        txt_path = os.path.abspath(txt_path)
        file_name = os.path.basename(txt_path)
        job_id = uuid.uuid4().hex
        job = {
            "id": job_id,
            "txt_path": txt_path,
            "export_md_to_docx": do_export,
            "provider_id": normalize_provider_id(app.ai_provider.get()),
            "status": "pending",  # pending | selecting | skipped | running | done
            "dialog": None,
        }
        self._jobs[job_id] = job
        self._job_begin()
        app.log_action(
            t("ai_handoff", name=file_name),
            lambda jid=job_id: self.open_prompt_dialog(jid),
        )
        self._prompt_queue.append(job_id)
        app.root.after(0, self.pump_prompt_queue)

    def pump_prompt_queue(self):
        """Показує наступне вікно вибору промптів, якщо немає відкритого."""
        if self._prompt_dialog_job_id is not None:
            return
        while self._prompt_queue:
            job_id = self._prompt_queue.pop(0)
            job = self._jobs.get(job_id)
            if not job or job["status"] not in ("pending", "skipped"):
                continue
            self.open_prompt_dialog(job_id)
            return

    def open_prompt_dialog(self, job_id):
        """Відкриває (або піднімає) вікно вибору промптів для job."""
        app = self.app
        job = self._jobs.get(job_id)
        if not job:
            return
        if job["status"] in ("running", "done"):
            return

        dialog = job.get("dialog")
        if dialog is not None:
            try:
                if dialog.winfo_exists():
                    dialog.lift()
                    dialog.focus_force()
                    return
            except tk.TclError:
                job["dialog"] = None

        if (
            self._prompt_dialog_job_id is not None
            and self._prompt_dialog_job_id != job_id
        ):
            if job_id not in self._prompt_queue and job["status"] in (
                "pending",
                "skipped",
            ):
                self._prompt_queue.append(job_id)
            other = self._jobs.get(self._prompt_dialog_job_id)
            if other and other.get("dialog"):
                try:
                    if other["dialog"].winfo_exists():
                        other["dialog"].lift()
                        other["dialog"].focus_force()
                except tk.TclError:
                    pass
            return

        if job["status"] == "skipped":
            job["status"] = "pending"
            self._job_begin()

        ensure_redactor_file()
        prompts = parse_redactor_prompts()
        file_name = os.path.basename(job["txt_path"])
        if not prompts:
            app.log(t("cursor_no_prompts"))
            job["status"] = "done"
            self._job_end()
            app.root.after(0, self.pump_prompt_queue)
            return

        job["status"] = "selecting"
        self._prompt_dialog_job_id = job_id

        def on_result(selected, provider_id):
            job["dialog"] = None
            self._prompt_dialog_job_id = None
            provider_id = normalize_provider_id(provider_id)
            job["provider_id"] = provider_id
            app.ai_provider.set(provider_id)
            app._persist_settings()
            if selected is None:
                job["status"] = "skipped"
                app.log(t("ai_skipped", name=file_name))
                app.log_action(
                    t("ai_select_prompts_again"),
                    lambda jid=job_id: self.open_prompt_dialog(jid),
                )
                self._job_end()
                app.root.after(0, self.pump_prompt_queue)
                return
            job["status"] = "running"
            self.start_after_prompt_choice(job, selected, provider_id)
            app.root.after(0, self.pump_prompt_queue)

        dialog = ui_dialogs.show_ai_prompts_dialog(
            app,
            file_name,
            prompts,
            on_result,
            provider_id=job.get("provider_id") or app.ai_provider.get(),
        )
        job["dialog"] = dialog

    def credentials(self):
        app = self.app
        return {
            "cursor_api_key": (app.cursor_api_key.get() or "").strip(),
            "gemini_api_key": (app.gemini_api_key.get() or "").strip(),
            "gemini_model": (app.gemini_model.get() or "").strip() or "gemini-2.0-flash",
            "azure_openai_endpoint": (app.azure_openai_endpoint.get() or "").strip(),
            "azure_openai_api_key": (app.azure_openai_api_key.get() or "").strip(),
            "azure_openai_deployment": (app.azure_openai_deployment.get() or "").strip(),
            "azure_openai_api_version": (
                (app.azure_openai_api_version.get() or "").strip()
                or "2024-08-01-preview"
            ),
        }

    def start_after_prompt_choice(self, job, prompts, provider_id):
        """Після вибору промптів/провайдера → асинхронний постпроцесинг."""
        app = self.app
        txt_path = job["txt_path"]
        do_export = job["export_md_to_docx"]
        credentials = self.credentials()
        provider_id = normalize_provider_id(provider_id)

        def on_created(path):
            app.queue_ctrl.register_output_paths([path])
            if do_export and os.path.splitext(path)[1].lower() in (".md", ".markdown"):
                app._export_markdown_to_docx(path)

        def on_complete():
            job["status"] = "done"
            self._job_end()

        def start():
            start_ai_postprocess_async(
                txt_path,
                provider_id=provider_id,
                credentials=credentials,
                log_func=app.log,
                on_file_created=on_created,
                on_complete=on_complete,
                resolve_output_path=app.resolve_output_path,
                prompts=prompts,
            )

        def maybe_install_then_start():
            if provider_id != PROVIDER_CURSOR:
                start()
                return
            if not credentials.get("cursor_api_key"):
                start()
                return
            try:
                import cursor_sdk  # noqa: F401
                start()
                return
            except ImportError:
                pass
            if messagebox.askyesno(t("installation"), t("cursor_sdk_install_prompt")):
                def install_then():
                    try:
                        install_dependencies(
                            log_func=app.log,
                            packages_to_update=[("cursor-sdk", None, None)],
                            include_nvidia=False,
                        )
                        start()
                    except Exception:
                        on_complete()
                        raise
                threading.Thread(target=install_then, daemon=True).start()
            else:
                app.log(t("cursor_sdk_missing"))
                start()

        app.root.after(0, maybe_install_then_start)

    def start_cursor_after_prompt_choice(self, job, prompts):
        """Сумісність: Cursor за замовчуванням."""
        self.start_after_prompt_choice(
            job, prompts, job.get("provider_id") or PROVIDER_CURSOR
        )
