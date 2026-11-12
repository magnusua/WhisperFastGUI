"""Sidecars next to a transcript are named after the video."""
import json
import os
import tempfile
import unittest

from whisperfast.core.session_store import (
    list_session_folders,
    load_meta,
    merge_meta,
    summary_path,
    write_summary,
)


class TestSessionSidecarNames(unittest.TestCase):
    def test_meta_and_summary_include_the_video_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = os.path.join(tmp, "Interview 12.mp4")
            txt = os.path.join(tmp, "Interview 12.txt")
            meta = merge_meta(tmp, {"source": video, "txt_path": txt}, source=txt)
            summary = write_summary(tmp, "short brief", source=txt)
            self.assertEqual(os.path.basename(meta), "Interview 12.meta.json")
            self.assertEqual(os.path.basename(summary), "Interview 12.summary.md")
            self.assertTrue(os.path.isfile(meta))
            self.assertEqual(load_meta(tmp, txt)["source"], video)
            self.assertEqual(summary_path(tmp, txt), summary)

    def test_two_videos_in_one_folder_keep_separate_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = merge_meta(tmp, {"source": "a.mp4", "calendar_title": "A"}, source="a.txt")
            second = merge_meta(tmp, {"source": "b.mp4", "calendar_title": "B"}, source="b.txt")
            self.assertNotEqual(first, second)
            self.assertEqual(load_meta(tmp, "a.txt")["calendar_title"], "A")
            self.assertEqual(load_meta(tmp, "b.txt")["calendar_title"], "B")
            folders = list(list_session_folders(tmp))
            self.assertEqual(len(folders), 1)

    def test_legacy_meta_json_is_still_found_and_reused_for_the_same_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = os.path.join(tmp, "meta.json")
            with open(legacy, "w", encoding="utf-8") as handle:
                json.dump({"source": os.path.join(tmp, "Talk.mp4"), "calendar_title": "Standup"}, handle)
            loaded = load_meta(tmp, os.path.join(tmp, "Talk.txt"))
            self.assertEqual(loaded["calendar_title"], "Standup")
            written = merge_meta(
                tmp,
                {"model": "large-v3"},
                source=os.path.join(tmp, "Talk.txt"),
            )
            self.assertEqual(os.path.basename(written), "Talk.meta.json")
            self.assertEqual(load_meta(tmp, "Talk.txt")["calendar_title"], "Standup")
            self.assertEqual(load_meta(tmp, "Talk.txt")["model"], "large-v3")
            self.assertIn(os.path.abspath(tmp), list(list_session_folders(tmp)))


if __name__ == "__main__":
    unittest.main()
