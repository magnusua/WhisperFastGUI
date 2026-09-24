"""Pure helpers from whisperfast.utils — timestamps and queue items."""
import os
import unittest
from unittest.mock import patch

from whisperfast.config import DEFAULT_START_TIMESTAMP, QUEUE_ITEM_KEYS
from whisperfast.utils import (
    format_timestamp,
    make_queue_item,
    normalize_queue_note,
    normalize_queue_path,
    parse_timestamp_to_seconds,
    queue_tree_values,
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
        self.assertEqual(item["note"], "")
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

    def test_note_is_normalized(self):
        with patch("whisperfast.utils.get_audio_duration_seconds", return_value=0.0):
            item = make_queue_item("clip.mp3", note="  hello   world  \n")
        self.assertEqual(item["note"], "hello world")
        self.assertEqual(normalize_queue_note("  a\tb  "), "a b")
        self.assertEqual(len(normalize_queue_note("x" * 250)), 200)

    def test_queue_tree_values_puts_note_after_filename(self):
        with patch("whisperfast.utils.get_audio_duration_seconds", return_value=0.0):
            item = make_queue_item("folder/talk.mp4", note="sales call")
        values = queue_tree_values(3, item)
        self.assertEqual(values[0], 3)
        self.assertEqual(values[1], "×")
        self.assertEqual(values[2], "talk.mp4")
        self.assertEqual(values[3], "sales call")
        self.assertEqual(values[4], "")
        self.assertEqual(values[-1], "⏳")
        item["processed"] = True
        self.assertEqual(queue_tree_values(3, item)[4], "▶")
        self.assertEqual(queue_tree_values(3, item)[5], "➤")
        self.assertEqual(queue_tree_values(3, item)[-1], "✓")
        item["ai_done"] = True
        self.assertEqual(queue_tree_values(3, item)[-1], "AI")
        item["tg_sent"] = True
        self.assertEqual(queue_tree_values(3, item)[-1], "AI + TG")
        item["error"] = "disk full"
        self.assertEqual(queue_tree_values(3, item)[-1], "disk full")


class TestLogFileNoteHeader(unittest.TestCase):
    def test_header_keeps_name_and_status_outside_the_cell(self):
        from whisperfast.ui.log_panel import LogPanel

        prefix, suffix = LogPanel._format_file_header_parts(
            {
                "name": "Video.mp4",
                "status": "done",
                "note": "sales call",
                "index": {"current": 1, "total": 1},
            },
            True,
        )
        self.assertIn("Video.mp4", prefix)
        self.assertNotIn("sales call", prefix)
        self.assertNotIn("sales call", suffix)
        self.assertIn("[", suffix)

    def test_header_line_has_no_inline_note(self):
        from whisperfast.ui.log_panel import LogPanel

        line = LogPanel._format_file_header_line(
            {"name": "Video.mp4", "status": "done", "note": "sales call"},
            True,
        )
        self.assertIn("Video.mp4", line)
        self.assertNotIn("sales call", line)

    def test_embedded_note_cell_is_an_entry(self):
        import tkinter as tk
        import tempfile

        from whisperfast.log_store import LogStore
        from whisperfast.ui.log_panel import LogPanel

        try:
            root = tk.Tk()
        except tk.TclError as e:
            raise unittest.SkipTest(f"no Tk display available: {e}")
        root.withdraw()
        self.addCleanup(root.destroy)
        with tempfile.TemporaryDirectory() as tmp:
            panel = LogPanel(root)
            panel._store = LogStore(path=os.path.join(tmp, "app_log.json"))
            box = tk.Text(root)
            panel.bind_widget(box)
            panel.setup_styles()
            file_id = panel.begin_file(os.path.join(tmp, "a.mp4"), name="a.mp4")
            root.update()
            widget = panel._file_note_widgets.get(file_id)
            self.assertIsInstance(widget, tk.Entry)
            self.assertGreaterEqual(int(widget.cget("width")), 20)
            panel.log_file_segment("0:01", "hello", count=1, file_id=file_id)
            root.update()
            panel.log_file_segment("0:02", "world", count=12, file_id=file_id)
            root.update()
            panel.end_file(status="done", file_id=file_id)
            root.update()
            text = box.get("1.0", "end")
            self.assertEqual(text.count("a.mp4"), 1)
            self.assertEqual(text.count("▶"), 1)
            windows = [item for item in box.dump("1.0", "end", window=True) if item[0] == "window"]
            self.assertEqual(len(windows), 1)
            self.assertEqual(len(panel._file_note_widgets), 1)

    def test_prompt_button_shows_when_txt_output_exists(self):
        import tkinter as tk
        import tempfile

        from whisperfast.i18n import t
        from whisperfast.log_store import LogStore
        from whisperfast.ui.log_panel import LogPanel

        try:
            root = tk.Tk()
        except tk.TclError as e:
            raise unittest.SkipTest(f"no Tk display available: {e}")
        root.withdraw()
        self.addCleanup(root.destroy)
        with tempfile.TemporaryDirectory() as tmp:
            panel = LogPanel(root)
            panel._store = LogStore(path=os.path.join(tmp, "app_log.json"))
            box = tk.Text(root)
            panel.bind_widget(box)
            panel.setup_styles()
            media = os.path.join(tmp, "a.mp4")
            txt = os.path.join(tmp, "a.txt")
            with open(txt, "w", encoding="utf-8") as f:
                f.write("hi")
            file_id = panel.begin_file(media, name="a.mp4")
            root.update()
            panel.add_file_output("txt", txt, file_id=file_id)
            root.update()
            self.assertFalse(panel._file_action_callbacks)
            self.assertIn(t("log_file_select_prompt_btn"), box.get("1.0", "end"))
            clicked = []
            panel.on_select_prompts = lambda fid: clicked.append(fid)
            # Tag on the prompt line is file_action_{id}; invoke the same branch as a click.
            ranges = box.tag_ranges(f"file_action_{file_id}")
            self.assertGreaterEqual(len(ranges), 2)
            bbox = box.bbox(ranges[0])
            if bbox:
                event = type("E", (), {"x": bbox[0] + 2, "y": bbox[1] + 2})()
                panel.on_action_click(event)
                self.assertEqual(clicked, [file_id])


if __name__ == "__main__":
    unittest.main()
