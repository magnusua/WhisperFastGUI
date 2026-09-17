"""Prompt library: promts/*.json and safe prompt filenames."""
import json
import os
import tempfile
import unittest

from whisperfast.postprocess.cursor_postprocess import (
    default_checked_prompt_nums,
    parse_redactor_prompts,
    sanitize_prompt_filename,
)
from whisperfast.postprocess.prompt_library import (
    load_prompt_specs,
    load_prompt_tuples,
)


class TestSanitizePromptFilename(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(sanitize_prompt_filename(""), "")
        self.assertEqual(sanitize_prompt_filename("   "), "")
        self.assertEqual(sanitize_prompt_filename(None), "")

    def test_spaces_and_forbidden_chars(self):
        self.assertEqual(sanitize_prompt_filename("TW_core"), "TW_core")
        self.assertEqual(sanitize_prompt_filename("foo bar"), "foo_bar")
        self.assertEqual(sanitize_prompt_filename('a<>:"/\\|?*b'), "ab")

    def test_strips_dots_and_underscores(self):
        self.assertEqual(sanitize_prompt_filename("...dots..."), "dots")
        self.assertEqual(sanitize_prompt_filename("_name_"), "name")


class TestLoadPromptJson(unittest.TestCase):
    def test_missing_dir(self):
        self.assertEqual(parse_redactor_prompts(os.path.join("no", "such")), [])

    def test_loads_json_skips_empty_sorts_by_number_drops_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.join(tmp, "promts")
            os.makedirs(folder)
            files = [
                (
                    "02-summary.json",
                    {
                        "num": 2,
                        "name": "summary",
                        "hint": {"EN": "ui only", "UK": "лише UI", "RU": "только UI"},
                        "body": "Add a title.\n",
                    },
                ),
                (
                    "01-redactor.json",
                    {
                        "num": 1,
                        "name": "redactor",
                        "hint": "ignored in body",
                        "body": "Clean up the transcript.",
                    },
                ),
                (
                    "03-empty.json",
                    {"num": 3, "name": "skipped", "hint": {"EN": "no body"}, "body": "  "},
                ),
                (
                    "04-quoted.json",
                    {"num": 4, "name": "quoted", "body": "Last section."},
                ),
            ]
            for name, obj in files:
                with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
                    json.dump(obj, f)
            prompts = parse_redactor_prompts(folder)
            specs = load_prompt_specs(tmp)
            self.assertEqual(load_prompt_tuples(tmp), prompts)
        self.assertEqual(
            [(n, name) for n, name, _text in prompts],
            [(1, "redactor"), (2, "summary"), (4, "quoted")],
        )
        self.assertIn("Clean up", prompts[0][2])
        self.assertNotIn("ignored", prompts[0][2])
        self.assertNotIn("ui only", prompts[1][2])
        self.assertIn("Last section", prompts[2][2])
        self.assertEqual(specs[1].hint_for("UK"), "лише UI")

    def test_shipped_library_has_bodies_and_hints(self):
        specs = load_prompt_specs()
        self.assertGreaterEqual(len(specs), 10)
        for spec in specs:
            self.assertTrue(spec.body.strip())
            self.assertNotIn('"hint"', spec.body[:40])
            self.assertTrue(spec.hint_for("EN") or spec.hint_for("UK") or spec.hint_for("RU"))


class TestDefaultCheckedPromptNums(unittest.TestCase):
    prompts = [(1, "a", "x"), (2, "b", "y"), (4, "c", "z")]

    def test_none_uses_first(self):
        self.assertEqual(default_checked_prompt_nums(self.prompts, None), {1})

    def test_empty_means_none(self):
        self.assertEqual(default_checked_prompt_nums(self.prompts, []), set())

    def test_filters_to_existing(self):
        self.assertEqual(default_checked_prompt_nums(self.prompts, [2, 4, 9]), {2, 4})

    def test_unmatched_falls_back_to_first(self):
        self.assertEqual(default_checked_prompt_nums(self.prompts, [99]), {1})

    def test_empty_prompts(self):
        self.assertEqual(default_checked_prompt_nums([], [1]), set())


if __name__ == "__main__":
    unittest.main()
