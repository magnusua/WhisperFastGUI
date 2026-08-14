"""Document-type helpers (no markitdown / no I/O)."""
import os
import unittest

from whisperfast.core.document_convert import (
    is_document_file,
    markdown_output_path,
    needs_office_to_md,
)


class TestIsDocumentFile(unittest.TestCase):
    def test_text_and_office(self):
        self.assertTrue(is_document_file("notes.md"))
        self.assertTrue(is_document_file("notes.MD"))
        self.assertTrue(is_document_file("a.txt"))
        self.assertTrue(is_document_file("a.pdf"))
        self.assertTrue(is_document_file("a.docx"))
        self.assertTrue(is_document_file("a.doc"))
        self.assertTrue(is_document_file("a.html"))

    def test_media_is_not_document(self):
        self.assertFalse(is_document_file("clip.mp3"))
        self.assertFalse(is_document_file("clip.mp4"))
        self.assertFalse(is_document_file("clip.wav"))


class TestNeedsOfficeToMd(unittest.TestCase):
    def test_office_only(self):
        self.assertTrue(needs_office_to_md("a.pdf"))
        self.assertTrue(needs_office_to_md("a.DOCX"))
        self.assertFalse(needs_office_to_md("a.md"))
        self.assertFalse(needs_office_to_md("a.txt"))
        self.assertFalse(needs_office_to_md("a.mp3"))


class TestMarkdownOutputPath(unittest.TestCase):
    def test_replaces_extension_in_output_dir(self):
        out = markdown_output_path(os.path.join("in", "Talk.PDF"), os.path.join("out", "dest"))
        self.assertEqual(os.path.basename(out), "Talk.md")
        self.assertEqual(os.path.abspath(out), os.path.abspath(os.path.join("out", "dest", "Talk.md")))


if __name__ == "__main__":
    unittest.main()
