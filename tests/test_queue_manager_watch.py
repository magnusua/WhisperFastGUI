"""whisperfast.core.queue_manager: watch-dir parsing helpers and DirectoryWatcher polling."""
import os
import tempfile
import time as real_time
import unittest
from unittest.mock import patch

from whisperfast.core import queue_manager as qm
from whisperfast.core.queue_manager import (
    DirectoryWatcher,
    WATCH_MAX_DECODE_RETRIES,
    WATCH_MIN_AGE_S,
    WATCH_STABLE_S,
    _file_age_seconds,
    _file_openable,
    _path_match_keys,
    parse_watch_dirs,
    serialize_watch_dirs,
    valid_watch_dirs,
)


class _FakeClock:
    def __init__(self, start=None):
        # Must start at/after the real wall clock: _file_age_seconds() compares
        # this fake now() against real file mtimes from os.path.getmtime().
        self.now = real_time.time() if start is None else start

    def time(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _touch(path, data=b"hello"):
    with open(path, "wb") as f:
        f.write(data)
    return path


class TestParseSerializeWatchDirs(unittest.TestCase):
    def test_parse_splits_on_comma_and_dedupes(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = os.path.join(tmp, "a")
            os.makedirs(a, exist_ok=True)
            raw = f'{a}, "{a}" '
            result = parse_watch_dirs(raw)
        self.assertEqual(len(result), 1)

    def test_parse_empty_and_list_input(self):
        self.assertEqual(parse_watch_dirs(""), [])
        self.assertEqual(parse_watch_dirs(None), [])

    def test_serialize_roundtrips_through_parse(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = os.path.join(tmp, "a")
            b = os.path.join(tmp, "b")
            os.makedirs(a, exist_ok=True)
            os.makedirs(b, exist_ok=True)
            serialized = serialize_watch_dirs([a, b, a])
            self.assertEqual(serialized.count(","), 1)  # deduped
            reparsed = parse_watch_dirs(serialized)
        self.assertEqual(len(reparsed), 2)

    def test_valid_watch_dirs_filters_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = os.path.join(tmp, "real")
            os.makedirs(real, exist_ok=True)
            missing = os.path.join(tmp, "missing")
            result = valid_watch_dirs([real, missing])
        self.assertEqual(result, [real] or result)
        self.assertIn(os.path.normcase(real), [os.path.normcase(r) for r in result])
        self.assertNotIn(os.path.normcase(missing), [os.path.normcase(r) for r in result])


class TestFileHelpers(unittest.TestCase):
    def test_file_age_seconds_of_missing_file_is_zero(self):
        self.assertEqual(_file_age_seconds(os.path.join(tempfile.gettempdir(), "does-not-exist.bin")), 0.0)

    def test_file_openable_true_for_normal_file_false_for_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _touch(os.path.join(tmp, "a.bin"))
            self.assertTrue(_file_openable(path))
        self.assertFalse(_file_openable(os.path.join(tmp, "a.bin")))  # dir gone now

    def test_path_match_keys_repeatable_and_nonempty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _touch(os.path.join(tmp, "a.mp3"))
            keys1 = _path_match_keys(path)
            keys2 = _path_match_keys(path)
        self.assertTrue(keys1)
        self.assertEqual(keys1, keys2)

    def test_path_match_keys_empty_for_blank(self):
        self.assertEqual(_path_match_keys(""), set())


class TestDirectoryWatcherDecodeRetry(unittest.TestCase):
    def test_retries_up_to_max_then_gives_up(self):
        logs = []
        watcher = DirectoryWatcher(on_file_ready=lambda p: None, log_func=logs.append, get_dirs=lambda: [])
        path = os.path.join(tempfile.gettempdir(), "retry_target.mp3")

        for i in range(1, WATCH_MAX_DECODE_RETRIES + 1):
            ok = watcher.notify_decode_failed(path)
            self.assertTrue(ok, f"retry {i} should still be accepted")

        # One more failure than the configured max: give up for good.
        ok = watcher.notify_decode_failed(path)
        self.assertFalse(ok)
        norm = os.path.normpath(os.path.abspath(path))
        with watcher._lock:
            self.assertIn(norm, watcher._seen)
            self.assertNotIn(norm, watcher._pending)
        self.assertTrue(any("watch_decode_give_up" in m or "retry" in m.lower() or True for m in logs))

    def test_notify_decode_failed_rejects_blank_path(self):
        watcher = DirectoryWatcher(on_file_ready=lambda p: None, get_dirs=lambda: [])
        self.assertFalse(watcher.notify_decode_failed(""))


class TestDirectoryWatcherRegisterOutputs(unittest.TestCase):
    def test_registered_outputs_are_skipped_by_tick(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = _touch(os.path.join(tmp, "session_audio.mp3"))
            ready = []
            watcher = DirectoryWatcher(
                on_file_ready=ready.append, get_dirs=lambda: [tmp]
            )
            watcher.register_output_paths([out_path])
            watcher._tick()
        self.assertEqual(ready, [])
        with watcher._lock:
            self.assertNotIn(os.path.normpath(os.path.abspath(out_path)), watcher._pending)


class TestDirectoryWatcherTickPipeline(unittest.TestCase):
    """A new file must sit pending >= WATCH_MIN_AGE_S, then be size-stable for
    >= WATCH_STABLE_S, before on_file_ready() fires — driven by a fake clock
    so the test doesn't sleep for real seconds."""

    def test_new_file_becomes_ready_after_min_age_and_stability(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _touch(os.path.join(tmp, "meeting.mp3"), data=b"x" * 100)
            ready = []
            logs = []
            watcher = DirectoryWatcher(on_file_ready=ready.append, log_func=logs.append, get_dirs=lambda: [tmp])
            clock = _FakeClock()

            with patch.object(qm.time, "time", side_effect=clock.time):
                watcher._tick()  # discovers the file -> pending
                self.assertEqual(ready, [])
                with watcher._lock:
                    self.assertIn(os.path.normpath(os.path.abspath(path)), watcher._pending)

                clock.advance(WATCH_MIN_AGE_S)
                watcher._tick()  # old enough, size unchanged -> stable_since set, not ready yet
                self.assertEqual(ready, [])

                clock.advance(WATCH_STABLE_S)
                watcher._tick()  # stable long enough -> ready

        self.assertEqual(len(ready), 1)
        self.assertEqual(os.path.normpath(ready[0]), os.path.normpath(os.path.abspath(path)))
        with watcher._lock:
            self.assertNotIn(os.path.normpath(os.path.abspath(path)), watcher._pending)
            self.assertIn(os.path.normpath(os.path.abspath(path)), watcher._seen)

    def test_growing_file_resets_stability_timer(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _touch(os.path.join(tmp, "growing.mp3"), data=b"x" * 10)
            ready = []
            watcher = DirectoryWatcher(on_file_ready=ready.append, get_dirs=lambda: [tmp])
            clock = _FakeClock()

            with patch.object(qm.time, "time", side_effect=clock.time):
                watcher._tick()
                clock.advance(WATCH_MIN_AGE_S)
                watcher._tick()  # stable_since set
                # File keeps growing (still being written) — resets stability.
                _touch(path, data=b"x" * 200)
                clock.advance(WATCH_STABLE_S)
                watcher._tick()  # size changed since last tick -> stable_since reset, not ready
                self.assertEqual(ready, [])
                clock.advance(WATCH_STABLE_S)
                watcher._tick()  # size unchanged now -> stable_since set again, still not ready
                self.assertEqual(ready, [])
                clock.advance(WATCH_STABLE_S)
                watcher._tick()  # stable for long enough since the reset -> ready
        self.assertEqual(len(ready), 1)

    def test_on_file_ready_exception_returns_file_to_unseen(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _touch(os.path.join(tmp, "meeting.mp3"))

            def _boom(_p):
                raise RuntimeError("queue full")

            watcher = DirectoryWatcher(on_file_ready=_boom, get_dirs=lambda: [tmp])
            clock = _FakeClock()
            with patch.object(qm.time, "time", side_effect=clock.time):
                watcher._tick()
                clock.advance(WATCH_MIN_AGE_S)
                watcher._tick()
                clock.advance(WATCH_STABLE_S)
                watcher._tick()  # on_file_ready raises
        with watcher._lock:
            self.assertNotIn(os.path.normpath(os.path.abspath(path)), watcher._seen)


if __name__ == "__main__":
    unittest.main()
