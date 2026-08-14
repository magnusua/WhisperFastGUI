"""Pure helpers from whisperfast.utils — timestamps and queue items."""
import os
import unittest
from unittest.mock import patch

from whisperfast.config import DEFAULT_START_TIMESTAMP, QUEUE_ITEM_KEYS
from whisperfast.utils import (
    format_timestamp,
    make_queue_item,
    normalize_queue_path,
    parse_timestamp_to_seconds,
)


class TestFormatTimestamp(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(format_timestamp(0), "00:00:00,000")
        self.assertEqual(format_timestamp(0.0), "00:00:00,000")

    def test_hours_minutes_seconds_ms(self):
        self.assertEqual(format_timestamp(3661.5), "01:01:01,500")

    def test_half_second(self):
        self.assertEqual(format_timestamp(0.5), "00:00:00,500")


class TestParseTimestampToSeconds(unittest.TestCase):
    def test_comma_and_dot_ms(self):
        self.assertEqual(parse_timestamp_to_seconds("00:00:00,000"), 0.0)
        self.assertEqual(parse_timestamp_to_seconds("01:01:01,500"), 3661.5)
        self.assertEqual(parse_timestamp_to_seconds("00:00:01.250"), 1.25)

    def test_without_ms(self):
        self.assertEqual(parse_timestamp_to_seconds("01:02:03"), 3723.0)

    def test_empty_and_invalid(self):
        self.assertIsNone(parse_timestamp_to_seconds(None))
        self.assertIsNone(parse_timestamp_to_seconds(""))
        self.assertIsNone(parse_timestamp_to_seconds("  "))
        self.assertIsNone(parse_timestamp_to_seconds("bad"))
        self.assertIsNone(parse_timestamp_to_seconds("1:2"))
        self.assertIsNone(parse_timestamp_to_seconds("00:60:00"))
        self.assertIsNone(parse_timestamp_to_seconds("00:00:60"))
        self.assertIsNone(parse_timestamp_to_seconds("-1:00:00"))

    def test_round_trip_with_format(self):
        seconds = 3661.5
        self.assertEqual(parse_timestamp_to_seconds(format_timestamp(seconds)), seconds)


class TestNormalizeQueuePath(unittest.TestCase):
    def test_none_and_empty(self):
        self.assertIsNone(normalize_queue_path(None))
        self.assertIsNone(normalize_queue_path(""))
        self.assertIsNone(normalize_queue_path("   "))
        self.assertIsNone(normalize_queue_path([]))
        self.assertIsNone(normalize_queue_path(123))

    def test_string_is_normpath(self):
        raw = "folder/file.mp3"
        self.assertEqual(normalize_queue_path(raw), os.path.normpath(raw))
        self.assertEqual(normalize_queue_path("  a/b  "), os.path.normpath("a/b"))

    def test_list_or_tuple_uses_first_element(self):
        raw = "folder/file.mp3"
        self.assertEqual(normalize_queue_path([raw]), os.path.normpath(raw))
        self.assertEqual(normalize_queue_path((raw, "ignored")), os.path.normpath(raw))


class TestMakeQueueItem(unittest.TestCase):
    def test_defaults_and_keys(self):
        with patch("whisperfast.utils.get_audio_duration_seconds", return_value=0.0):
            item = make_queue_item(os.path.normpath("clip.mp3"))
        self.assertEqual(tuple(item), QUEUE_ITEM_KEYS)
        self.assertEqual(item["path"], os.path.normpath("clip.mp3"))
        self.assertEqual(item["start"], DEFAULT_START_TIMESTAMP)
        self.assertEqual(item["end"], DEFAULT_START_TIMESTAMP)
        self.assertEqual(item["end_segment_1"], "")
        self.assertEqual(item["end_segment_2"], "")
        self.assertFalse(item["processed"])

    def test_duration_sets_end_timestamp(self):
        with patch("whisperfast.utils.get_audio_duration_seconds", return_value=90.0):
            item = make_queue_item("clip.mp3")
        self.assertEqual(item["end"], format_timestamp(90.0))

    def test_overrides(self):
        with patch("whisperfast.utils.get_audio_duration_seconds", return_value=10.0):
            item = make_queue_item(
                "clip.mp3",
                start="00:00:01,000",
                processed=True,
            )
        self.assertEqual(item["start"], "00:00:01,000")
        self.assertTrue(item["processed"])
        self.assertEqual(item["end"], format_timestamp(10.0))


if __name__ == "__main__":
    unittest.main()
