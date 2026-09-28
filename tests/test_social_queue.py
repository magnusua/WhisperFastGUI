"""Social download queue is independent of Whisper and AI."""
from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from whisperfast.telegram import social_queue as sq


class TestSocialQueueIsolation(unittest.TestCase):
    def setUp(self):
        self.q = sq.reset_social_queue_for_tests()

    def test_process_returns_file_to_chat_without_whisper(self):
        replies = []
        files = []
        whisper = []
        video = os.path.join(tempfile.gettempdir(), "soc.mp4")
        with open(video, "w", encoding="utf-8") as handle:
            handle.write("x")

        with patch("whisperfast.telegram.seen.classify", return_value=("download", None)), patch(
            "whisperfast.telegram.links.claim_link", return_value=("new", video, None)
        ), patch("whisperfast.settings.load_app_settings", return_value={"telegram_social_to_queue": False}), patch(
            "whisperfast.telegram.links.mark_own_upload"
        ), patch("whisperfast.telegram.links.remember_link"), patch(
            "whisperfast.telegram.links.log_downloaded_file"
        ), patch("whisperfast.telegram.worker.resolve_work_dir", return_value=tempfile.gettempdir()):
            sq.process_social_urls(
                ["https://youtu.be/abc"],
                chat_id=1,
                message_id=2,
                settings={},
                log=lambda *_a, **_k: None,
                reply=replies.append,
                send_files=lambda items: files.extend(items),
                submit_whisper=lambda *a: whisper.append(a),
            )
        self.assertTrue(any("скач" in r.casefold() or "download" in r.casefold() or "завант" in r.casefold() for r in replies) or replies)
        self.assertEqual(files[0]["path"], video)
        self.assertEqual(whisper, [])

    def test_whisper_submit_is_fire_and_forget(self):
        replies = []
        whisper = []
        video = os.path.join(tempfile.gettempdir(), "soc2.mp4")
        with open(video, "w", encoding="utf-8") as handle:
            handle.write("x")

        def submit(path, cid, mid):
            whisper.append((path, cid, mid))
            # Simulate Whisper being busy/slow — social must still finish.
            time.sleep(0.05)

        with patch("whisperfast.telegram.seen.classify", return_value=("ready", None)), patch(
            "whisperfast.telegram.links.claim_link", return_value=("new", video, None)
        ), patch("whisperfast.settings.load_app_settings", return_value={"telegram_social_to_queue": True}), patch(
            "whisperfast.telegram.links.remember_link"
        ), patch("whisperfast.telegram.links.log_downloaded_file"), patch(
            "whisperfast.telegram.worker.resolve_work_dir", return_value=tempfile.gettempdir()
        ):
            t0 = time.monotonic()
            sq.process_social_urls(
                ["https://youtu.be/xyz"],
                chat_id=7,
                message_id=9,
                settings={},
                log=lambda *_a, **_k: None,
                reply=replies.append,
                send_files=lambda _items: None,
                submit_whisper=submit,
            )
            elapsed = time.monotonic() - t0
        self.assertEqual(whisper, [(video, 7, 9)])
        self.assertLess(elapsed, 1.0)
        self.assertTrue(replies)

    def test_serial_worker_runs_one_job_at_a_time(self):
        q = SocialSerialProbe()
        active = []
        peaks = []
        gate = threading.Event()
        started = threading.Event()

        def job1():
            active.append(1)
            peaks.append(len(active))
            started.set()
            gate.wait(timeout=2)
            active.pop()

        def job2():
            active.append(1)
            peaks.append(len(active))
            active.pop()

        q._social_inline = False
        q.submit(job1)
        self.assertTrue(started.wait(timeout=2))
        q.submit(job2)
        time.sleep(0.1)
        self.assertEqual(max(peaks), 1)
        gate.set()
        self.assertTrue(q.wait_idle(timeout=3))


class SocialSerialProbe(sq.SocialJobQueue):
    pass


if __name__ == "__main__":
    unittest.main()
