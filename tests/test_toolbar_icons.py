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
        self.assertEqual(toolbar_icons.FILE_MAP["clear_log"], "delete.png")

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
            output_folder_btn=ttk.Button(root),
            mp3_settings_btn=ttk.Button(root),
            watch_dirs_btn=ttk.Button(root),
            watch_enabled=tk.BooleanVar(value=False),
            recog_lang_btn=ttk.Button(root),
            lang_mode=tk.StringVar(value="None"),
            _recog_lang_label=lambda value: "AUTO" if not value or value == "None" else str(value).upper(),
            edit_redactor_btn=ttk.Button(root),
            cursor_api_key_btn=ttk.Button(root),
            telegram_btn=ttk.Button(root),
            export_md_docx_btn=ttk.Button(root),
            clear_log_btn=ttk.Button(root),
            dependencies_btn=ttk.Button(root),
            autostart_btn=ttk.Button(root),
            autostart_enabled=tk.BooleanVar(value=False),
            cancel_btn=ttk.Button(root),
            start_btn=ttk.Button(root),
        )
        toolbar_icons.apply_static(app)
        self.assertEqual(app.add_files_btn._toolbar_icon_key, "add_files")
        self.assertEqual(app.add_directory_btn._toolbar_icon_key, "add_directory")
        self.assertEqual(app.clear_queue_btn._toolbar_icon_key, "clear_queue")
        self.assertEqual(app.archive_btn._toolbar_icon_key, "archive")
        self.assertEqual(app.capture_settings_btn._toolbar_icon_key, "capture_settings")
        self.assertEqual(app.play_sound_btn._toolbar_icon_key, "notify_on")
        self.assertEqual(app.output_folder_btn._toolbar_icon_key, "output_folder")
        self.assertEqual(app.mp3_settings_btn._toolbar_icon_key, "mp3_off")
        self.assertEqual(app.watch_dirs_btn._toolbar_icon_key, "watch_off")
        self.assertEqual(app.recog_lang_btn._toolbar_icon_key, "lang_auto")
        self.assertEqual(app.edit_redactor_btn._toolbar_icon_key, "prompts_off")
        self.assertEqual(app.cursor_api_key_btn._toolbar_icon_key, "api_keys")
        self.assertEqual(app.telegram_btn._toolbar_icon_key, "telegram_off")
        self.assertEqual(app.telegram_btn.cget("text"), "")
        self.assertEqual(app.export_md_docx_btn._toolbar_icon_key, "docx_off")
        app.save_audio_mp3 = tk.BooleanVar(value=True)
        app.send_txt_to_ai = tk.BooleanVar(value=True)
        app.telegram_listener_on = tk.BooleanVar(value=True)
        app.export_md_to_docx = tk.BooleanVar(value=True)
        toolbar_icons.apply_feature_states(app)
        self.assertEqual(app.mp3_settings_btn._toolbar_icon_key, "mp3_on")
        self.assertEqual(app.edit_redactor_btn._toolbar_icon_key, "prompts_on")
        self.assertEqual(app.telegram_btn._toolbar_icon_key, "telegram_on")
        self.assertEqual(app.export_md_docx_btn._toolbar_icon_key, "docx_on")
        self.assertEqual(app.clear_log_btn._toolbar_icon_key, "clear_log")
        self.assertEqual(app.dependencies_btn._toolbar_icon_key, "dependencies")
        self.assertEqual(app.autostart_btn._toolbar_icon_key, "autostart_off")
        self.assertEqual(app.cancel_btn._toolbar_icon_key, "cancel")
        self.assertEqual(app.start_btn._toolbar_icon_key, "start_transcription")
        self.assertEqual(app.start_btn.cget("text"), "")
        self.assertEqual(app.add_files_btn.cget("text"), "")
        self.assertEqual(app.play_sound_btn.cget("text"), "")
        self.assertEqual(app.output_folder_btn.cget("text"), "")
        self.assertEqual(app.export_md_docx_btn.cget("text"), "")
        self.assertEqual(app.clear_log_btn.cget("text"), "")
        self.assertEqual(app.cancel_btn.cget("text"), "")

        app.play_sound_on_finish.set(False)
        toolbar_icons.apply_notify_state(app)
        self.assertEqual(app.play_sound_btn._toolbar_icon_key, "notify_off")

        app.autostart_enabled.set(True)
        toolbar_icons.apply_autostart_state(app)
        self.assertEqual(app.autostart_btn._toolbar_icon_key, "autostart_on")
        app.autostart_enabled.set(False)
        toolbar_icons.apply_autostart_state(app)
        self.assertEqual(app.autostart_btn._toolbar_icon_key, "autostart_off")

        toolbar_icons.apply_capture_state(app, running=False, paused=False)
        self.assertEqual(app.capture_btn._toolbar_icon_key, "capture_start")
        self.assertEqual(app.capture_pause_btn._toolbar_icon_key, "capture_pause")
        self.assertEqual(app.capture_btn.cget("style"), "TButton")
        toolbar_icons.apply_capture_state(app, running=True, paused=True)
        self.assertEqual(app.capture_btn._toolbar_icon_key, "capture_stop")
        self.assertEqual(app.capture_pause_btn._toolbar_icon_key, "capture_resume")
        self.assertEqual(app.capture_btn.cget("text"), "")
        self.assertEqual(app.capture_btn.cget("style"), toolbar_icons.CAPTURE_ON_STYLE)
        toolbar_icons.apply_capture_state(app, running=False, paused=False)
        self.assertEqual(app.capture_btn.cget("style"), "TButton")

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
