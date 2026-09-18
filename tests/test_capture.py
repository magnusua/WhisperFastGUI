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


if __name__ == "__main__":
    unittest.main()
