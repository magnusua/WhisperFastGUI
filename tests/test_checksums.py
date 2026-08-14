"""SHA-256 SUMS parsing and file verification."""
import os
import tempfile
import unittest

from whisperfast.updates.checksums import (
    expected_digest_for_filename,
    parse_sha256sums,
    sha256_file,
    verify_file_sha256,
)


class TestParseSha256Sums(unittest.TestCase):
    def test_gnu_and_binary_marker(self):
        text = (
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa  foo.zip\n"
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb *bar.zip\n"
            "# comment\n"
            "\n"
        )
        mapping = parse_sha256sums(text)
        self.assertEqual(mapping["foo.zip"], "a" * 64)
        self.assertEqual(mapping["bar.zip"], "b" * 64)

    def test_bsd_format_and_basename(self):
        text = "SHA256 (dir/WhisperFastGUI-1.2.11-src.zip) = " + ("c" * 64) + "\n"
        mapping = parse_sha256sums(text)
        self.assertEqual(mapping["WhisperFastGUI-1.2.11-src.zip"], "c" * 64)

    def test_expected_digest_case_insensitive_name(self):
        checksums = {"Release.ZIP": "d" * 64}
        self.assertEqual(expected_digest_for_filename(checksums, "release.zip"), "d" * 64)
        self.assertIsNone(expected_digest_for_filename(checksums, "other.zip"))


class TestVerifyFileSha256(unittest.TestCase):
    def test_match_and_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.bin")
            with open(path, "wb") as f:
                f.write(b"hello")
            digest = sha256_file(path)
            self.assertTrue(verify_file_sha256(path, digest))
            self.assertTrue(verify_file_sha256(path, digest.upper()))
            self.assertFalse(verify_file_sha256(path, "0" * 64))
            self.assertFalse(verify_file_sha256(path, ""))


class TestMakeReleaseChecksumsScript(unittest.TestCase):
    def test_write_sha256sums_gnu_line(self):
        import importlib.util

        script = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "scripts",
            "make_release_checksums.py",
        )
        spec = importlib.util.spec_from_file_location("make_release_checksums", script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as tmp:
            blob = os.path.join(tmp, "WhisperFastGUI-9.9.9-src.zip")
            with open(blob, "wb") as f:
                f.write(b"zip-bytes")
            out = os.path.join(tmp, "SHA256SUMS")
            mod.write_sha256sums([blob], out)
            with open(out, encoding="utf-8") as f:
                body = f.read()
            digest = sha256_file(blob)
            self.assertEqual(body, f"{digest}  WhisperFastGUI-9.9.9-src.zip\n")
            mapping = parse_sha256sums(body)
            self.assertEqual(mapping["WhisperFastGUI-9.9.9-src.zip"], digest)


if __name__ == "__main__":
    unittest.main()
