"""Retry Whisper/document processing after a failed log file-session."""
from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from whisperfast.core.queue_manager import QueueController
from whisperfast.i18n import t


class TestQueuePrepareRetry(unittest.TestCase):
    def test_prepare_retry_clears_error_and_processed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "clip.mka")
            open(path, "wb").close()
            ctrl = QueueController(request_queue_file=os.path.join(tmp, "q.json"))
            ctrl.queue.append(
                {
                    "path": path,
                    "note": "",
                    "start": "00:00:00",
                    "end_segment_1": "",
                    "end_segment_2": "",
                    "end": "",
                    "processed": False,
                    "error": "boom",
                }
            )
            idx = ctrl.prepare_retry(path)
            self.assertEqual(idx, 0)
            self.assertEqual(ctrl.queue[0]["error"], "")
            self.assertFalse(ctrl.queue[0]["processed"])


class TestFailedFileRetryUi(unittest.TestCase):
    def test_failed_block_shows_restart_processing_button(self):
        import tkinter as tk

        from whisperfast.ui.log_panel import LogPanel

        try:
            root = tk.Tk()
        except tk.TclError as e:
            raise unittest.SkipTest(f"no Tk display available: {e}")
        root.withdraw()
        self.addCleanup(root.destroy)
        clicked = []
        with tempfile.TemporaryDirectory() as tmp:
            panel = LogPanel(root, base_dir=tmp)
            panel.on_retry_failed_file = clicked.append
            box = tk.Text(root)
            panel.bind_widget(box)
            panel.setup_styles()
            path = os.path.join(tmp, "bad.mp3")
            open(path, "wb").close()
            file_id = panel.begin_file(path, name="bad.mp3")
            root.update()
            panel.end_file(status="failed", error="decode failed", file_id=file_id)
            root.update()
            text = box.get("1.0", "end")
            self.assertIn(t("log_file_retry_whisper_btn"), text)
            self.assertNotIn(t("log_file_retry_ai_btn"), text)

    def test_retry_failed_file_starts_single_queue_item(self):
        from whisperfast.ui import gui as gui_mod

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "talk.wav")
            open(path, "wb").close()
            started = []
            ctrl = QueueController(request_queue_file=os.path.join(tmp, "q.json"))
            ctrl.queue.append(
                {
                    "path": path,
                    "note": "",
                    "start": "00:00:00",
                    "end_segment_1": "",
                    "end_segment_2": "",
                    "end": "",
                    "processed": False,
                    "error": "gpu timeout",
                }
            )

            class Panel:
                def __init__(self):
                    self._store = SimpleNamespace(
                        get_file=lambda fid: {
                            "id": fid,
                            "source": path,
                            "name": "talk.wav",
                            "status": "failed",
                            "error": "gpu timeout",
                        }
                    )
                    self.reopened = []

                def reopen_file_for_retry(self, file_id):
                    self.reopened.append(file_id)

            app = SimpleNamespace(
                queue_ctrl=ctrl,
                queue=ctrl.queue,
                log_panel=Panel(),
                log_file_event=lambda *a, **k: None,
                log=lambda *a, **k: None,
                start_thread=lambda **kwargs: started.append(kwargs),
            )
            gui_mod.WhisperGUI.retry_failed_file(app, "file1")
            self.assertEqual(ctrl.queue[0]["error"], "")
            self.assertEqual(app.log_panel.reopened, ["file1"])
            self.assertEqual(started, [{"mode": "single", "target_idx": 0}])


if __name__ == "__main__":
    unittest.main()
