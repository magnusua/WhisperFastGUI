"""Windows Startup shortcut helper for FTW autostart."""
import os
import tempfile
import unittest

from whisperfast import autostart


class TestAutostart(unittest.TestCase):
    def test_shortcut_path_uses_startup_dir(self):
        path = autostart.shortcut_path(r"C:\Startup")
        self.assertEqual(os.path.basename(path), autostart.LNK_NAME)
        self.assertTrue(path.endswith(os.path.join("Startup", autostart.LNK_NAME)))

    def test_is_enabled_and_disable_with_stub_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(autostart.is_enabled(startup_dir=tmp))
            stub = autostart.shortcut_path(tmp)
            with open(stub, "w", encoding="utf-8") as f:
                f.write("stub")
            self.assertTrue(autostart.is_enabled(startup_dir=tmp))
            autostart.disable(startup_dir=tmp)
            self.assertFalse(autostart.is_enabled(startup_dir=tmp))
            autostart.disable(startup_dir=tmp)

    def test_enable_requires_vbs(self):
        with tempfile.TemporaryDirectory() as tmp:
            startup = os.path.join(tmp, "Startup")
            os.makedirs(startup)
            with self.assertRaises(FileNotFoundError):
                autostart.enable(startup_dir=startup, base_dir=tmp)

    def test_enable_creates_shortcut(self):
        with tempfile.TemporaryDirectory() as tmp:
            vbs = autostart.vbs_path(tmp)
            with open(vbs, "w", encoding="utf-8") as f:
                f.write("' test\n")
            startup = os.path.join(tmp, "Startup")
            autostart.enable(startup_dir=startup, base_dir=tmp)
            self.assertTrue(autostart.is_enabled(startup_dir=startup))
            autostart.disable(startup_dir=startup)
            self.assertFalse(autostart.is_enabled(startup_dir=startup))


if __name__ == "__main__":
    unittest.main()
