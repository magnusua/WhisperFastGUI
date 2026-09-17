"""SRT parser and prompt-rule matching."""
import unittest

from whisperfast.postprocess.prompt_rules import first_matching_rule, normalize_prompt_rules, rule_matches
from whisperfast.srt_parse import parse_srt
from whisperfast.core.export_transcript import write_vtt
from whisperfast.postprocess.usage import apply_usage, budget_exceeded, cost_usd, estimate_tokens


class Seg:
    def __init__(self, start, end, text, speaker=""):
        self.start = start
        self.end = end
        self.text = text
        self.speaker = speaker


class TestSrtParse(unittest.TestCase):
    def test_two_cues(self):
        text = (
            "1\n"
            "00:00:00.000 --> 00:00:01.500\n"
            "Hello\n"
            "\n"
            "2\n"
            "00:00:01,500 --> 00:00:03,000\n"
            "World\n"
        )
        cues = parse_srt(text)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0]["text"], "Hello")
        self.assertAlmostEqual(cues[1]["start"], 1.5)


class TestPromptRules(unittest.TestCase):
    def test_always_and_filename(self):
        rules = normalize_prompt_rules(
            [
                {"match": "filename", "pattern": "*.wav", "prompt_nums": [2], "skip_dialog": True},
                {"match": "always", "prompt_nums": [1], "skip_dialog": False},
            ]
        )
        self.assertEqual(len(rules), 2)
        hit = first_matching_rule(rules, r"C:\meetings\a.wav")
        self.assertEqual(hit["prompt_nums"], [2])
        self.assertTrue(rule_matches(rules[0], r"C:\meetings\a.wav"))
        self.assertFalse(rule_matches(rules[0], r"C:\meetings\a.mp3"))

    def test_invalid_dropped(self):
        self.assertEqual(normalize_prompt_rules("nope"), [])
        self.assertEqual(normalize_prompt_rules([{"match": "other"}])[0]["match"], "always")


class TestUsage(unittest.TestCase):
    def test_budget(self):
        self.assertGreater(estimate_tokens("abcd" * 10), 0)
        self.assertEqual(cost_usd("ollama", 1000, 1000), 0.0)
        s = {"ai_month_budget": 1.0, "ai_spend_month": "", "ai_spend_usd": 0.0}
        apply_usage(s, "claude", 500_000, 500_000)
        self.assertTrue(budget_exceeded(s))
        self.assertFalse(budget_exceeded({"ai_month_budget": 0, "ai_spend_usd": 99}))


class TestVtt(unittest.TestCase):
    def test_write_vtt(self, tmp_path=None):
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "a.vtt")
            write_vtt(path, [Seg(0, 1.2, "hi", "SPEAKER_00")])
            with open(path, encoding="utf-8") as f:
                text = f.read()
            self.assertIn("WEBVTT", text)
            self.assertIn("SPEAKER_00: hi", text)


if __name__ == "__main__":
    unittest.main()
