"""Prompt library: promts/*.json and safe prompt filenames."""
import json
import os
import tempfile
import unittest

from whisperfast.postprocess.cursor_postprocess import (
    default_checked_prompt_nums,
    is_one_liner_output,
    parse_redactor_prompts,
    prompt_label_from_output_path,
    sanitize_prompt_filename,
)
from whisperfast.postprocess.prompt_library import (
    edit_prompt_as_markdown,
    export_prompt_body_to_markdown,
    import_prompt_body_from_markdown,
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


class TestPromptLabelFromOutputPath(unittest.TestCase):
    def test_keeps_underscore_in_one_liner(self):
        path = os.path.join("out", "2026-09-18 16-29-13_one_liner.md")
        src = os.path.join("out", "2026-09-18 16-29-13.txt")
        self.assertEqual(prompt_label_from_output_path(path, src), "one_liner")
        self.assertTrue(is_one_liner_output(path, label="liner"))
        self.assertTrue(is_one_liner_output(path, label="one_liner"))

    def test_red_flags_and_plain_name(self):
        src = os.path.join("d", "talk.txt")
        self.assertEqual(
            prompt_label_from_output_path(os.path.join("d", "talk_red_flags.md"), src),
            "red_flags",
        )
        self.assertEqual(
            prompt_label_from_output_path(os.path.join("d", "talk_redactor.md"), src),
            "redactor",
        )
        self.assertFalse(is_one_liner_output(os.path.join("d", "talk_redactor.md"), "redactor"))


class TestPromptMarkdownRoundtrip(unittest.TestCase):
    def _write_prompt(self, folder, name="01-redactor.json"):
        path = os.path.join(folder, name)
        obj = {
            "num": 1,
            "name": "redactor",
            "hint": {"EN": "ui only", "UK": "лише UI", "RU": "только UI"},
            "body": "Clean the transcript.\nKeep speakers.",
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.write("\n")
        return path

    def test_export_then_import_updates_body_keeps_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_path = self._write_prompt(tmp)
            md_path = export_prompt_body_to_markdown(json_path)
            self.addCleanup(lambda p=md_path: os.path.isfile(p) and os.remove(p))
            with open(md_path, encoding="utf-8") as f:
                text = f.read()
            self.assertIn("Clean the transcript.", text)
            self.assertNotIn("ui only", text)
            with open(md_path, "w", encoding="utf-8") as f:
                f.write("New protocol body\n")
            self.assertTrue(import_prompt_body_from_markdown(md_path, json_path))
            with open(json_path, encoding="utf-8") as f:
                obj = json.load(f)
            self.assertEqual(obj["body"], "New protocol body")
            self.assertEqual(obj["hint"]["UK"], "лише UI")
            self.assertEqual(obj["name"], "redactor")

    def test_empty_markdown_does_not_wipe_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_path = self._write_prompt(tmp)
            md_path = os.path.join(tmp, "empty.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write("   \n")
            self.assertFalse(import_prompt_body_from_markdown(md_path, json_path))
            with open(json_path, encoding="utf-8") as f:
                obj = json.load(f)
            self.assertIn("Clean the transcript.", obj["body"])

    def test_edit_prompt_as_markdown_writes_json_and_deletes_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_path = self._write_prompt(tmp)
            seen = {}

            def _rewrite(md_path):
                seen["md"] = md_path
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write("Edited in Markdown\n")
                return True

            edit_prompt_as_markdown(
                json_path,
                background=False,
                wait_open=_rewrite,
            )
            with open(json_path, encoding="utf-8") as f:
                obj = json.load(f)
            self.assertEqual(obj["body"], "Edited in Markdown")
            self.assertEqual(obj["hint"]["EN"], "ui only")
            self.assertTrue(seen["md"])
            self.assertFalse(os.path.isfile(seen["md"]))

    def test_edit_skips_save_when_editor_does_not_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_path = self._write_prompt(tmp)
            seen = {}

            def _no_open(md_path):
                seen["md"] = md_path
                return False

            edit_prompt_as_markdown(
                json_path,
                background=False,
                wait_open=_no_open,
            )
            with open(json_path, encoding="utf-8") as f:
                obj = json.load(f)
            self.assertIn("Clean the transcript.", obj["body"])
            self.assertFalse(os.path.isfile(seen["md"]))


if __name__ == "__main__":
    unittest.main()
