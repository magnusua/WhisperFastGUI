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


if __name__ == "__main__":
    unittest.main()
