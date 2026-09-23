"""AI job retry after Cursor/provider failure."""
from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from whisperfast.ui.ai_jobs import AiJobQueue


class ImmediateRoot:
    def after(self, ms, fn):
        fn()


class FakeApp:
    def __init__(self, api_key="test-key"):
        self.root = ImmediateRoot()
        self.ai_provider = SimpleNamespace(get=lambda: "cursor", set=lambda _v: None)
        self.export_md_to_docx = SimpleNamespace(get=lambda: False)
        self.cursor_api_key = SimpleNamespace(get=lambda: api_key)
        self.gemini_api_key = SimpleNamespace(get=lambda: "")
        self.gemini_model = SimpleNamespace(get=lambda: "")
        self.anthropic_api_key = SimpleNamespace(get=lambda: "")
        self.claude_model = SimpleNamespace(get=lambda: "")
        self.azure_openai_endpoint = SimpleNamespace(get=lambda: "")
        self.azure_openai_api_key = SimpleNamespace(get=lambda: "")
        self.azure_openai_deployment = SimpleNamespace(get=lambda: "")
        self.azure_openai_api_version = SimpleNamespace(get=lambda: "")
        self.queue = []
        self.queue_ctrl = SimpleNamespace(
            register_output_paths=lambda _p: None,
            watch_pending_continue=False,
        )
        self.log_panel = SimpleNamespace(attach_file=lambda *_a, **_k: None)
        self.retry_cbs = {}
        self.file_events = []
        self.logs = []
        self.actions = []

    def find_file_log_id(self, path):
        return "file1"

    def make_file_logger(self, file_id):
        return lambda *_a, **_k: None

    def set_file_prompt_callback(self, file_id, callback):
        pass

    def set_file_retry_callback(self, file_id, callback):
        if callback is None:
            self.retry_cbs.pop(file_id, None)
        else:
            self.retry_cbs[file_id] = callback

    def log_action(self, msg, callback):
        self.actions.append((msg, callback))

    def log_file_event(self, msg, tag=None, file_id=None, callback=None):
        self.file_events.append(msg)

    def log(self, msg, tag=None):
        self.logs.append(msg)

    def add_file_output(self, role, path, label=None, file_id=None):
        pass

    def resolve_output_path(self, path):
        return path

    def _persist_settings(self):
        pass

    def _export_markdown_to_docx(self, *args, **kwargs):
        pass


class TestAiJobRetry(unittest.TestCase):
    def _run_job(self, app, created, prompts=None):
        prompts = prompts or [(1, "redactor", "clean up")]
        jobs = AiJobQueue(app)
        job_id = jobs.register_job(os.path.join(tempfile.gettempdir(), "a.txt"), log_file_id="file1")
        job = jobs._jobs[job_id]

        def fake_start(*_a, **kwargs):
            kwargs["on_complete"](created)

        with patch("whisperfast.ui.ai_jobs.start_ai_postprocess_async", side_effect=fake_start):
            with patch.dict("sys.modules", {"cursor_sdk": SimpleNamespace()}):
                jobs.start_after_prompt_choice(job, prompts, "cursor", delay_s=0)
        return jobs, job_id

    def test_offers_retry_when_api_chain_creates_nothing(self):
        app = FakeApp(api_key="test-key")
        jobs, job_id = self._run_job(app, created=[])
        self.assertIn("file1", app.retry_cbs)
        self.assertEqual(jobs._jobs[job_id]["status"], "done")

    def test_no_retry_when_all_prompts_succeed(self):
        app = FakeApp(api_key="test-key")
        jobs, _job_id = self._run_job(app, created=["out.md"])
        self.assertNotIn("file1", app.retry_cbs)
        self.assertEqual(jobs._jobs[_job_id]["status"], "done")

    def test_no_retry_without_api_key(self):
        app = FakeApp(api_key="")
        self._run_job(app, created=[])
        self.assertNotIn("file1", app.retry_cbs)

    def test_offers_retry_when_chain_stops_midway(self):
        app = FakeApp(api_key="test-key")
        prompts = [(1, "redactor", "clean"), (2, "summary", "sum")]
        self._run_job(app, created=["out.md"], prompts=prompts)
        self.assertIn("file1", app.retry_cbs)

    def test_retry_reruns_same_prompts_without_delay(self):
        app = FakeApp(api_key="test-key")
        jobs, job_id = self._run_job(app, created=[])
        calls = []

        def fake_start(*_a, **kwargs):
            calls.append(kwargs)
            kwargs["on_complete"]([])

        with patch("whisperfast.ui.ai_jobs.start_ai_postprocess_async", side_effect=fake_start):
            with patch.dict("sys.modules", {"cursor_sdk": SimpleNamespace()}):
                jobs.retry_job(job_id)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["delay_s"], 0)
        self.assertEqual(calls[0]["prompts"], [(1, "redactor", "clean up")])
        self.assertTrue(any("a.txt" in str(m) for m in app.file_events))


class TestAutoProcess(unittest.TestCase):
    def _job(self, auto, rules=None, nums=None):
        app = FakeApp()
        app.ai_prompt_rules = [] if rules is None else rules
        app.ai_default_prompt_nums = [1, 9] if nums is None else nums
        app.ai_auto_process = SimpleNamespace(get=lambda: auto)
        jobs = AiJobQueue(app)
        job_id = jobs.register_job(os.path.join(tempfile.gettempdir(), "a.txt"), log_file_id="file1")
        return jobs, jobs._jobs[job_id]

    def test_checked_prompts_run_without_a_window(self):
        jobs, job = self._job(True)
        seen = []
        jobs.start_after_prompt_choice = lambda _job, selected, _provider: seen.append(
            [item[0] for item in selected]
        )
        prompts = [(1, "a", "a"), (2, "b", "b"), (9, "c", "c")]
        with patch("whisperfast.settings.load_app_settings", return_value={}):
            with patch("whisperfast.postprocess.usage.budget_exceeded", return_value=False):
                with patch(
                    "whisperfast.postprocess.cursor_postprocess.parse_redactor_prompts",
                    return_value=prompts,
                ):
                    with patch("whisperfast.ui.ai_jobs.ensure_redactor_file"):
                        handled = jobs._maybe_autorun(job)
        self.assertTrue(handled)
        self.assertEqual(seen, [[1, 9]])
        self.assertEqual(job["status"], "running")

    def test_without_the_check_the_window_stays(self):
        jobs, job = self._job(False)
        with patch("whisperfast.settings.load_app_settings", return_value={}):
            with patch("whisperfast.postprocess.usage.budget_exceeded", return_value=False):
                handled = jobs._maybe_autorun(job)
        self.assertFalse(handled)
        self.assertEqual(job["status"], "pending")


class TestApplyOneLinerBrief(unittest.TestCase):
    def test_fills_library_log_and_queue(self):
        from whisperfast.library import ConversationLibrary
        from whisperfast.ui.ai_jobs import apply_one_liner_brief
        from whisperfast.utils import make_queue_item

        with tempfile.TemporaryDirectory() as tmp:
            with patch("whisperfast.utils.get_audio_duration_seconds", return_value=1.0):
                media = os.path.join(tmp, "2026-09-18 16-29-13.mp4")
                txt = os.path.join(tmp, "2026-09-18 16-29-13.txt")
                with open(media, "wb") as f:
                    f.write(b"x")
                with open(txt, "w", encoding="utf-8") as f:
                    f.write("hi")
                lib = ConversationLibrary(os.path.join(tmp, "library.sqlite"))
                try:
                    lib.upsert_job(
                        {
                            "id": "file1",
                            "created_at": "2026-09-18T16:29:13",
                            "source": media,
                            "name": os.path.basename(media),
                            "txt_path": txt,
                        }
                    )
                    notes = []
                    app = FakeApp()
                    app.library = lib
                    app.apply_file_note = lambda fid, note, log_event=True: notes.append(
                        (fid, note, log_event)
                    )
                    app.queue_ctrl = SimpleNamespace(
                        register_output_paths=lambda _p: None,
                        watch_pending_continue=False,
                        queue=[make_queue_item(media)],
                    )

                    def set_note_if_empty_for_path(path, note):
                        app.queue_ctrl.queue[0]["note"] = note
                        return True

                    app.queue_ctrl.set_note_if_empty_for_path = set_note_if_empty_for_path
                    self.assertTrue(
                        apply_one_liner_brief(app, "file1", "  sales call  ", source_path=txt)
                    )
                    self.assertEqual(lib.get_job("file1")["summary"], "sales call")
                    self.assertEqual(notes, [("file1", "sales call", False)])
                    self.assertEqual(app.queue_ctrl.queue[0]["note"], "sales call")
                    self.assertFalse(apply_one_liner_brief(app, "file1", "other", source_path=txt))
                    self.assertEqual(lib.get_job("file1")["summary"], "sales call")
                finally:
                    lib.close()


class TestPromptInputResolve(unittest.TestCase):
    def test_outputs_prefer_txt_then_sibling(self):
        from whisperfast.ui.ai_jobs import prompt_input_from_outputs, prompt_input_from_source

        with tempfile.TemporaryDirectory() as tmp:
            txt = os.path.join(tmp, "a.txt")
            md = os.path.join(tmp, "a.md")
            with open(txt, "w", encoding="utf-8") as f:
                f.write("t")
            with open(md, "w", encoding="utf-8") as f:
                f.write("m")
            found = prompt_input_from_outputs(
                [{"role": "md", "path": md}, {"role": "txt", "path": txt}],
                require_file=True,
            )
            self.assertEqual(os.path.normcase(found), os.path.normcase(txt))
            media = os.path.join(tmp, "a.mp4")
            with open(media, "wb") as f:
                f.write(b"x")
            self.assertEqual(
                os.path.normcase(prompt_input_from_source(media, require_file=True)),
                os.path.normcase(txt),
            )
            self.assertEqual(prompt_input_from_source(os.path.join(tmp, "none.mp4")), "")


class TestStartPromptsForPath(unittest.TestCase):
    def test_reuses_existing_job(self):
        app = FakeApp()
        q = AiJobQueue(app)
        with tempfile.TemporaryDirectory() as tmp:
            txt = os.path.join(tmp, "talk.txt")
            with open(txt, "w", encoding="utf-8") as f:
                f.write("hi")
            job_id = q.register_job(txt, log_file_id="file1")
            q._jobs[job_id]["status"] = "skipped"
            opened = []
            q.open_prompt_dialog = lambda jid: opened.append(jid)
            self.assertTrue(q.start_prompts_for_path(txt))
            self.assertEqual(opened, [job_id])
            self.assertEqual(len(q._jobs), 1)

    def test_missing_transcript_returns_false(self):
        app = FakeApp()
        q = AiJobQueue(app)
        self.assertFalse(q.start_prompts_for_path(os.path.join("no", "such.mp4")))


if __name__ == "__main__":
    unittest.main()
