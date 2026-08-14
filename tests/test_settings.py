"""settings.json load/save round-trip and send_txt_to_cursor → send_txt_to_ai migration."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from whisperfast.settings import (
    default_settings,
    load_app_settings,
    save_app_settings,
)


class SettingsTmpTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "settings.json")
        self.patcher = patch("whisperfast.settings.settings_path", return_value=self.path)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self._tmp.cleanup()


class TestLoadAppSettings(SettingsTmpTestCase):
    def test_creates_defaults_when_missing(self):
        data = load_app_settings()
        self.assertTrue(os.path.isfile(self.path))
        defaults = default_settings()
        self.assertEqual(data["whisper_model"], defaults["whisper_model"])
        self.assertIn("send_txt_to_ai", data)
        self.assertFalse(data["send_txt_to_ai"])
        self.assertFalse(data["send_txt_to_cursor"])

    def test_fills_missing_keys_from_defaults(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"language": "UK"}, f)
        data = load_app_settings()
        self.assertEqual(data["language"], "UK")
        self.assertEqual(data["output_mode"], default_settings()["output_mode"])

    def test_wrong_types_fall_back_to_defaults_without_dropping_valid_fields(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "language": "UK",
                    "output_mode": 123,
                    "watch_enabled": 1,
                    "play_sound_on_finish": True,
                },
                f,
            )
        data = load_app_settings()
        self.assertEqual(data["language"], "UK")
        self.assertEqual(data["output_mode"], default_settings()["output_mode"])
        self.assertIsInstance(data["output_mode"], str)
        self.assertFalse(data["watch_enabled"])
        self.assertTrue(data["play_sound_on_finish"])

    def test_non_object_json_returns_defaults(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(["not", "a", "dict"], f)
        data = load_app_settings()
        self.assertEqual(data, default_settings())
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{not json")
        data = load_app_settings()
        self.assertEqual(data, default_settings())


class TestLegacyAiFlagMigration(SettingsTmpTestCase):
    def test_send_txt_to_cursor_migrates_to_send_txt_to_ai(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"send_txt_to_cursor": True}, f)
        data = load_app_settings()
        self.assertTrue(data["send_txt_to_ai"])
        self.assertTrue(data["send_txt_to_cursor"])

    def test_send_txt_to_ai_wins_and_alias_is_synced(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"send_txt_to_ai": False, "send_txt_to_cursor": True}, f)
        data = load_app_settings()
        self.assertFalse(data["send_txt_to_ai"])
        self.assertFalse(data["send_txt_to_cursor"])


class TestSaveLoadRoundTrip(SettingsTmpTestCase):
    def test_round_trip_preserves_values(self):
        load_app_settings()
        save_app_settings(
            {
                "language": "RU",
                "send_txt_to_ai": True,
                "send_txt_to_cursor": True,
                "output_dir": os.path.join("out", "folder"),
            }
        )
        data = load_app_settings()
        self.assertEqual(data["language"], "RU")
        self.assertTrue(data["send_txt_to_ai"])
        self.assertTrue(data["send_txt_to_cursor"])
        self.assertEqual(data["output_dir"], os.path.join("out", "folder"))


if __name__ == "__main__":
    unittest.main()
