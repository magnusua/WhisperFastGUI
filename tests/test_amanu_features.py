"""Speakers, filename tokens, ICS, auto-record machine, echo filter, clips (no hardware)."""
import os
import tempfile
import unittest
import wave
from datetime import datetime, timezone

from whisperfast.core.auto_record import (
    START_AUTO,
    START_CALENDAR,
    STOP_GONE,
    STOP_SILENCE,
    AutoRecordMachine,
)
from whisperfast.core.calendar import (
    event_has_meeting_url,
    filter_events,
    overlapping_event,
    parse_ics,
    upcoming_event,
)
from whisperfast.core.capture import CaptureSession
from whisperfast.core.capture_encode import mix_to_mono, normalize_codec
from whisperfast.core.capture_finalize import capture_dir_from_settings
from whisperfast.core.capture_names import format_clip_seconds, parse_clip_seconds, render_filename
from whisperfast.core.echo_filter import filter_echo_segments
from whisperfast.core.speakers import (
    SPEAKER_FAR,
    SPEAKER_NEAR,
    apply_llm_names,
    parse_llm_speaker_names,
    quote_in_transcript,
    rewrite_text_speakers,
)
from whisperfast.config import AUDIO_EXTENSIONS, VALID_EXTS


class _Seg:
    def __init__(self, text, speaker):
        self.text = text
        self.speaker = speaker
        self.start = 0
        self.end = 1


class TestSpeakerQuoteFilter(unittest.TestCase):
    def test_quote_must_appear(self):
        transcript = "hello my name is Alex and we can start"
        self.assertTrue(quote_in_transcript("my name is Alex", transcript))
        self.assertFalse(quote_in_transcript("I am Bob the builder", transcript))

    def test_llm_high_with_quote_applies(self):
        speakers = {
            SPEAKER_NEAR: {"id": SPEAKER_NEAR, "display": "You", "source": "default"},
        }
        text = '{"SPEAKER_00": {"name": "Alex", "confidence": "high", "quote": "my name is Alex"}}'
        cands = parse_llm_speaker_names(text)
        out, skipped = apply_llm_names(speakers, cands, "hello my name is Alex")
        self.assertEqual(out[SPEAKER_NEAR]["display"], "Alex")
        self.assertEqual(skipped, [])

    def test_llm_rejects_missing_quote_and_manual(self):
        speakers = {
            SPEAKER_NEAR: {"id": SPEAKER_NEAR, "display": "Pat", "source": "manual"},
        }
        cands = parse_llm_speaker_names(
            'SPEAKER_00: Alex ("not in file") high\nSPEAKER_01: Sam ("hello there") high'
        )
        out, skipped = apply_llm_names(speakers, cands, "hello there everyone")
        self.assertEqual(out[SPEAKER_NEAR]["display"], "Pat")
        self.assertIn(SPEAKER_NEAR, skipped)
        self.assertEqual(out[SPEAKER_FAR]["display"], "Sam")

    def test_rewrite_labels(self):
        body = "You: hi\nThem: hello\n"
        self.assertIn("Alex:", rewrite_text_speakers(body, {"You": "Alex"}))


class TestFilenameAndClip(unittest.TestCase):
    def test_parse_format_clip(self):
        self.assertEqual(parse_clip_seconds("2:02"), 122)
        self.assertEqual(parse_clip_seconds("122"), 122)
        self.assertEqual(parse_clip_seconds("5"), 5)
        self.assertEqual(format_clip_seconds(122), "2:02")
        self.assertEqual(format_clip_seconds(5), "0:05")

    def test_tokens_and_calendar(self):
        when = datetime(2026, 9, 17, 14, 5, 9)
        name = render_filename("%W %Y-%m-%d %H.%M.%S", when, window_title="Zoom Meeting*")
        self.assertIn("2026-09-17", name)
        self.assertIn("14.05.09", name)
        self.assertNotIn("*", name)
        cal = render_filename(
            "%C %Y-%m-%d %H.%M.%S",
            when,
            calendar_title="Standup / Q3",
            use_calendar=True,
        )
        self.assertTrue(cal.startswith("Standup"))
        unknown = render_filename("%W %Q", when, window_title="FTW")
        self.assertIn("%Q", unknown)

    def test_use_calendar_fallback_without_event(self):
        when = datetime(2026, 9, 17, 8, 0, 0)
        name = render_filename(
            "%C %Y-%m-%d %H.%M.%S",
            when,
            window_title="FTW",
            calendar_title="",
            use_calendar=True,
        )
        self.assertTrue(name.startswith("FTW"))


class TestCalendarIcs(unittest.TestCase):
    ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART:20260917T120000Z
DTEND:20260917T130000Z
SUMMARY:Standup
LOCATION:https://meet.google.com/abc
ATTENDEE:mailto:a@example.com
END:VEVENT
END:VCALENDAR
"""

    def test_parse_and_overlap(self):
        events = parse_ics(self.ICS)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "Standup")
        self.assertTrue(event_has_meeting_url(events[0]))
        when = datetime(2026, 9, 17, 12, 30, tzinfo=timezone.utc)
        hit = overlapping_event(events, when)
        self.assertIsNotNone(hit)
        soon = datetime(2026, 9, 17, 11, 59, tzinfo=timezone.utc)
        self.assertIsNotNone(upcoming_event(events, soon, lead_min=2))

    def test_gcal_filters(self):
        events = parse_ics(self.ICS)
        self.assertEqual(len(filter_events(events, "meeting_url")), 1)
        self.assertEqual(len(filter_events(events, "keywords", "nope")), 0)
        self.assertEqual(len(filter_events(events, "keywords", "stand")), 1)


class TestAutoRecordMachine(unittest.TestCase):
    def test_manual_ignored(self):
        m = AutoRecordMachine()
        action = m.tick(
            now=10,
            auto_enabled=True,
            match={"app": "zoom"},
            calendar_event=None,
            recording=True,
            trigger="manual",
            elapsed_s=60,
            last_sound_age_s=30,
            start_delay_s=0,
            stop_delay_s=0,
            min_duration_s=0,
            silence_stop_s=15,
            calendar_enabled=False,
        )
        self.assertEqual(action, "")

    def test_start_after_delay(self):
        m = AutoRecordMachine()
        self.assertEqual(
            m.tick(
                now=1,
                auto_enabled=True,
                match={"app": "zoom"},
                calendar_event=None,
                recording=False,
                trigger="",
                elapsed_s=0,
                last_sound_age_s=0,
                start_delay_s=3,
                stop_delay_s=2,
                min_duration_s=5,
                silence_stop_s=15,
                calendar_enabled=False,
            ),
            "",
        )
        self.assertEqual(
            m.tick(
                now=5,
                auto_enabled=True,
                match={"app": "zoom"},
                calendar_event=None,
                recording=False,
                trigger="",
                elapsed_s=0,
                last_sound_age_s=0,
                start_delay_s=3,
                stop_delay_s=2,
                min_duration_s=5,
                silence_stop_s=15,
                calendar_enabled=False,
            ),
            START_AUTO,
        )

    def test_calendar_start(self):
        m = AutoRecordMachine()
        self.assertEqual(
            m.tick(
                now=1,
                auto_enabled=False,
                match=None,
                calendar_event={"title": "Sync"},
                recording=False,
                trigger="",
                elapsed_s=0,
                last_sound_age_s=0,
                start_delay_s=0,
                stop_delay_s=0,
                min_duration_s=0,
                silence_stop_s=0,
                calendar_enabled=True,
            ),
            START_CALENDAR,
        )

    def test_stop_when_gone_after_min_duration(self):
        m = AutoRecordMachine()
        self.assertEqual(
            m.tick(
                now=20,
                auto_enabled=True,
                match=None,
                calendar_event=None,
                recording=True,
                trigger="auto",
                elapsed_s=20,
                last_sound_age_s=1,
                start_delay_s=0,
                stop_delay_s=2,
                min_duration_s=15,
                silence_stop_s=0,
                calendar_enabled=False,
            ),
            "",
        )
        self.assertEqual(
            m.tick(
                now=23,
                auto_enabled=True,
                match=None,
                calendar_event=None,
                recording=True,
                trigger="auto",
                elapsed_s=23,
                last_sound_age_s=1,
                start_delay_s=0,
                stop_delay_s=2,
                min_duration_s=15,
                silence_stop_s=0,
                calendar_enabled=False,
            ),
            STOP_GONE,
        )

    def test_silence_15s_auto_only(self):
        m = AutoRecordMachine()
        self.assertEqual(
            m.tick(
                now=40,
                auto_enabled=True,
                match={"app": "zoom"},
                calendar_event=None,
                recording=True,
                trigger="auto",
                elapsed_s=40,
                last_sound_age_s=15,
                start_delay_s=0,
                stop_delay_s=8,
                min_duration_s=10,
                silence_stop_s=15,
                calendar_enabled=False,
            ),
            STOP_SILENCE,
        )


class TestEchoAndCodec(unittest.TestCase):
    def test_echo_drops_exact_far_dup(self):
        segs = [
            _Seg("hello world", "SPEAKER_00"),
            _Seg("hello world", "SPEAKER_01"),
            _Seg("new line", "SPEAKER_01"),
        ]
        out = filter_echo_segments(segs)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[1].text, "new line")

    def test_opus_in_extensions(self):
        self.assertIn(".opus", AUDIO_EXTENSIONS)
        self.assertIn(".opus", VALID_EXTS)
        self.assertEqual(normalize_codec("m4a"), "aac")

    def test_mono_mix(self):
        try:
            from pydub import AudioSegment
            from pydub.generators import Sine
        except ImportError:
            self.skipTest("pydub missing")
        stereo = Sine(440).to_audio_segment(duration=50).set_channels(2)
        mono = mix_to_mono(stereo)
        self.assertEqual(mono.channels, 1)


class TestCaptureClipCopy(unittest.TestCase):
    def test_copy_last_seconds(self):
        session = CaptureSession()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "capture_x.wav")
            pcm = b"\x00\x00\x01\x00" * 48000  # 1s stereo 48k would be 48000 frames; here 48000 frames at 8k
            with wave.open(path, "wb") as wf:
                wf.setnchannels(2)
                wf.setsampwidth(2)
                wf.setframerate(8000)
                wf.writeframes(pcm)
            session._path = path
            session._samplerate = 8000
            dest = os.path.join(tmp, "clip.wav")
            out = session.copy_last_seconds(0.5, dest)
            self.assertTrue(os.path.isfile(out))
            with wave.open(out, "rb") as wf:
                self.assertEqual(wf.getnchannels(), 2)
                self.assertGreater(wf.getnframes(), 0)


class TestDateSubdir(unittest.TestCase):
    def test_auto_date_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = capture_dir_from_settings(
                {"capture_dir": tmp, "capture_auto_date_subdir": True},
                auto=True,
            )
            self.assertTrue(path.startswith(tmp))
            self.assertRegex(os.path.basename(path), r"^\d{4}-\d{2}-\d{2}$")
            manual = capture_dir_from_settings(
                {"capture_dir": tmp, "capture_auto_date_subdir": True},
                auto=False,
            )
            self.assertEqual(os.path.normpath(manual), os.path.normpath(tmp))


if __name__ == "__main__":
    unittest.main()
