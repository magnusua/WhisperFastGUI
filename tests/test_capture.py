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


if __name__ == "__main__":
    unittest.main()
