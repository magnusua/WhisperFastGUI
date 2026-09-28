"""LogStore flush scheduling and multi-channel hub."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest

from whisperfast.log_store import (
    CHANNEL_SOCIAL,
    CHANNEL_TELEGRAM,
    CHANNEL_WHISPER,
    ChannelLogHub,
    LogStore,
    bind_log_channel,
    channel_log_path,
)


class TestLogStoreFlushScheduling(unittest.TestCase):
    def test_flush_scheduler_not_called_under_lock(self):
        """Regression: Tk after under lock + flush on main thread = UI freeze."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "app_log.json")
            store = LogStore(path=path)
            held_during_schedule = []

            def scheduler(_delay):
                # If still under store._lock, non-blocking acquire fails — that
                # is the old deadlock with flush() on the UI thread.
                acquired = store._lock.acquire(blocking=False)
                held_during_schedule.append(not acquired)
                if acquired:
                    store._lock.release()

            store.set_flush_scheduler(scheduler)
            store.append_line("hello from worker")
            self.assertEqual(held_during_schedule, [False])

    def test_worker_log_does_not_deadlock_with_flush(self):
        """Worker append + concurrent flush must finish (no hang)."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "app_log.json")
            store = LogStore(path=path)
            barrier = threading.Barrier(2)
            errors = []

            entry = store.begin_file(os.path.join(tmp, "a.wav"), name="a.wav")
            fid = entry["id"]

            def flusher():
                try:
                    barrier.wait(timeout=2)
                    for _ in range(40):
                        store.flush()
                except Exception as exc:
                    errors.append(exc)

            def writer():
                try:
                    barrier.wait(timeout=2)
                    for i in range(80):
                        store.append_line(f"line {i}")
                        store.add_file_event(fid, f"event {i}")
                except Exception as exc:
                    errors.append(exc)

            t1 = threading.Thread(target=flusher)
            t2 = threading.Thread(target=writer)
            t1.start()
            t2.start()
            t1.join(timeout=5)
            t2.join(timeout=5)
            self.assertFalse(t1.is_alive(), "flush thread hung")
            self.assertFalse(t2.is_alive(), "writer thread hung")
            self.assertEqual(errors, [])
            store.flush()
            self.assertTrue(os.path.isfile(path))


class TestChannelLogHub(unittest.TestCase):
    def test_writes_separate_files_and_merges_for_ui(self):
        with tempfile.TemporaryDirectory() as tmp:
            hub = ChannelLogHub(base_dir=tmp)
            hub.append_line("tg hello", channel=CHANNEL_TELEGRAM)
            hub.append_line("yt link", channel=CHANNEL_SOCIAL)
            hub.begin_file(os.path.join(tmp, "v.mp4"), name="v.mp4", channel=CHANNEL_WHISPER)
            hub.flush()

            self.assertTrue(os.path.isfile(channel_log_path(CHANNEL_TELEGRAM, tmp)))
            self.assertTrue(os.path.isfile(channel_log_path(CHANNEL_SOCIAL, tmp)))
            self.assertTrue(os.path.isfile(channel_log_path(CHANNEL_WHISPER, tmp)))

            all_entries = hub.get_entries(hub.day_keys()[0])
            self.assertEqual(len(all_entries), 3)
            channels = {e["channel"] for e in all_entries}
            self.assertEqual(channels, {CHANNEL_TELEGRAM, CHANNEL_SOCIAL, CHANNEL_WHISPER})

            only_tg = hub.get_entries(hub.day_keys()[0], channels=[CHANNEL_TELEGRAM])
            self.assertEqual(len(only_tg), 1)
            self.assertEqual(only_tg[0]["channel"], CHANNEL_TELEGRAM)
            self.assertIn("tg hello", only_tg[0]["text"])

    def test_migrates_legacy_app_log_into_whisper(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = os.path.join(tmp, "app_log.json")
            with open(legacy, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "version": 2,
                        "days": {
                            "2026-01-01": {
                                "entries": [
                                    {
                                        "kind": "line",
                                        "id": "abc",
                                        "ts": "2026-01-01T12:00:00",
                                        "text": "legacy line\n",
                                        "tag": None,
                                    }
                                ]
                            }
                        },
                    },
                    f,
                )
            hub = ChannelLogHub(base_dir=tmp)
            entries = hub.get_entries("2026-01-01", channels=[CHANNEL_WHISPER])
            self.assertEqual(len(entries), 1)
            self.assertIn("legacy line", entries[0]["text"])
            self.assertEqual(entries[0]["channel"], CHANNEL_WHISPER)
            self.assertFalse(os.path.isfile(legacy))
            self.assertTrue(
                os.path.isfile(legacy + ".migrated")
                or os.path.isfile(channel_log_path(CHANNEL_WHISPER, tmp))
            )

    def test_clear_one_channel_keeps_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            hub = ChannelLogHub(base_dir=tmp)
            hub.append_line("tg", channel=CHANNEL_TELEGRAM)
            hub.append_line("soc", channel=CHANNEL_SOCIAL)
            hub.clear(channels=[CHANNEL_TELEGRAM])
            day = hub.day_keys()[0]
            self.assertEqual(hub.get_entries(day, channels=[CHANNEL_TELEGRAM]), [])
            left = hub.get_entries(day, channels=[CHANNEL_SOCIAL])
            self.assertEqual(len(left), 1)

    def test_bind_log_channel_passes_kwarg(self):
        seen = []

        def log(msg, tag=None, channel=None):
            seen.append((msg, tag, channel))

        wrapped = bind_log_channel(log, CHANNEL_SOCIAL)
        wrapped("hi", "link")
        self.assertEqual(seen, [("hi", "link", CHANNEL_SOCIAL)])


if __name__ == "__main__":
    unittest.main()
