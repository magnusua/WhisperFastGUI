"""Restart scripts must not interpolate archive filenames into the shell."""
import os
import tempfile
import unittest
from unittest.mock import patch

from whisperfast.updates import app_updates


class TestRestartScriptNoShellInterpolation(unittest.TestCase):
    def test_bat_or_sh_does_not_embed_zip_entry_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "src")
            os.makedirs(source)
            with open(os.path.join(source, "main.py"), "w", encoding="utf-8") as f:
                f.write("# hi\n")
            apply_py = os.path.join(tmp, "_apply_update.py")
            with patch.object(app_updates, "BASE_DIR", tmp), patch.object(
                app_updates, "_APPLY_UPDATE_PY", apply_py
            ):
                script = app_updates._write_restart_script(source)
            with open(script, "r", encoding="utf-8") as f:
                body = f.read().lower()
            self.assertNotIn("xcopy", body)
            self.assertNotIn("copy /y", body)
            self.assertNotIn("rsync", body)
            self.assertIn("_apply_update.py", body)
            with open(apply_py, "r", encoding="utf-8") as f:
                py_body = f.read()
            self.assertIn("shutil", py_body)
            self.assertIn(repr(os.path.abspath(source)), py_body)


if __name__ == "__main__":
    unittest.main()
