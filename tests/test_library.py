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


if __name__ == "__main__":
    unittest.main()
