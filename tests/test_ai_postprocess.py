"""whisperfast.postprocess.ai_postprocess: provider dispatch (direct, not via the UI layer)."""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from whisperfast.postprocess import ai_postprocess as aip


class _FakeProvider:
    def __init__(self, result=None, error=None):
        self.calls = []
        self._result = result if result is not None else []
        self._error = error
        self.label_key = "provider_cursor"

    def process(self, txt_path, prompts, credentials, log_func=None, on_file_created=None, resolve_output_path=None):
        self.calls.append(
            {
                "txt_path": txt_path,
                "prompts": prompts,
                "credentials": credentials,
            }
        )
        if self._error:
            raise self._error
        return self._result


class TestProcessTxtWithAi(unittest.TestCase):
    def test_no_prompts_returns_empty_without_calling_provider(self):
        fake_provider = _FakeProvider()
        with patch.object(aip, "get_provider", return_value=fake_provider):
            result = aip.process_txt_with_ai(
                "file.txt", provider_id="cursor", prompts=[], delay_s=0
            )
        self.assertEqual(result, [])
        self.assertEqual(fake_provider.calls, [])

    def test_dispatches_to_the_requested_provider_with_given_prompts(self):
        fake_provider = _FakeProvider(result=["out1.docx"])
        prompts = [(1, "Summarize", "system prompt")]
        creds = {"api_key": "abc"}
        with patch.object(aip, "get_provider", return_value=fake_provider) as m_get:
            result = aip.process_txt_with_ai(
                "file.txt",
                provider_id="GEMINI",  # normalize_provider_id should lowercase it
                credentials=creds,
                prompts=prompts,
                delay_s=0,
            )
        self.assertEqual(result, ["out1.docx"])
        m_get.assert_called_once_with("gemini")
        self.assertEqual(len(fake_provider.calls), 1)
        call = fake_provider.calls[0]
        self.assertEqual(call["txt_path"], "file.txt")
        self.assertEqual(call["prompts"], prompts)
        self.assertEqual(call["credentials"], creds)

    def test_unknown_provider_id_falls_back_to_cursor(self):
        fake_provider = _FakeProvider(result=[])
        with patch.object(aip, "get_provider", return_value=fake_provider) as m_get:
            aip.process_txt_with_ai(
                "file.txt", provider_id="not-a-real-provider", prompts=[(1, "p", "s")], delay_s=0
            )
        m_get.assert_called_once_with("cursor")

    def test_provider_returning_none_normalizes_to_empty_list(self):
        fake_provider = _FakeProvider(result=None)
        with patch.object(aip, "get_provider", return_value=fake_provider):
            result = aip.process_txt_with_ai(
                "file.txt", provider_id="cursor", prompts=[(1, "p", "s")], delay_s=0
            )
        self.assertEqual(result, [])

    def test_delay_is_awaited_before_dispatch(self):
        fake_provider = _FakeProvider(result=[])
        waited = {}
        real_wait = threading.Event.wait

        def fake_wait(self, timeout=None):
            waited["timeout"] = timeout
            return real_wait(self, 0)

        with patch.object(aip, "get_provider", return_value=fake_provider), \
                patch.object(threading.Event, "wait", fake_wait):
            aip.process_txt_with_ai(
                "file.txt", provider_id="cursor", prompts=[(1, "p", "s")], delay_s=1.5
            )
        self.assertEqual(waited.get("timeout"), 1.5)


class TestStartAiPostprocessAsync(unittest.TestCase):
    def _run_and_wait(self, **kwargs):
        done = threading.Event()
        results = {}

        def on_complete(created=None):
            results["created"] = created
            done.set()

        kwargs.setdefault("on_complete", on_complete)
        aip.start_ai_postprocess_async("file.txt", delay_s=0, **kwargs)
        self.assertTrue(done.wait(2), "async job did not complete in time")
        return results

    def test_success_calls_on_complete_with_created_files(self):
        fake_provider = _FakeProvider(result=["a.docx", "b.docx"])
        with patch.object(aip, "get_provider", return_value=fake_provider):
            results = self._run_and_wait(provider_id="cursor", prompts=[(1, "p", "s")])
        self.assertEqual(results["created"], ["a.docx", "b.docx"])

    def test_provider_exception_logs_and_completes_with_empty_list(self):
        fake_provider = _FakeProvider(error=RuntimeError("provider exploded"))
        logs = []
        with patch.object(aip, "get_provider", return_value=fake_provider):
            results = self._run_and_wait(
                provider_id="cursor", prompts=[(1, "p", "s")], log_func=logs.append
            )
        self.assertEqual(results["created"], [])
        self.assertTrue(any("provider exploded" in msg for msg in logs))

    def test_on_complete_without_arguments_is_still_called(self):
        fake_provider = _FakeProvider(result=["a.docx"])
        done = threading.Event()

        def on_complete():
            done.set()

        with patch.object(aip, "get_provider", return_value=fake_provider):
            aip.start_ai_postprocess_async(
                "file.txt",
                provider_id="cursor",
                prompts=[(1, "p", "s")],
                delay_s=0,
                on_complete=on_complete,
            )
        self.assertTrue(done.wait(2), "on_complete() fallback was not invoked")


if __name__ == "__main__":
    unittest.main()
