"""Windows Startup shortcut helper for FTW autostart."""
import os
import tempfile
import unittest
from unittest.mock import patch

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
            with patch.object(autostart, "clear_registry_run_entries", return_value=[]):
                autostart.disable(startup_dir=tmp)
            self.assertFalse(autostart.is_enabled(startup_dir=tmp))
            with patch.object(autostart, "clear_registry_run_entries", return_value=[]):
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
            with patch.object(autostart, "clear_registry_run_entries", return_value=["FTW"]):
                removed = autostart.enable(startup_dir=startup, base_dir=tmp)
            self.assertEqual(removed, ["FTW"])
            self.assertTrue(autostart.is_enabled(startup_dir=startup))
            with patch.object(autostart, "clear_registry_run_entries", return_value=[]):
                autostart.disable(startup_dir=startup)
            self.assertFalse(autostart.is_enabled(startup_dir=startup))

    def test_run_value_matches_name_path_or_launcher(self):
        root = r"D:\Apps\FTW"
        self.assertTrue(autostart._run_value_is_ours("WhisperFastGUI", "anything", root))
        self.assertTrue(
            autostart._run_value_is_ours("Other", r'"D:\Apps\FTW\run_whisper.vbs"', root)
        )
        self.assertTrue(autostart._run_value_is_ours("Other", r"C:\x\start_delayed.vbs", root))
        self.assertFalse(autostart._run_value_is_ours("OneDrive", r"C:\Program Files\OneDrive.exe", root))

    def test_clear_registry_deletes_only_our_values(self):
        root = r"D:\Apps\FTW"
        values = [
            ("FTW", r"wscript.exe D:\Apps\FTW\start_delayed.vbs", 1),
            ("OneDrive", r"C:\Program Files\OneDrive.exe", 1),
            ("Old", r"C:\legacy\run_whisper.vbs", 1),
        ]
        deleted = []

        class _Key:
            pass

        def enum_value(_key, index):
            if index >= len(values):
                raise OSError(index)
            return values[index]

        with patch.object(autostart.sys, "platform", "win32"):
            with patch.dict("sys.modules", {"winreg": unittest.mock.Mock()}):
                import winreg

                winreg.HKEY_CURRENT_USER = object()
                winreg.KEY_READ = 1
                winreg.KEY_SET_VALUE = 2
                winreg.REG_SZ = 1
                winreg.REG_EXPAND_SZ = 2
                winreg.OpenKey.return_value = _Key()
                winreg.EnumValue.side_effect = enum_value
                winreg.DeleteValue.side_effect = lambda _key, name: deleted.append(name)
                removed = autostart.clear_registry_run_entries(root)
        self.assertEqual(removed, ["FTW", "Old"])
        self.assertEqual(deleted, ["FTW", "Old"])
        winreg.CloseKey.assert_called_once()


if __name__ == "__main__":
    unittest.main()
