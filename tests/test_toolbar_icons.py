"""Toolbar pictograms: bundled PNGs and icon-only capture buttons."""
import os
import tkinter as tk
from tkinter import ttk
import unittest
from types import SimpleNamespace

from whisperfast.ui import toolbar_icons
from whisperfast.ui.capture_ui import refresh_capture_buttons


def _make_root():
    try:
        root = tk.Tk()
    except tk.TclError as e:
        raise unittest.SkipTest(f"no Tk display available: {e}")
    root.withdraw()
    return root


class TestToolbarIcons(unittest.TestCase):
    def test_all_png_files_exist(self):
        missing = [
            key
            for key in toolbar_icons.FILE_MAP
            if not os.path.isfile(toolbar_icons.icon_path(key))
        ]
        self.assertEqual(missing, [], f"missing toolbar PNG for: {missing}")
        self.assertEqual(toolbar_icons.FILE_MAP["archive"], "search.png")

    def test_load_and_apply_on_buttons(self):
        root = _make_root()
        self.addCleanup(root.destroy)
        photos = toolbar_icons.load(root)
        self.assertTrue(photos, "expected PhotoImage objects from resources/icons")
        for key in toolbar_icons.FILE_MAP:
            self.assertIn(key, photos)

        app = SimpleNamespace(
            _toolbar_photos=photos,
            add_files_btn=ttk.Button(root),
            add_directory_btn=ttk.Button(root),
            clear_queue_btn=ttk.Button(root),
            archive_btn=ttk.Button(root),
            capture_clip_btn=ttk.Button(root),
            capture_settings_btn=ttk.Button(root),
            capture_btn=ttk.Button(root),
            capture_pause_btn=ttk.Button(root),
            play_sound_btn=ttk.Button(root),
            play_sound_on_finish=tk.BooleanVar(value=True),
        )
        toolbar_icons.apply_static(app)
        self.assertEqual(app.add_files_btn._toolbar_icon_key, "add_files")
        self.assertEqual(app.add_directory_btn._toolbar_icon_key, "add_directory")
        self.assertEqual(app.clear_queue_btn._toolbar_icon_key, "clear_queue")
        self.assertEqual(app.archive_btn._toolbar_icon_key, "archive")
        self.assertEqual(app.capture_settings_btn._toolbar_icon_key, "capture_settings")
        self.assertEqual(app.play_sound_btn._toolbar_icon_key, "notify_on")
        self.assertEqual(app.add_files_btn.cget("text"), "")
        self.assertEqual(app.play_sound_btn.cget("text"), "")

        app.play_sound_on_finish.set(False)
        toolbar_icons.apply_notify_state(app)
        self.assertEqual(app.play_sound_btn._toolbar_icon_key, "notify_off")

        toolbar_icons.apply_capture_state(app, running=False, paused=False)
        self.assertEqual(app.capture_btn._toolbar_icon_key, "capture_start")
        self.assertEqual(app.capture_pause_btn._toolbar_icon_key, "capture_pause")
        toolbar_icons.apply_capture_state(app, running=True, paused=True)
        self.assertEqual(app.capture_btn._toolbar_icon_key, "capture_stop")
        self.assertEqual(app.capture_pause_btn._toolbar_icon_key, "capture_resume")
        self.assertEqual(app.capture_btn.cget("text"), "")

    def test_refresh_capture_buttons_keeps_icons_not_labels(self):
        root = _make_root()
        self.addCleanup(root.destroy)
        photos = toolbar_icons.load(root)
        app = SimpleNamespace(
            _toolbar_photos=photos,
            capture_btn=ttk.Button(root, text="should-not-stay"),
            capture_pause_btn=ttk.Button(root, text="should-not-stay"),
            capture_clip_btn=ttk.Button(root, text="should-not-stay"),
            capture_settings_btn=ttk.Button(root, text="should-not-stay"),
            capture_clip_entry=None,
        )
        refresh_capture_buttons(app)
        self.assertEqual(app.capture_btn.cget("text"), "")
        self.assertEqual(app.capture_btn._toolbar_icon_key, "capture_start")
        self.assertEqual(str(app.capture_pause_btn.cget("state")), "disabled")


if __name__ == "__main__":
    unittest.main()
