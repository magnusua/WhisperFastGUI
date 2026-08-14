"""run_queue against a TranscriptionHost fake (no Tkinter / no Whisper weights)."""
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from whisperfast.core.queue_manager import QueueController
from whisperfast.core.transcription import run_queue
from whisperfast.utils import make_queue_item


class ImmediateRoot:
    def after(self, ms, fn):
        fn()


class FakeHost:
    """Structural stand-in for WhisperGUI / TranscriptionHost."""

    def __init__(self, tmpdir):
        self.tmpdir = tmpdir
        qfile = os.path.join(tmpdir, "request_queue.json")
        self.queue_ctrl = QueueController(request_queue_file=qfile, log_func=lambda *a, **k: None)
        self.queue = self.queue_ctrl.queue
        self.cancel_requested = False
        self.root = ImmediateRoot()
        self.ai_jobs = SimpleNamespace(open_prompt_dialog=lambda *_a, **_k: None)
        self.logs = []
        self.file_events = []
        self.ended = []
        self.reset_calls = 0
        self.skipped_reports = []
        self._file_seq = 0

    def log(self, msg, tag=None):
        self.logs.append(msg)

    def begin_file_log(self, source, name=None, current=None, total=None):
        self._file_seq += 1
        return self._file_seq

    def log_file_event(self, msg, tag=None, file_id=None, callback=None):
        self.file_events.append(msg)

    def log_file_segment(self, *args, **kwargs):
        pass

    def end_file_log(self, status, error=None, file_id=None):
        self.ended.append((status, error, file_id))

    def add_file_output(self, role, path, label=None, file_id=None):
        pass

    def set_file_prompt_callback(self, file_id, callback):
        pass

    def set_file_source(self, file_id, path):
        pass

    def ask_save_mp3_confirm(self, filename):
        return False

    def save_files(self, *args, **kwargs):
        from whisperfast.core.transcription import save_files as real_save

        return real_save(self, *args, **kwargs)

    def resolve_output_path(self, path):
        return path

    def resolve_output_paths(self, paths):
        return list(paths)

    def reset_ui(self):
        self.reset_calls += 1

    def _resolve_output_dir(self, path, opts=None):
        return os.path.dirname(os.path.abspath(path))

    def _resolve_mp3_output_dir(self, path, opts=None):
        return self._resolve_output_dir(path, opts)

    def _processed_marker(self):
        return ""

    def _set_progress_value(self, value):
        pass

    def _register_ai_job(self, txt_path, export_md_to_docx=None, log_file_id=None):
        return None

    def _schedule_cursor_postprocess(self, *args, **kwargs):
        pass

    def _export_markdown_to_docx(self, md_path, log_file_id=None):
        pass

    def _maybe_log_all_complete(self, send_txt_to_cursor, will_continue):
        pass

    def _maybe_play_finish_sound(self, play_requested, send_txt_to_cursor, will_continue):
        pass

    def _report_skipped_and_offer_remove(self, skipped_paths):
        self.skipped_reports.append(list(skipped_paths))

    def _mark_done_by_path(self, path):
        self.queue_ctrl.mark_done_by_path(path)


class FakeModel:
    def __init__(self):
        self.transcribed = []

    def transcribe(self, path, language=None, vad_filter=True):
        self.transcribed.append(path)
        segs = [SimpleNamespace(start=0.0, end=1.0, text="hello")]
        return iter(segs), {}


def _touch(path, data=b"x"):
    with open(path, "wb") as f:
        f.write(data)


class TestRunQueue(unittest.TestCase):
    def setUp(self):
        self._duration = patch(
            "whisperfast.core.transcription.get_audio_duration_seconds",
            return_value=10.0,
        )
        self._duration.start()
        self.addCleanup(self._duration.stop)
        self._utils_duration = patch("whisperfast.utils.get_audio_duration_seconds", return_value=10.0)
        self._utils_duration.start()
        self.addCleanup(self._utils_duration.stop)

    def test_missing_file_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = FakeHost(tmp)
            missing = os.path.join(tmp, "gone.mp3")
            app.queue.append(make_queue_item(missing))
            model = FakeModel()
            with patch(
                "whisperfast.core.transcription.WhisperModelSingleton.get",
                return_value=model,
            ):
                run_queue(app, "all", None, options={})
            self.assertEqual(model.transcribed, [])
            self.assertEqual(app.ended[0][0], "skipped")
            self.assertEqual(app.reset_calls, 1)
            self.assertEqual(len(app.skipped_reports), 1)

    def test_document_txt_does_not_call_whisper(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = FakeHost(tmp)
            src = os.path.join(tmp, "note.txt")
            with open(src, "w", encoding="utf-8") as f:
                f.write("hello doc\n")
            app.queue.append(make_queue_item(src))
            with patch(
                "whisperfast.core.transcription.WhisperModelSingleton.get",
                side_effect=AssertionError("Whisper must not load for documents"),
            ):
                run_queue(app, "all", None, options={})
            md = os.path.join(tmp, "note.md")
            self.assertTrue(os.path.isfile(md))
            self.assertTrue(app.queue[0]["processed"])
            self.assertEqual(app.ended[-1][0], "done")
            self.assertEqual(app.reset_calls, 1)

    def test_media_writes_txt_srt_with_mocked_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = FakeHost(tmp)
            src = os.path.join(tmp, "clip.mp3")
            _touch(src)
            app.queue.append(make_queue_item(src))
            model = FakeModel()
            with patch(
                "whisperfast.core.transcription.WhisperModelSingleton.get",
                return_value=model,
            ):
                run_queue(app, "all", None, options={"save_audio_mp3": False})
            self.assertEqual(len(model.transcribed), 1)
            self.assertTrue(os.path.isfile(os.path.join(tmp, "clip.txt")))
            self.assertTrue(os.path.isfile(os.path.join(tmp, "clip.srt")))
            with open(os.path.join(tmp, "clip.txt"), encoding="utf-8") as f:
                self.assertIn("hello", f.read())
            self.assertTrue(app.queue[0]["processed"])
            self.assertEqual(app.ended[-1][0], "done")

    def test_cancel_skips_processing(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = FakeHost(tmp)
            src = os.path.join(tmp, "clip.mp3")
            _touch(src)
            app.queue.append(make_queue_item(src))
            app.cancel_requested = True
            model = FakeModel()
            with patch(
                "whisperfast.core.transcription.WhisperModelSingleton.get",
                return_value=model,
            ):
                run_queue(app, "all", None, options={})
            self.assertEqual(model.transcribed, [])
            self.assertFalse(app.queue[0]["processed"])
            self.assertEqual(app.reset_calls, 1)

    def test_only_new_skips_already_processed(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = FakeHost(tmp)
            a = os.path.join(tmp, "a.mp3")
            b = os.path.join(tmp, "b.mp3")
            _touch(a)
            _touch(b)
            app.queue.append(make_queue_item(a, processed=True))
            app.queue.append(make_queue_item(b))
            model = FakeModel()
            with patch(
                "whisperfast.core.transcription.WhisperModelSingleton.get",
                return_value=model,
            ):
                run_queue(app, "only_new", None, options={"save_audio_mp3": False})
            self.assertEqual(len(model.transcribed), 1)
            self.assertEqual(
                os.path.normcase(model.transcribed[0]),
                os.path.normcase(b),
            )
            self.assertTrue(app.queue[1]["processed"])

            model.transcribed.clear()
            app.queue[0]["processed"] = True
            app.queue[1]["processed"] = False
            with patch(
                "whisperfast.core.transcription.WhisperModelSingleton.get",
                return_value=model,
            ):
                run_queue(app, "all", None, options={"save_audio_mp3": False})
            self.assertEqual(len(model.transcribed), 2)


if __name__ == "__main__":
    unittest.main()
