"""Missing translation keys are returned as-is and warned once."""
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from whisperfast.i18n import lang_manager
from whisperfast.i18n.lang_manager import t


class TestMissingTranslationKey(unittest.TestCase):
    def setUp(self):
        lang_manager._missing_key_warned.clear()

    def tearDown(self):
        lang_manager._missing_key_warned.clear()

    def test_known_key_does_not_warn(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            text = t("error")
        self.assertNotEqual(text, "error")
        self.assertNotIn("Missing translation key", buf.getvalue())

    def test_unknown_key_returns_key_and_warns_once(self):
        key = "__wf_missing_key_for_test__"
        buf = io.StringIO()
        with redirect_stdout(buf):
            first = t(key)
            second = t(key)
        self.assertEqual(first, key)
        self.assertEqual(second, key)
        warnings = [
            line for line in buf.getvalue().splitlines() if "Missing translation key" in line
        ]
        self.assertEqual(len(warnings), 1)
        self.assertIn(repr(key), warnings[0])

    def test_no_warn_when_translations_empty(self):
        key = "__wf_missing_while_empty__"
        buf = io.StringIO()
        with patch.object(lang_manager, "_translations", {}):
            with patch.object(lang_manager, "load_translations"):
                with redirect_stdout(buf):
                    self.assertEqual(t(key), key)
        self.assertNotIn("Missing translation key", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
