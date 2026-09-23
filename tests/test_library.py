"""Conversation library SQLite + FTS."""
import os
import tempfile
import unittest

from whisperfast.library import ConversationLibrary


class TestConversationLibrary(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "library.sqlite")
        self.lib = ConversationLibrary(self.db)
        self.txt = os.path.join(self._tmp.name, "talk.txt")
        with open(self.txt, "w", encoding="utf-8") as f:
            f.write("hello world unique_token_xyz\n")

    def tearDown(self):
        self.lib.close()
        self._tmp.cleanup()

    def test_upsert_search_delete(self):
        job = self.lib.upsert_job(
            {
                "id": "abc",
                "created_at": "2026-09-17T12:00:00",
                "name": "talk.mp3",
                "status": "done",
                "txt_path": self.txt,
                "summary": "a call about widgets",
            }
        )
        self.assertEqual(job["name"], "talk.mp3")
        found = self.lib.search("unique_token_xyz")
        self.assertTrue(any(j["id"] == "abc" for j in found))
        listed = self.lib.list_jobs()
        self.assertEqual(len(listed), 1)
        extra = os.path.join(self._tmp.name, "talk_TW_core.md")
        with open(extra, "w", encoding="utf-8") as f:
            f.write("x")
        self.lib.add_output("abc", "ai", extra, label="TW_core")
        deleted = self.lib.delete_job("abc", delete_files=True)
        self.assertIn(self.txt, deleted)
        self.assertIn(extra, deleted)
        self.assertFalse(os.path.isfile(self.txt))
        self.assertIsNone(self.lib.get_job("abc"))

    def test_set_summary_if_empty_does_not_overwrite(self):
        self.lib.upsert_job(
            {
                "id": "job1",
                "created_at": "2026-09-18T12:00:00",
                "name": "talk.mp3",
                "summary": "typed by user",
            }
        )
        self.assertFalse(self.lib.set_summary_if_empty("job1", "from one_liner"))
        self.assertEqual(self.lib.get_job("job1")["summary"], "typed by user")
        self.lib.upsert_job(
            {
                "id": "job2",
                "created_at": "2026-09-18T12:00:00",
                "name": "other.mp3",
                "summary": "",
            }
        )
        self.assertTrue(self.lib.set_summary_if_empty("job2", "from one_liner"))
        self.assertEqual(self.lib.get_job("job2")["summary"], "from one_liner")

    def test_find_by_source(self):
        src = os.path.join(self._tmp.name, "talk.mp4")
        self.lib.upsert_job(
            {
                "id": "src1",
                "created_at": "2026-09-22T12:00:00",
                "source": src,
                "name": "talk.mp4",
                "txt_path": self.txt,
            }
        )
        found = self.lib.find_by_source(src)
        self.assertIsNotNone(found)
        self.assertEqual(found["id"], "src1")
        self.assertEqual(found["txt_path"], self.txt)
        self.assertIsNone(self.lib.find_by_source(os.path.join(self._tmp.name, "missing.mp4")))


if __name__ == "__main__":
    unittest.main()
