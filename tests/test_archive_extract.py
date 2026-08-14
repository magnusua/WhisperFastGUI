"""Zip-slip / tar-slip path checks."""
import os
import tempfile
import unittest
import zipfile

from whisperfast.archive_extract import (
    UnsafeArchiveMember,
    archive_member_destination,
    safe_extract_zip,
)


class TestArchiveMemberDestination(unittest.TestCase):
    def test_normal_relative(self):
        dest = os.path.abspath(os.path.join("app", "extract"))
        target = archive_member_destination(dest, "whisperfast/main.py")
        self.assertIsNotNone(target)
        self.assertTrue(target.startswith(dest))

    def test_rejects_parent_and_absolute(self):
        dest = os.path.abspath("extract")
        self.assertIsNone(archive_member_destination(dest, "../evil.txt"))
        self.assertIsNone(archive_member_destination(dest, "..\\evil.txt"))
        self.assertIsNone(archive_member_destination(dest, "/tmp/evil.txt"))
        self.assertIsNone(archive_member_destination(dest, "foo/../../evil.txt"))
        self.assertIsNone(archive_member_destination(dest, "C:/Windows/evil.txt"))


class TestSafeExtractZip(unittest.TestCase):
    def test_zip_slip_does_not_write_outside(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "out")
            os.makedirs(dest)
            zip_path = os.path.join(tmp, "bad.zip")
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("../evil.txt", "pwned")
            with self.assertRaises(UnsafeArchiveMember):
                safe_extract_zip(zip_path, dest)
            self.assertFalse(os.path.isfile(os.path.join(tmp, "evil.txt")))

    def test_extracts_safe_member(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "out")
            zip_path = os.path.join(tmp, "ok.zip")
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("pkg/hello.txt", "hi")
            safe_extract_zip(zip_path, dest)
            self.assertTrue(os.path.isfile(os.path.join(dest, "pkg", "hello.txt")))


if __name__ == "__main__":
    unittest.main()
