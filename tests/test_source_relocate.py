"""Source relocate helpers: named-folder reuse and one-time move."""
import os
import tempfile
import unittest

from whisperfast.core.source_relocate import (
    move_source_to_output_dir,
    named_folder_output_dir,
    unique_dest_path,
)


def _identity(name):
    return name


class TestNamedFolderOutputDir(unittest.TestCase):
    def test_first_processing_uses_child_named_after_stem(self):
        src = os.path.join("media", "talk.mp4")
        out = named_folder_output_dir(src, "{basename}", _identity)
        self.assertEqual(os.path.basename(out), "talk")
        self.assertEqual(os.path.basename(os.path.dirname(out)), "media")

    def test_already_in_same_named_folder_does_not_nest(self):
        src = os.path.join("media", "talk", "talk.mp4")
        out = named_folder_output_dir(src, "{basename}", _identity)
        self.assertEqual(os.path.normcase(out), os.path.normcase(os.path.abspath(os.path.join("media", "talk"))))

    def test_repeated_reprocess_stays_in_first_folder(self):
        first = named_folder_output_dir(os.path.join("media", "clip.wav"), "{basename}", _identity)
        nested_src = os.path.join(first, "clip.wav")
        again = named_folder_output_dir(nested_src, "{basename}", _identity)
        third = named_folder_output_dir(os.path.join(again, "clip.wav"), "{basename}", _identity)
        self.assertEqual(os.path.normcase(again), os.path.normcase(os.path.abspath(first)))
        self.assertEqual(os.path.normcase(third), os.path.normcase(os.path.abspath(first)))

    def test_custom_template_reuses_matching_parent(self):
        src = os.path.join("media", "processed_talk", "talk.mp4")
        out = named_folder_output_dir(src, "processed_{basename}", _identity)
        self.assertEqual(
            os.path.normcase(out),
            os.path.normcase(os.path.abspath(os.path.join("media", "processed_talk"))),
        )

    def test_custom_template_still_creates_child_when_not_there(self):
        src = os.path.join("media", "talk.mp4")
        out = named_folder_output_dir(src, "processed_{basename}", _identity)
        self.assertEqual(os.path.basename(out), "processed_talk")


class TestMoveSourceToOutputDir(unittest.TestCase):
    def test_same_directory_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "a.mp3")
            with open(src, "w", encoding="utf-8") as f:
                f.write("x")
            final, moved, err = move_source_to_output_dir(src, tmp)
            self.assertFalse(moved)
            self.assertIsNone(err)
            self.assertEqual(os.path.normcase(final), os.path.normcase(src))
            self.assertTrue(os.path.isfile(src))

    def test_moves_once_then_stays(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "talk.mp4")
            with open(src, "w", encoding="utf-8") as f:
                f.write("x")
            dest_dir = named_folder_output_dir(src, "{basename}", _identity)
            final, moved, err = move_source_to_output_dir(src, dest_dir)
            self.assertTrue(moved)
            self.assertIsNone(err)
            self.assertTrue(os.path.isfile(final))
            self.assertFalse(os.path.isfile(src))

            dest_again = named_folder_output_dir(final, "{basename}", _identity)
            stayed, moved_again, err2 = move_source_to_output_dir(final, dest_again)
            self.assertFalse(moved_again)
            self.assertIsNone(err2)
            self.assertEqual(os.path.normcase(stayed), os.path.normcase(final))
            self.assertTrue(os.path.isfile(final))
            nested = os.path.join(dest_dir, "talk", "talk.mp4")
            self.assertFalse(os.path.exists(nested))


class TestUniqueDestPath(unittest.TestCase):
    def test_increments_when_taken(self):
        with tempfile.TemporaryDirectory() as tmp:
            taken = os.path.join(tmp, "a.mp3")
            with open(taken, "w", encoding="utf-8") as f:
                f.write("x")
            dest = unique_dest_path(tmp, "a.mp3")
            self.assertEqual(os.path.basename(dest), "a_1.mp3")
