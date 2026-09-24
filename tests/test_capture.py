"""Capture WAV repair, crash markers, pause flags (no audio hardware)."""
import os
import struct
import tempfile
import unittest
import wave

from whisperfast.core.capture import (
    CaptureSession,
    recover_captures,
    repair_wav_header,
)


def _write_pcm_wav(path, frames=80, rate=8000):
    pcm = b"\x00\x00\x01\x00" * frames  # stereo 16-bit
    with wave.open(path, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm)
    return frames * 4


class TestRepairWavHeader(unittest.TestCase):
    def test_rewrites_zero_data_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "capture_crash.wav")
            data_bytes = _write_pcm_wav(path)
            with open(path, "r+b") as f:
                f.seek(4)
                f.write(struct.pack("<I", 36))
                f.seek(40)
                f.write(struct.pack("<I", 0))
            self.assertTrue(repair_wav_header(path))
            with open(path, "rb") as f:
                header = f.read(44)
            size = os.path.getsize(path)
            self.assertEqual(struct.unpack_from("<I", header, 4)[0], size - 8)
            self.assertEqual(struct.unpack_from("<I", header, 40)[0], data_bytes)
            self.assertFalse(repair_wav_header(path))

    def test_skips_non_wav(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "capture_x.wav")
            with open(path, "wb") as f:
                f.write(b"not a wav file at all........")
            self.assertFalse(repair_wav_header(path))


class TestRecoverCaptures(unittest.TestCase):
    def test_repairs_inprogress_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "capture_20260101_120000.wav")
            _write_pcm_wav(path)
            with open(path, "r+b") as f:
                f.seek(40)
                f.write(struct.pack("<I", 0))
            with open(path + ".inprogress", "w", encoding="utf-8") as marker:
                marker.write("recording\n")
            recovered = recover_captures(tmp)
            self.assertEqual(len(recovered), 1)
            self.assertTrue(os.path.isfile(recovered[0]))
            self.assertFalse(os.path.isfile(path + ".inprogress"))


class TestCaptureSessionPause(unittest.TestCase):
    def test_toggle_pause_without_start(self):
        session = CaptureSession()
        self.assertFalse(session.toggle_pause())
        self.assertFalse(session.paused)
        session._running = True
        self.assertTrue(session.toggle_pause())
        self.assertTrue(session.paused)
        self.assertFalse(session.toggle_pause())
        self.assertFalse(session.paused)


class TestLoopbackFallbackLog(unittest.TestCase):
    def test_pcm16_mixes_to_mono(self):
        import ctypes

        import numpy as np

        from whisperfast.core.wasapi_loopback import _frames_to_mono

        stereo = np.array([[1000, -1000], [2000, 0]], dtype="<i2")
        raw = ctypes.create_string_buffer(stereo.tobytes())
        mono = _frames_to_mono(ctypes.c_void_p(ctypes.addressof(raw)), 2, 2, 1, 16, 4)
        self.assertEqual(mono.shape, (2,))
        self.assertAlmostEqual(float(mono[0]), 0.0, places=4)

    def test_note_flushes_when_log_attached(self):
        session = CaptureSession()
        session._note("loopback down")
        logs = []
        session.set_log(logs.append)
        self.assertEqual(logs, ["loopback down"])
        session._note("still down")
        self.assertEqual(logs[-1], "still down")


class TestCaptureLogSession(unittest.TestCase):
    def test_log_capture_file_creates_clickable_source(self):
        from whisperfast.ui.capture_ui import _log_capture_file, _update_capture_log

        class FakeApp:
            def __init__(self):
                self.begun = []
                self.events = []
                self.outputs = []
                self.sources = []
                self.logs = []
                self._ids = {}

            def begin_file_log(self, source, name=None, current=None, total=None):
                fid = f"f{len(self.begun) + 1}"
                self.begun.append((fid, source, name))
                self._ids[os.path.abspath(source)] = fid
                return fid

            def find_file_log_id(self, path):
                return self._ids.get(os.path.abspath(path))

            def log_file_event(self, msg, tag=None, file_id=None, callback=None):
                self.events.append((file_id, msg))

            def add_file_output(self, role, path, label=None, file_id=None, reindex=True):
                self.outputs.append((file_id, role, path))

            def set_file_source(self, file_id, path, reindex=True):
                self.sources.append((file_id, path))

            def log(self, msg, tag=None):
                self.logs.append((msg, tag))

        with tempfile.TemporaryDirectory() as tmp:
            wav = os.path.join(tmp, "capture_x.wav")
            opus = os.path.join(tmp, "meeting.opus")
            app = FakeApp()
            fid = _log_capture_file(app, wav, "capture_started")
            self.assertEqual(fid, "f1")
            self.assertEqual(app.outputs[0][1], "source")
            self.assertEqual(os.path.normpath(app.outputs[0][2]), os.path.normpath(wav))
            self.assertTrue(app.events[0][1])
            _update_capture_log(app, fid, opus, "capture_saved")
            self.assertEqual(os.path.normpath(app.sources[0][1]), os.path.normpath(opus))
            self.assertEqual(app.outputs[-1][1], "source")
            again = _log_capture_file(app, wav, "capture_started")
            self.assertEqual(again, fid)
            self.assertEqual(len(app.begun), 1)


class TestClipLog(unittest.TestCase):
    def test_clip_result_writes_main_log_and_parent_session(self):
        from whisperfast.ui.capture_ui import _log_clip_result

        class Session:
            log_file_id = "rec-1"

        class FakeApp:
            def __init__(self):
                self.logs = []
                self.file_events = []

            def log(self, msg, tag=None):
                self.logs.append((str(msg), tag))

            def make_file_logger(self, file_id):
                def _log(msg, *a, **k):
                    self.file_events.append((file_id, str(msg)))

                return _log

        with tempfile.TemporaryDirectory() as tmp:
            clip = os.path.join(tmp, "capture_clip.wav")
            _write_pcm_wav(clip)
            app = FakeApp()
            _log_clip_result(app, Session(), clip, "0:18")
            self.assertEqual(len(app.logs), 1)
            self.assertEqual(app.logs[0][1], "link")
            self.assertIn("0:18", app.logs[0][0])
            self.assertIn(os.path.abspath(clip), app.logs[0][0])
            self.assertEqual(app.file_events[0][0], "rec-1")
            self.assertIn("0:18", app.file_events[0][1])

    def test_clip_result_failed_without_file(self):
        from whisperfast.ui.capture_ui import _log_clip_result

        class Session:
            log_file_id = ""

        class FakeApp:
            def __init__(self):
                self.logs = []

            def log(self, msg, tag=None):
                self.logs.append((str(msg), tag))

        app = FakeApp()
        _log_clip_result(app, Session(), "", "0:18")
        self.assertEqual(len(app.logs), 1)
        self.assertIsNone(app.logs[0][1])


class TestReuseCaptureFileLog(unittest.TestCase):
    def test_reuses_existing_id(self):
        from whisperfast.core.transcription import _reuse_or_begin_file_log

        class FakeApp:
            def __init__(self):
                self.begun = 0
                self.attached = []

                class Panel:
                    def __init__(self, outer):
                        self.outer = outer

                    def attach_file(self, file_id):
                        self.outer.attached.append(file_id)

                self.log_panel = Panel(self)

            def find_file_log_id(self, path):
                return "cap-1"

            def begin_file_log(self, source, name=None, current=None, total=None):
                self.begun += 1
                return "new"

        app = FakeApp()
        self.assertEqual(_reuse_or_begin_file_log(app, r"D:\a.opus", name="a.opus"), "cap-1")
        self.assertEqual(app.begun, 0)
        self.assertEqual(app.attached, ["cap-1"])


class TestEnqueueCapture(unittest.TestCase):
    def test_stop_puts_recording_in_processing_queue(self):
        from unittest.mock import patch

        from whisperfast.core.queue_manager import QueueController
        from whisperfast.ui.capture_ui import _enqueue_capture, _finalize_and_enqueue

        class Tree:
            def __init__(self):
                self.rows = {}
                self._order = []

            def insert(self, parent, index, iid=None, values=()):
                iid = iid or str(len(self._order))
                self.rows[iid] = tuple(values)
                self._order.append(iid)
                return iid

            def delete(self, *iids):
                drop = set(iids)
                self._order = [i for i in self._order if i not in drop]
                for i in drop:
                    self.rows.pop(i, None)

            def get_children(self):
                return list(self._order)

        class FakeApp:
            def __init__(self, ctrl):
                self.queue_ctrl = ctrl
                self.logs = []

            def log(self, msg, tag=None):
                self.logs.append((msg, tag))

            def begin_file_log(self, source, name=None, current=None, total=None):
                return "f1"

            def find_file_log_id(self, path):
                return None

            def log_file_event(self, msg, tag=None, file_id=None, callback=None):
                self.logs.append((file_id, msg))

            def add_file_output(self, role, path, label=None, file_id=None, reindex=True):
                return None

        with tempfile.TemporaryDirectory() as tmp:
            wav = os.path.join(tmp, "capture_x.wav")
            _write_pcm_wav(wav)
            qfile = os.path.join(tmp, "request_queue.json")
            ctrl = QueueController(request_queue_file=qfile, log_func=lambda *a, **k: None)
            ctrl.bind_treeview(Tree())
            app = FakeApp(ctrl)
            _enqueue_capture(app, wav)
            self.assertEqual(len(ctrl.queue), 1)
            self.assertEqual(os.path.normcase(ctrl.queue[0]["path"]), os.path.normcase(wav))
            self.assertEqual(len(ctrl.queue_list.get_children()), 1)
            with patch("whisperfast.ui.capture_ui.finalize_wav", return_value=wav), patch(
                "whisperfast.ui.capture_ui.meeting_window_title", return_value="FTW"
            ):
                out = _finalize_and_enqueue(app, wav, {}, is_clip=False)
            self.assertEqual(os.path.normcase(out), os.path.normcase(wav))
            self.assertEqual(len(ctrl.queue), 1)

    def test_finalize_enqueues_when_window_title_raises(self):
        from unittest.mock import patch

        from whisperfast.core.queue_manager import QueueController
        from whisperfast.ui.capture_ui import _finalize_and_enqueue

        class Tree:
            def __init__(self):
                self.rows = {}
                self._order = []

            def insert(self, parent, index, iid=None, values=()):
                iid = iid or str(len(self._order))
                self.rows[iid] = tuple(values)
                self._order.append(iid)
                return iid

            def delete(self, *iids):
                drop = set(iids)
                self._order = [i for i in self._order if i not in drop]
                for i in drop:
                    self.rows.pop(i, None)

            def get_children(self):
                return list(self._order)

        class FakeApp:
            def __init__(self, ctrl):
                self.queue_ctrl = ctrl
                self.logs = []

            def log(self, msg, tag=None):
                self.logs.append((msg, tag))

            def begin_file_log(self, source, name=None, current=None, total=None):
                return "f1"

            def find_file_log_id(self, path):
                return None

            def log_file_event(self, msg, tag=None, file_id=None, callback=None):
                self.logs.append((file_id, msg))

            def add_file_output(self, role, path, label=None, file_id=None, reindex=True):
                return None

        with tempfile.TemporaryDirectory() as tmp:
            wav = os.path.join(tmp, "capture_x.wav")
            _write_pcm_wav(wav)
            ctrl = QueueController(
                request_queue_file=os.path.join(tmp, "q.json"),
                log_func=lambda *a, **k: None,
            )
            ctrl.bind_treeview(Tree())
            app = FakeApp(ctrl)
            with patch("whisperfast.ui.capture_ui.finalize_wav", return_value=wav), patch(
                "whisperfast.ui.capture_ui.meeting_window_title",
                side_effect=AttributeError("set"),
            ):
                out = _finalize_and_enqueue(app, wav, {}, is_clip=False)
            self.assertEqual(os.path.normcase(out), os.path.normcase(wav))
            self.assertEqual(len(ctrl.queue), 1)

    def test_capture_log_skips_library_when_panel_present(self):
        from whisperfast.ui.capture_ui import _log_capture_file

        class Panel:
            def __init__(self):
                self.begun = []
                self.events = []
                self.outputs = []

            def begin_file(self, source, name=None, current=None, total=None):
                self.begun.append(source)
                return "panel-1"

            def log_file_event(self, msg, tag=None, file_id=None, callback=None):
                self.events.append((file_id, msg))

            def add_file_output(self, role, path, label=None, file_id=None):
                self.outputs.append((file_id, role, path))

        class FakeApp:
            def __init__(self):
                self.log_panel = Panel()
                self.library_begins = 0

            def find_file_log_id(self, path):
                return None

            def begin_file_log(self, source, name=None, current=None, total=None):
                self.library_begins += 1
                return "lib-1"

        with tempfile.TemporaryDirectory() as tmp:
            wav = os.path.join(tmp, "capture_x.wav")
            _write_pcm_wav(wav)
            app = FakeApp()
            fid = _log_capture_file(app, wav, "capture_started")
            self.assertEqual(fid, "panel-1")
            self.assertEqual(app.library_begins, 0)
            self.assertEqual(len(app.log_panel.begun), 1)

    def test_finalize_wav_drops_draft_after_opus(self):
        from unittest.mock import patch

        from whisperfast.core.capture_finalize import finalize_wav

        with tempfile.TemporaryDirectory() as tmp:
            wav = os.path.join(tmp, "capture_x.wav")
            _write_pcm_wav(wav)
            opus = os.path.join(tmp, "meeting.opus")
            with open(opus, "wb") as f:
                f.write(b"OggS")
            with patch(
                "whisperfast.core.capture_finalize.encode_capture",
                return_value=(opus, None),
            ):
                out = finalize_wav(wav, {"capture_codec": "opus"}, window_title="FTW")
            self.assertEqual(os.path.normcase(out), os.path.normcase(opus))
            self.assertFalse(os.path.isfile(wav))


class TestLogCancelStopsCapture(unittest.TestCase):
    def test_enabled_while_recording_and_stops_it(self):
        import threading
        from types import SimpleNamespace
        from unittest.mock import patch

        from whisperfast.core.capture import get_capture_session
        from whisperfast.ui.capture_ui import handle_log_cancel, sync_log_cancel_button

        class Btn:
            def __init__(self):
                self.state = "disabled"

            def config(self, **kw):
                if "state" in kw:
                    self.state = kw["state"]

        session = get_capture_session()
        was = session._running
        session._running = True
        logs = []
        lock = threading.Lock()
        app = SimpleNamespace(
            cancel_requested=False,
            log=logs.append,
            _process_queue_lock=lock,
            cancel_btn=Btn(),
        )
        try:
            sync_log_cancel_button(app)
            self.assertEqual(app.cancel_btn.state, "normal")
            with patch("whisperfast.ui.capture_ui.stop_capture") as stop:
                handle_log_cancel(app)
            stop.assert_called_once_with(app)
            self.assertFalse(app.cancel_requested)
            self.assertEqual(logs, [])
            lock.acquire()
            with patch("whisperfast.ui.capture_ui.stop_capture"):
                handle_log_cancel(app)
            self.assertTrue(app.cancel_requested)
            self.assertTrue(logs)
        finally:
            session._running = was
            if lock.locked():
                lock.release()


if __name__ == "__main__":
    unittest.main()
