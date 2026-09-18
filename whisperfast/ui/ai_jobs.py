"""AI-постпроцесинг: вибір промптів (кілька вікон одночасно), API/fallback, finish hooks."""

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
from whisperfast.postprocess.providers import (
    PROVIDER_CURSOR,
    get_provider,
    normalize_provider_id,
)
from whisperfast.setup.installer import install_dependencies
from whisperfast.ui import dialogs as ui_dialogs
from whisperfast.utils import play_finish_sound

# Зсув cascade для кількох вікон «Промты», щоб не накривали одне одне
_PROMPT_CASCADE_DX = 28
_PROMPT_CASCADE_DY = 28

# Верхня межа self._jobs у довгій tray-сесії; звільняємо найстаріші
# завершені job, щойно поріг перевищено (running/selecting/open-dialog не чіпаємо).
_MAX_RETAINED_JOBS = 2000


class AiJobQueue:
    """Оркестрація AI після TXT/MD: діалог промптів на кожен файл, finish hooks."""

    def __init__(self, app):
        self.app = app
        self._lock = threading.Lock()
        self._pending = 0
        self._finish_sound_deferred = False
        self._all_complete_deferred = False
        self._jobs = {}
        # job_id з відкритим вікном «Промты» (кілька одночасно — норма)
        self._open_prompt_job_ids = set()
        # Сумісність зі старим кодом / overwrite-check
        self._prompt_dialog_job_id = None

    def edit_redactor_file(self):
        open_redactor_file(log_func=self.app.log)

    def show_prompts_overview(self):
        """Вікно зі списком промптів; галочка = за замовчуванням; Змінити відкриває JSON рядка."""
        dialog = getattr(self, "_overview_dialog", None)
        if dialog is not None:
            try:
                if dialog.winfo_exists():
                    refresh = getattr(dialog, "_wf_refresh_prompts", None)
                    if callable(refresh):
                        refresh()
                    dialog.lift()
                    dialog.focus_force()
                    return
            except tk.TclError:
                self._overview_dialog = None
        ensure_redactor_file()
        dialog = ui_dialogs.show_ai_prompts_overview_dialog(self.app)

        def _clear(event=None, dlg=dialog):
            if event is not None and event.widget is not dlg:
                return
            if getattr(self, "_overview_dialog", None) is dlg:
                self._overview_dialog = None

        dialog.bind("<Destroy>", _clear, add="+")
        self._overview_dialog = dialog
        return dialog

    def has_open_prompt_dialog(self) -> bool:
        """True, якщо хоча б одне вікно «Промты» відкрите."""
        return bool(self._open_prompt_job_ids)

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
        self._prune_old_jobs()

    def _prune_old_jobs(self):
        """Evict the oldest resolved jobs once self._jobs exceeds _MAX_RETAINED_JOBS.

        Only "done"/"skipped" jobs with no open dialog are eligible, so this
        never touches a job the user could still be actively interacting with.
        """
        excess = len(self._jobs) - _MAX_RETAINED_JOBS
        if excess <= 0:
            return
        for job_id, job in list(self._jobs.items()):
            if excess <= 0:
                break
            if job.get("status") not in ("done", "skipped"):
                continue
            if job.get("dialog") is not None or job_id in self._open_prompt_job_ids:
                continue
            del self._jobs[job_id]
            excess -= 1

    def register_job(self, txt_path, export_md_to_docx=None, log_file_id=None):
        """Створює pending AI-job без логу/діалогу. Повертає job_id."""
        app = self.app
        do_export = (
            app.export_md_to_docx.get()
            if export_md_to_docx is None
            else bool(export_md_to_docx)
        )
        txt_path = os.path.abspath(txt_path)
        file_id = log_file_id or app.find_file_log_id(txt_path)
        job_id = uuid.uuid4().hex
        self._jobs[job_id] = {
            "id": job_id,
            "txt_path": txt_path,
            "export_md_to_docx": do_export,
            "provider_id": normalize_provider_id(app.ai_provider.get()),
            "status": "pending",  # pending | selecting | skipped | running | done
            "dialog": None,
            "log_file_id": file_id,
            "prompts": None,
        }
        self._job_begin()
        return job_id

    def log_select_prompt_action(self, msg, job_id):
        """Кнопка «Обрати промти» в секції Created files (або action-рядок без file-сесії)."""
        job = self._jobs.get(job_id) or {}
        file_id = job.get("log_file_id")
        cb = lambda jid=job_id: self.open_prompt_dialog(jid)
        if file_id:
            self.app.set_file_prompt_callback(file_id, cb)
        else:
            self.app.log_action(msg, cb)

    def schedule_postprocess(
        self, txt_path, cursor_api_key="", export_md_to_docx=None, job_id=None, log_file_id=None
    ):
        """Після TXT/MD: клікабельний «Передаю в AI» + одразу вікно промптів для цього файлу."""
        del cursor_api_key  # сумісність виклику; ключі з settings / env
        app = self.app
        if job_id and job_id in self._jobs:
            job = self._jobs[job_id]
            txt_path = job["txt_path"]
            if export_md_to_docx is not None:
                job["export_md_to_docx"] = bool(export_md_to_docx)
            if log_file_id and not job.get("log_file_id"):
                job["log_file_id"] = log_file_id
        else:
            job_id = self.register_job(
                txt_path, export_md_to_docx=export_md_to_docx, log_file_id=log_file_id
            )
            job = self._jobs[job_id]
            txt_path = job["txt_path"]
        if job.get("log_file_id"):
            app.log_panel.attach_file(job["log_file_id"])
        file_name = os.path.basename(txt_path)
        self.log_select_prompt_action(t("ai_handoff", name=file_name), job_id)
        if self._maybe_autorun(job):
            return
        # Одразу вікно для цього файлу (паралельно з іншими відкритими «Промты»)
        app.root.after(0, lambda jid=job_id: self.open_prompt_dialog(jid))

    def _maybe_autorun(self, job) -> bool:
        """Run matching prompt rules without a dialog. Returns True if handled."""
        from whisperfast.postprocess.cursor_postprocess import parse_redactor_prompts
        from whisperfast.postprocess.prompt_rules import first_matching_rule, select_prompts
        from whisperfast.postprocess.usage import budget_exceeded
        from whisperfast.settings import load_app_settings

        app = self.app
        settings = load_app_settings()
        if budget_exceeded(settings):
            fid = job.get("log_file_id")
            msg = t("ai_budget_paused")
            if fid:
                app.log_file_event(msg, file_id=fid)
            else:
                app.log(msg)
            job["status"] = "skipped"
            self._job_end()
            return True
        rules = getattr(app, "ai_prompt_rules", None) or settings.get("ai_prompt_rules") or []
        source = job.get("txt_path") or ""
        watch_dirs = []
        try:
            from whisperfast.core.queue_manager import parse_watch_dirs

            watch_dirs = parse_watch_dirs(app.watch_dir.get())
        except Exception:
            pass
        rule = first_matching_rule(rules, source, watch_dirs=watch_dirs)
        if not rule or not rule.get("skip_dialog"):
            return False
        nums = rule.get("prompt_nums") or []
        if not nums:
            return False
        ensure_redactor_file()
        selected = select_prompts(parse_redactor_prompts(), nums)
        if not selected:
            return False
        job["status"] = "running"
        provider_id = job.get("provider_id") or app.ai_provider.get()
        app.root.after(
            0,
            lambda: self.start_after_prompt_choice(job, selected, provider_id),
        )
        return True

    def pump_prompt_queue(self):
        """Сумісність: відкрити вікна для всіх pending/skipped без діалогу."""
        for job_id, job in list(self._jobs.items()):
            if job.get("status") in ("pending", "skipped") and not job.get("dialog"):
                self.open_prompt_dialog(job_id)

    def _unregister_open_dialog(self, job_id):
        self._open_prompt_job_ids.discard(job_id)
        if self._prompt_dialog_job_id == job_id:
            self._prompt_dialog_job_id = next(iter(self._open_prompt_job_ids), None)

    def _cascade_offset(self) -> tuple[int, int]:
        n = len(self._open_prompt_job_ids)
        return (n * _PROMPT_CASCADE_DX, n * _PROMPT_CASCADE_DY)

    def open_prompt_dialog(self, job_id):
        """Відкриває (або піднімає) вікно вибору промптів для job. Кілька вікон — ОК."""
        app = self.app
        job = self._jobs.get(job_id)
        if not job:
            return
        if job["status"] == "running":
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
                self._unregister_open_dialog(job_id)

        # Повторний вибір після skip / після завершення AI
        if job["status"] in ("skipped", "done"):
            job["status"] = "pending"
            self._job_begin()

        ensure_redactor_file()
        prompts = parse_redactor_prompts()
        file_name = os.path.basename(job["txt_path"])
        if not prompts:
            fid = job.get("log_file_id")
            if fid:
                app.log_file_event(t("cursor_no_prompts"), file_id=fid)
            else:
                app.log(t("cursor_no_prompts"))
            job["status"] = "done"
            self._job_end()
            return

        job["status"] = "selecting"
        offset_x, offset_y = self._cascade_offset()
        self._open_prompt_job_ids.add(job_id)
        self._prompt_dialog_job_id = job_id

        def on_result(selected, provider_id):
            job["dialog"] = None
            self._unregister_open_dialog(job_id)
            provider_id = normalize_provider_id(provider_id)
            job["provider_id"] = provider_id
            app.ai_provider.set(provider_id)
            app._persist_settings()
            if selected is None:
                job["status"] = "skipped"
                fid = job.get("log_file_id")
                if fid:
                    app.log_file_event(t("ai_skipped", name=file_name), file_id=fid)
                else:
                    app.log(t("ai_skipped", name=file_name))
                self.log_select_prompt_action(t("ai_select_prompts_again"), job_id)
                self._job_end()
                return
            job["status"] = "running"
            self.start_after_prompt_choice(job, selected, provider_id)

        dialog = ui_dialogs.show_ai_prompts_dialog(
            app,
            file_name,
            prompts,
            on_result,
            provider_id=job.get("provider_id") or app.ai_provider.get(),
            cascade_offset=(offset_x, offset_y),
            default_nums=getattr(app, "ai_default_prompt_nums", None),
        )
        job["dialog"] = dialog

    def credentials(self):
        app = self.app
        return {
            "cursor_api_key": (app.cursor_api_key.get() or "").strip(),
            "gemini_api_key": (app.gemini_api_key.get() or "").strip(),
            "gemini_model": (app.gemini_model.get() or "").strip() or "gemini-2.0-flash",
            "gemini_oauth_refresh_token": (
                getattr(app, "gemini_oauth_refresh_token", None) and app.gemini_oauth_refresh_token.get() or ""
            ).strip(),
            "gemini_oauth_email": (
                getattr(app, "gemini_oauth_email", None) and app.gemini_oauth_email.get() or ""
            ).strip(),
            "google_oauth_client_id": (
                getattr(app, "google_oauth_client_id", None) and app.google_oauth_client_id.get() or ""
            ).strip()
            or str((getattr(app, "capture_cfg", None) or {}).get("google_calendar_client_id") or "").strip(),
            "google_oauth_client_secret": (
                getattr(app, "google_oauth_client_secret", None) and app.google_oauth_client_secret.get() or ""
            ).strip()
            or str((getattr(app, "capture_cfg", None) or {}).get("google_calendar_client_secret") or "").strip(),
            "google_calendar_client_id": str(
                (getattr(app, "capture_cfg", None) or {}).get("google_calendar_client_id") or ""
            ).strip(),
            "google_calendar_client_secret": str(
                (getattr(app, "capture_cfg", None) or {}).get("google_calendar_client_secret") or ""
            ).strip(),
            "google_cloud_project_id": (
                getattr(app, "google_cloud_project_id", None) and app.google_cloud_project_id.get() or ""
            ).strip(),
            "anthropic_api_key": (app.anthropic_api_key.get() or "").strip(),
            "claude_model": (app.claude_model.get() or "").strip() or "claude-sonnet-4-5",
            "azure_openai_endpoint": (app.azure_openai_endpoint.get() or "").strip(),
            "azure_openai_api_key": (app.azure_openai_api_key.get() or "").strip(),
            "azure_openai_deployment": (app.azure_openai_deployment.get() or "").strip(),
            "azure_openai_api_version": (
                (app.azure_openai_api_version.get() or "").strip()
                or "2024-08-01-preview"
            ),
            "ollama_base_url": (getattr(app, "ollama_base_url", None) and app.ollama_base_url.get() or "").strip(),
            "ollama_model": (getattr(app, "ollama_model", None) and app.ollama_model.get() or "").strip(),
            "openai_compatible_base_url": (
                getattr(app, "openai_compatible_base_url", None)
                and app.openai_compatible_base_url.get()
                or ""
            ).strip(),
            "openai_compatible_api_key": (
                getattr(app, "openai_compatible_api_key", None)
                and app.openai_compatible_api_key.get()
                or ""
            ).strip(),
            "openai_compatible_model": (
                getattr(app, "openai_compatible_model", None)
                and app.openai_compatible_model.get()
                or ""
            ).strip(),
        }

    def _clear_ai_retry(self, job):
        """Прибрати кнопку «Перезапустити завдання» для цього job."""
        fid = job.get("log_file_id")
        setter = getattr(self.app, "set_file_retry_callback", None)
        if fid and callable(setter):
            setter(fid, None)

    def _offer_ai_retry(self, job):
        """Показати кнопку повторного запуску після помилки AI (ті самі промпти)."""
        job_id = job.get("id")
        if not job_id:
            return
        cb = lambda jid=job_id: self.retry_job(jid)
        fid = job.get("log_file_id")
        setter = getattr(self.app, "set_file_retry_callback", None)
        if fid and callable(setter):
            setter(fid, cb)
        else:
            self.app.log_action(t("log_file_retry_ai_btn"), cb)

    def retry_job(self, job_id):
        """Повторити AI-завдання з уже обраними промптами (без діалогу і без паузи)."""
        job = self._jobs.get(job_id)
        if not job:
            return
        if job.get("status") == "running":
            return
        prompts = job.get("prompts")
        if not prompts:
            self.open_prompt_dialog(job_id)
            return
        if job.get("status") in ("skipped", "done"):
            self._job_begin()
        job["status"] = "running"
        self._clear_ai_retry(job)
        file_name = os.path.basename(job["txt_path"])
        fid = job.get("log_file_id")
        msg = t("ai_retrying", name=file_name)
        if fid:
            self.app.log_file_event(msg, file_id=fid)
        else:
            self.app.log(msg)
        self.start_after_prompt_choice(
            job,
            prompts,
            job.get("provider_id") or PROVIDER_CURSOR,
            delay_s=0,
        )

    def start_after_prompt_choice(self, job, prompts, provider_id, delay_s=None):
        """Після вибору промптів/провайдера → асинхронний постпроцесинг."""
        app = self.app
        txt_path = job["txt_path"]
        do_export = job["export_md_to_docx"]
        credentials = self.credentials()
        provider_id = normalize_provider_id(provider_id)
        job["provider_id"] = provider_id
        job["prompts"] = list(prompts)
        file_id = job.get("log_file_id")
        log_func = app.make_file_logger(file_id) if file_id else app.log
        used_api = get_provider(provider_id).has_api_credentials(credentials)
        expected = len(prompts)
        self._clear_ai_retry(job)

        from whisperfast.postprocess.usage import (
            apply_usage,
            budget_exceeded,
        )
        from whisperfast.settings import load_app_settings, save_app_settings

        if budget_exceeded(load_app_settings()):
            log_func(t("ai_budget_paused"))
            job["status"] = "skipped"
            self._job_end()
            return

        def on_usage(pid, prompt_tokens, completion_tokens, usd):
            data = load_app_settings()
            apply_usage(data, pid, prompt_tokens, completion_tokens, usd=usd)
            save_app_settings(
                {
                    "ai_spend_month": data.get("ai_spend_month"),
                    "ai_spend_usd": data.get("ai_spend_usd"),
                    "ai_prompt_tokens": data.get("ai_prompt_tokens"),
                    "ai_completion_tokens": data.get("ai_completion_tokens"),
                }
            )
            try:
                log_func(
                    t(
                        "ai_usage_line",
                        prompt=prompt_tokens,
                        completion=completion_tokens,
                        usd=f"{usd:.4f}",
                    )
                )
            except Exception:
                pass

        def on_created(path):
            app.queue_ctrl.register_output_paths([path])
            label = os.path.splitext(os.path.basename(path))[0]
            if "_" in label:
                label = label.rsplit("_", 1)[-1]
            if file_id:
                app.add_file_output("ai", path, label=label, file_id=file_id)
                if label.lower() in ("one_liner", "one-liner"):
                    try:
                        with open(path, "r", encoding="utf-8", errors="replace") as f:
                            summary = f.read().strip().splitlines()[0][:200]
                        lib = getattr(app, "library", None)
                        applied = False
                        setter = getattr(lib, "set_summary_if_empty", None) if lib else None
                        if callable(setter) and summary:
                            applied = bool(setter(file_id, summary))
                        elif lib and summary:
                            lib.set_meta(file_id, summary=summary)
                            applied = True
                        txt_p = job.get("txt_path") or txt_path
                        folder = os.path.dirname(txt_p) if txt_p else ""
                        if folder and applied:
                            from whisperfast.core.session_store import write_summary

                            write_summary(folder, summary)
                    except Exception:
                        pass
                if label.lower() in ("names",):
                    try:
                        with open(path, "r", encoding="utf-8", errors="replace") as f:
                            llm_text = f.read()
                        txt_p = job.get("txt_path") or txt_path
                        if txt_p and os.path.isfile(txt_p):
                            from whisperfast.core.speakers import (
                                apply_llm_names,
                                load_speakers,
                                parse_llm_speaker_names,
                                save_speakers,
                                speakers_json_path,
                            )

                            with open(txt_p, "r", encoding="utf-8", errors="replace") as tf:
                                transcript = tf.read()
                            sp_path = speakers_json_path(txt_p)
                            current = load_speakers(sp_path)
                            updated, _skipped = apply_llm_names(
                                current, parse_llm_speaker_names(llm_text), transcript
                            )
                            save_speakers(sp_path, updated)
                            if file_id:
                                app.add_file_output("speakers", sp_path, file_id=file_id)
                    except Exception:
                        pass
            if do_export and os.path.splitext(path)[1].lower() in (".md", ".markdown"):
                app._export_markdown_to_docx(path, log_file_id=file_id)

        def on_complete(created=None):
            job["status"] = "done"
            n = len(created) if created is not None else 0
            if used_api and expected and n < expected:
                self._offer_ai_retry(job)
            else:
                self._clear_ai_retry(job)
            self._job_end()

        def start():
            kwargs = {}
            if delay_s is not None:
                kwargs["delay_s"] = delay_s
            start_ai_postprocess_async(
                txt_path,
                provider_id=provider_id,
                credentials=credentials,
                log_func=log_func,
                on_file_created=on_created,
                on_complete=on_complete,
                resolve_output_path=app.resolve_output_path,
                prompts=prompts,
                on_usage=on_usage,
                **kwargs,
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
                            log_func=log_func,
                            packages_to_update=[("cursor-sdk", None, None)],
                            include_nvidia=False,
                        )
                        start()
                    except Exception:
                        on_complete()
                        raise
                threading.Thread(target=install_then, daemon=True).start()
            else:
                log_func(t("cursor_sdk_missing"))
                start()

        app.root.after(0, maybe_install_then_start)

    def start_cursor_after_prompt_choice(self, job, prompts):
        """Сумісність: Cursor за замовчуванням."""
        self.start_after_prompt_choice(
            job, prompts, job.get("provider_id") or PROVIDER_CURSOR
        )
