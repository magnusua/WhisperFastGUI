"""settings.json load/save round-trip and send_txt_to_cursor → send_txt_to_ai migration."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from whisperfast.settings import (
    default_settings,
    load_app_settings,
    normalize_default_prompt_nums,
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


class TestDefaultPromptNums(SettingsTmpTestCase):
    def test_normalize_invalid_type_falls_back_to_first(self):
        self.assertEqual(normalize_default_prompt_nums(None), [1])
        self.assertEqual(normalize_default_prompt_nums("1"), [1])
        self.assertEqual(normalize_default_prompt_nums(1), [1])

    def test_normalize_keeps_empty_and_unique_positive(self):
        self.assertEqual(normalize_default_prompt_nums([]), [])
        self.assertEqual(
            normalize_default_prompt_nums([1, 1, 2, 0, -3, "x", "4"]),
            [1, 2, 4],
        )

    def test_load_fills_missing_default_prompt_nums(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"language": "EN"}, f)
        data = load_app_settings()
        self.assertEqual(data["ai_default_prompt_nums"], [1])

    def test_load_sanitizes_prompt_nums(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"ai_default_prompt_nums": [1, "2", 2, 0]}, f)
        data = load_app_settings()
        self.assertEqual(data["ai_default_prompt_nums"], [1, 2])

    def test_empty_list_is_kept(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"ai_default_prompt_nums": []}, f)
        data = load_app_settings()
        self.assertEqual(data["ai_default_prompt_nums"], [])


class TestNewSoftphoneSettings(SettingsTmpTestCase):
    def test_defaults_include_archive_flags(self):
        data = load_app_settings()
        self.assertFalse(data["export_json"])
        self.assertFalse(data["diarization_enabled"])
        self.assertEqual(data["ai_prompt_rules"], [])
        self.assertFalse(data["ai_auto_process"])
        self.assertEqual(data["ai_month_budget"], 0.0)
        self.assertEqual(data["ollama_base_url"], "http://127.0.0.1:11434")

    def test_prompt_rules_sanitized(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "ai_prompt_rules": [
                        {"match": "filename", "pattern": "*.mp3", "prompt_nums": [2, 2, 0], "skip_dialog": 1}
                    ]
                },
                f,
            )
        data = load_app_settings()
        self.assertEqual(data["ai_prompt_rules"][0]["prompt_nums"], [2])
        self.assertTrue(data["ai_prompt_rules"][0]["skip_dialog"])


if __name__ == "__main__":
    unittest.main()
