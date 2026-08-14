"""Output path conflict helpers — overwrite, timed suffix, skip."""
import os
import unittest
from datetime import datetime
from unittest.mock import patch

from whisperfast.core.output_conflict import (
    apply_time_suffix_to_paths,
    make_timed_alt_path,
    resolve_output_paths,
)


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 8, 14, 15, 30, 45)


class TestMakeTimedAltPath(unittest.TestCase):
    def test_hhmm_when_free(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "talk.txt")
            with patch("whisperfast.core.output_conflict.datetime", _FrozenDateTime):
                alt = make_timed_alt_path(src)
            self.assertEqual(alt, os.path.join(tmp, "talk_1530.txt"))

    def test_falls_back_to_hhmmss_then_counter(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "talk.txt")
            taken_hhmm = os.path.join(tmp, "talk_1530.txt")
            taken_hhmmss = os.path.join(tmp, "talk_153045.txt")
            for path in (src, taken_hhmm, taken_hhmmss):
                with open(path, "w", encoding="utf-8") as f:
                    f.write("x")
            with patch("whisperfast.core.output_conflict.datetime", _FrozenDateTime):
                alt = make_timed_alt_path(src)
            self.assertEqual(alt, os.path.join(tmp, "talk_153045_1.txt"))


class TestApplyTimeSuffixToPaths(unittest.TestCase):
    def test_same_stamp_on_group(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = [
                os.path.join(tmp, "a.txt"),
                os.path.join(tmp, "a.srt"),
                "",
            ]
            result = apply_time_suffix_to_paths(paths, stamp="1530")
            self.assertEqual(result[0], os.path.abspath(os.path.join(tmp, "a_1530.txt")))
            self.assertEqual(result[1], os.path.abspath(os.path.join(tmp, "a_1530.srt")))
            self.assertEqual(result[2], "")


class TestResolveOutputPaths(unittest.TestCase):
    def test_no_existing_does_not_ask(self):
        asked = []

        def ask(path, alt_name):
            asked.append((path, alt_name))
            return True

        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = [os.path.join(tmp, "new.txt"), os.path.join(tmp, "new.srt")]
            result = resolve_output_paths(paths, ask)
            self.assertEqual(result, [os.path.abspath(p) for p in paths])
            self.assertEqual(asked, [])

    def test_overwrite_keeps_paths(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            existing = os.path.join(tmp, "talk.txt")
            with open(existing, "w", encoding="utf-8") as f:
                f.write("x")
            result = resolve_output_paths([existing], lambda *_: True)
            self.assertEqual(result, [os.path.abspath(existing)])

    def test_skip_returns_empty_strings(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            existing = os.path.join(tmp, "talk.txt")
            sibling = os.path.join(tmp, "talk.srt")
            with open(existing, "w", encoding="utf-8") as f:
                f.write("x")
            result = resolve_output_paths([existing, sibling], lambda *_: None)
            self.assertEqual(result, ["", ""])

    def test_no_overwrite_applies_time_suffix(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            existing = os.path.join(tmp, "talk.txt")
            sibling = os.path.join(tmp, "talk.srt")
            with open(existing, "w", encoding="utf-8") as f:
                f.write("x")
            with patch("whisperfast.core.output_conflict.datetime", _FrozenDateTime):
                result = resolve_output_paths([existing, sibling], lambda *_: False)
            self.assertTrue(result[0].endswith("talk_1530.txt") or "talk_1530" in os.path.basename(result[0]))
            self.assertTrue(result[1].endswith("talk_1530.srt") or "talk_1530" in os.path.basename(result[1]))
            self.assertEqual(
                os.path.splitext(os.path.basename(result[0]))[0],
                os.path.splitext(os.path.basename(result[1]))[0],
            )


if __name__ == "__main__":
    unittest.main()
