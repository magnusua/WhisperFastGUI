"""whisperfast.core.capture_prefs: capture/meeting settings defaults and helpers."""
import unittest

from whisperfast.core.capture_prefs import (
    AUTO_RECORD_PRESETS,
    CAPTURE_DEFAULTS,
    CODEC_EXTENSIONS,
    capture_defaults,
    codec_extension,
    enabled_auto_apps,
    snapshot_capture_settings,
)


class TestCaptureDefaults(unittest.TestCase):
    def test_returns_a_copy_not_the_shared_dict(self):
        d1 = capture_defaults()
        d1["user_name"] = "mutated"
        d2 = capture_defaults()
        self.assertEqual(d2["user_name"], "You")
        self.assertIsNot(d1, CAPTURE_DEFAULTS)

    def test_has_an_entry_for_every_auto_record_preset(self):
        d = capture_defaults()
        for key in AUTO_RECORD_PRESETS:
            self.assertIn(f"auto_record_{key}", d)


class TestEnabledAutoApps(unittest.TestCase):
    def test_no_apps_enabled_by_default(self):
        self.assertEqual(enabled_auto_apps(capture_defaults()), [])

    def test_only_enabled_presets_are_returned_in_preset_order(self):
        settings = capture_defaults()
        settings["auto_record_whatsapp"] = True
        settings["auto_record_zoom"] = True
        result = enabled_auto_apps(settings)
        self.assertEqual(result, ["zoom", "whatsapp"])  # preset order, not insertion order

    def test_missing_keys_are_treated_as_disabled(self):
        self.assertEqual(enabled_auto_apps({}), [])


class TestCodecExtension(unittest.TestCase):
    def test_known_codecs(self):
        for codec, ext in CODEC_EXTENSIONS.items():
            self.assertEqual(codec_extension(codec), ext)

    def test_unknown_codec_falls_back_to_opus(self):
        self.assertEqual(codec_extension("flac"), ".opus")
        self.assertEqual(codec_extension(""), ".opus")
        self.assertEqual(codec_extension(None), ".opus")

    def test_case_and_whitespace_insensitive(self):
        self.assertEqual(codec_extension("  MP3  "), ".mp3")
        self.assertEqual(codec_extension("Aac"), ".m4a")


class TestSnapshotCaptureSettings(unittest.TestCase):
    def test_fills_in_missing_keys_with_defaults(self):
        snap = snapshot_capture_settings({})
        self.assertEqual(snap, capture_defaults())

    def test_overrides_only_known_keys(self):
        settings = {
            "user_name": "Alice",
            "capture_codec": "mp3",
            "unrelated_setting": "ignored",
            "whisper_model": "tiny",  # not a capture key, must not leak in
        }
        snap = snapshot_capture_settings(settings)
        self.assertEqual(snap["user_name"], "Alice")
        self.assertEqual(snap["capture_codec"], "mp3")
        self.assertNotIn("unrelated_setting", snap)
        self.assertNotIn("whisper_model", snap)
        # Everything else keeps its default.
        self.assertEqual(snap["them_name"], "Them")

    def test_does_not_mutate_input_settings(self):
        settings = {"user_name": "Alice"}
        snapshot_capture_settings(settings)
        self.assertEqual(settings, {"user_name": "Alice"})


if __name__ == "__main__":
    unittest.main()
