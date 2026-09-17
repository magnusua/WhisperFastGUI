"""Parsing of redactor1.md prompts and safe prompt filenames."""
import os
import tempfile
import unittest

from whisperfast.postprocess.cursor_postprocess import (
    default_checked_prompt_nums,
    parse_redactor_prompts,
    sanitize_prompt_filename,
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


class TestParseRedactorPrompts(unittest.TestCase):
    def test_missing_file(self):
        self.assertEqual(parse_redactor_prompts(os.path.join("no", "such", "redactor1.md")), [])

    def test_parses_uk_and_en_headers_skips_empty_sorts_by_number(self):
        content = (
            "# Title\n"
            "\n"
            "## Prompt #2 \"summary\"\n"
            "\n"
            "Add a title.\n"
            "\n"
            "## Промпт №1 \"redactor\"\n"
            "\n"
            "Clean up the transcript.\n"
            "\n"
            "## Промпт №3 \"skipped\"\n"
            "\n"
            "## Prompt #4 'quoted'\n"
            "\n"
            "Last section.\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "redactor1.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            prompts = parse_redactor_prompts(path)
        self.assertEqual(
            [(n, name) for n, name, _text in prompts],
            [(1, "redactor"), (2, "summary"), (4, "quoted")],
        )
        self.assertIn("Clean up", prompts[0][2])
        self.assertIn("Add a title", prompts[1][2])
        self.assertIn("Last section", prompts[2][2])

    def test_unquoted_name_is_empty(self):
        content = "## Промпт №1\n\nBody text.\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "redactor1.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            prompts = parse_redactor_prompts(path)
        self.assertEqual(len(prompts), 1)
        self.assertEqual(prompts[0][0], 1)
        self.assertEqual(prompts[0][1], "")
        self.assertEqual(prompts[0][2], "Body text.")


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
