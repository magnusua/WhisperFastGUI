"""Speakers, filename tokens, ICS, auto-record machine, echo filter, clips (no hardware)."""
import io
import json
import os
import tempfile
import threading
import time
import unittest
import wave
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs

from whisperfast.core.auto_record import (
    START_AUTO,
    START_CALENDAR,
    STOP_GONE,
    STOP_SILENCE,
    AutoRecordMachine,
)
from whisperfast.core.calendar import (
    CLIENT_ID_ENV,
    GoogleLoopbackAuth,
    clear_google_session,
    event_has_meeting_url,
    extract_oauth_code_from_url,
    filter_events,
    google_auth_url,
    make_pkce,
    overlapping_event,
    parse_ics,
    resolve_google_client_id,
    store_google_session,
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


class TestGoogleBrowserOAuth(unittest.TestCase):
    def test_pkce_s256(self):
        import base64
        import hashlib

        verifier, challenge = make_pkce()
        self.assertGreater(len(verifier), 40)
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        self.assertEqual(challenge, expected)
        other_v, other_c = make_pkce()
        self.assertNotEqual(verifier, other_v)
        self.assertNotEqual(challenge, other_c)

    def test_auth_url_includes_pkce(self):
        url = google_auth_url(
            "cid.apps.googleusercontent.com",
            "http://127.0.0.1:9/",
            code_challenge="abc_challenge",
        )
        self.assertIn("code_challenge=abc_challenge", url)
        self.assertIn("code_challenge_method=S256", url)
        self.assertIn("access_type=offline", url)
        self.assertIn("calendar.readonly", url)
        self.assertIn("userinfo.email", url)
        bare = google_auth_url("cid", "http://127.0.0.1:9/")
        self.assertNotIn("code_challenge", bare)

    def test_extract_code_and_client_id(self):
        self.assertEqual(
            extract_oauth_code_from_url("http://127.0.0.1:1234/?code=XYZ&state=s"),
            "XYZ",
        )
        self.assertEqual(extract_oauth_code_from_url("raw-code"), "raw-code")
        self.assertEqual(extract_oauth_code_from_url(""), "")
        self.assertEqual(resolve_google_client_id("from-settings"), "from-settings")
        old = os.environ.get(CLIENT_ID_ENV)
        os.environ[CLIENT_ID_ENV] = "from-env"
        try:
            self.assertEqual(resolve_google_client_id(""), "from-env")
            self.assertEqual(resolve_google_client_id("  settings-id  "), "settings-id")
        finally:
            if old is None:
                os.environ.pop(CLIENT_ID_ENV, None)
            else:
                os.environ[CLIENT_ID_ENV] = old

    def test_loopback_binds_localhost(self):
        session = GoogleLoopbackAuth("cid.apps.googleusercontent.com")
        try:
            self.assertTrue(session.redirect_uri.startswith("http://127.0.0.1:"))
            self.assertIn("code_challenge=", session.url)
            self.assertIn(session.challenge, session.url)
            self.assertGreater(session.port, 0)
        finally:
            session.close()

    def test_store_and_clear_session(self):
        settings = {}
        store_google_session(settings, "refresh-token", "user@example.com")
        self.assertTrue(settings.get("google_calendar_refresh_token"))
        self.assertEqual(settings.get("google_calendar_email"), "user@example.com")
        clear_google_session(settings)
        self.assertEqual(settings.get("google_calendar_refresh_token"), "")
        self.assertEqual(settings.get("google_calendar_email"), "")


class _FakeUrlopenResponse:
    """Minimal stand-in for the context manager urlopen() returns."""

    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestGoogleTokenExchange(unittest.TestCase):
    """whisperfast.core.google_oauth: token exchange/refresh/email, incl. error parsing."""

    def test_exchange_google_code_posts_expected_fields(self):
        from whisperfast.core import google_oauth

        captured = {}

        def fake_urlopen(req, timeout=20):
            captured["url"] = req.full_url
            captured["body"] = req.data
            return _FakeUrlopenResponse(json.dumps({"access_token": "tok123"}).encode())

        with patch.object(google_oauth, "urlopen", side_effect=fake_urlopen):
            token = google_oauth.exchange_google_code(
                "cid", "secret", "authcode", "http://127.0.0.1:9/", code_verifier="verifier123"
            )
        self.assertEqual(token.get("access_token"), "tok123")
        self.assertEqual(captured["url"], google_oauth.GOOGLE_TOKEN_URL)
        posted = parse_qs(captured["body"].decode("utf-8"))
        self.assertEqual(posted["code"], ["authcode"])
        self.assertEqual(posted["client_id"], ["cid"])
        self.assertEqual(posted["client_secret"], ["secret"])
        self.assertEqual(posted["code_verifier"], ["verifier123"])
        self.assertEqual(posted["grant_type"], ["authorization_code"])

    def test_refresh_google_token_posts_refresh_grant(self):
        from whisperfast.core import google_oauth

        captured = {}

        def fake_urlopen(req, timeout=20):
            captured["body"] = req.data
            return _FakeUrlopenResponse(json.dumps({"access_token": "newtok"}).encode())

        with patch.object(google_oauth, "urlopen", side_effect=fake_urlopen):
            token = google_oauth.refresh_google_token("cid", "", "refresh-abc")
        self.assertEqual(token.get("access_token"), "newtok")
        posted = parse_qs(captured["body"].decode("utf-8"))
        self.assertEqual(posted["grant_type"], ["refresh_token"])
        self.assertEqual(posted["refresh_token"], ["refresh-abc"])
        self.assertNotIn("client_secret", posted)

    def test_token_request_http_error_extracts_description(self):
        from whisperfast.core import google_oauth

        err_body = json.dumps(
            {"error": "invalid_grant", "error_description": "Bad refresh token"}
        ).encode()
        http_err = HTTPError(
            url=google_oauth.GOOGLE_TOKEN_URL,
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=io.BytesIO(err_body),
        )
        with patch.object(google_oauth, "urlopen", side_effect=http_err):
            with self.assertRaises(RuntimeError) as ctx:
                google_oauth.refresh_google_token("cid", "", "bad-refresh")
        self.assertIn("Bad refresh token", str(ctx.exception))

    def test_token_request_non_http_error_falls_back_to_str(self):
        from whisperfast.core import google_oauth

        with patch.object(google_oauth, "urlopen", side_effect=OSError("network unreachable")):
            with self.assertRaises(RuntimeError) as ctx:
                google_oauth.refresh_google_token("cid", "", "refresh-abc")
        self.assertIn("network unreachable", str(ctx.exception))

    def test_fetch_google_email_success_and_failure(self):
        from whisperfast.core import google_oauth

        with patch.object(
            google_oauth,
            "urlopen",
            return_value=_FakeUrlopenResponse(json.dumps({"email": "user@example.com"}).encode()),
        ):
            self.assertEqual(google_oauth.fetch_google_email("tok"), "user@example.com")

        with patch.object(google_oauth, "urlopen", side_effect=OSError("down")):
            self.assertEqual(google_oauth.fetch_google_email("tok"), "")

        self.assertEqual(google_oauth.fetch_google_email(""), "")


def _stub_event(title, source):
    return {
        "title": title,
        "start": None,
        "end": None,
        "attendees": [],
        "location": "",
        "description": "",
        "source": source,
    }


class TestCollectEvents(unittest.TestCase):
    """whisperfast.core.calendar.collect_events(): aggregation, 60s cache, force refresh."""

    def setUp(self):
        from whisperfast.core import calendar as calendar_mod

        self.cal = calendar_mod
        self.cal._cache["ts"] = 0.0
        self.cal._cache["events"] = []
        self.cal._async_refresh_in_flight = False

    def test_aggregates_enabled_sources_and_caches_for_60s(self):
        cal = self.cal
        settings = {
            "calendar_ics_enabled": True,
            "calendar_ics_path": "ignored.ics",
            "calendar_outlook_enabled": True,
            "calendar_google_enabled": True,
        }
        with patch.object(cal, "load_ics_path", return_value=[_stub_event("ICS meet", "ics")]) as m_ics, \
                patch.object(cal, "load_outlook_events", return_value=[_stub_event("Outlook meet", "outlook")]) as m_outlook, \
                patch.object(cal, "_google_events_from_settings", return_value=[_stub_event("Google meet", "google")]) as m_google:
            events = cal.collect_events(settings)
            self.assertEqual(
                {e["title"] for e in events}, {"ICS meet", "Outlook meet", "Google meet"}
            )
            # Second call within the 60s TTL must hit the cache, not refetch.
            cal.collect_events(settings)
            self.assertEqual(m_ics.call_count, 1)
            self.assertEqual(m_outlook.call_count, 1)
            self.assertEqual(m_google.call_count, 1)
            # force=True always refetches.
            cal.collect_events(settings, force=True)
            self.assertEqual(m_ics.call_count, 2)

    def test_disabled_sources_are_not_queried(self):
        cal = self.cal
        settings = {"calendar_ics_enabled": False, "calendar_outlook_enabled": False, "calendar_google_enabled": False}
        with patch.object(cal, "load_ics_path") as m_ics, \
                patch.object(cal, "load_outlook_events") as m_outlook, \
                patch.object(cal, "_google_events_from_settings") as m_google:
            self.assertEqual(cal.collect_events(settings), [])
            m_ics.assert_not_called()
            m_outlook.assert_not_called()
            m_google.assert_not_called()

    def test_google_events_from_settings_without_credentials(self):
        self.assertEqual(self.cal._google_events_from_settings({}), [])

    def test_google_events_from_settings_aggregates_calendars(self):
        cal = self.cal
        settings = {
            "google_calendar_client_id": "cid",
            "google_calendar_client_secret": "secret",
            "google_calendar_refresh_token": "encrypted-blob",
            "google_calendar_ids": "primary, team@example.com",
        }
        with patch.object(cal, "unprotect_string", side_effect=lambda v: v), \
                patch.object(cal, "refresh_google_token", return_value={"access_token": "tok"}) as m_refresh, \
                patch.object(
                    cal, "fetch_google_events",
                    side_effect=lambda token, cal_id, hours=12: [_stub_event(cal_id, "google")],
                ):
            events = cal._google_events_from_settings(settings)
        m_refresh.assert_called_once_with("cid", "secret", "encrypted-blob")
        self.assertEqual([e["title"] for e in events], ["primary", "team@example.com"])

    def test_google_events_from_settings_swallows_refresh_error(self):
        cal = self.cal
        settings = {
            "google_calendar_client_id": "cid",
            "google_calendar_refresh_token": "blob",
        }
        with patch.object(cal, "unprotect_string", side_effect=lambda v: v), \
                patch.object(cal, "refresh_google_token", side_effect=RuntimeError("invalid_grant")):
            self.assertEqual(cal._google_events_from_settings(settings), [])


class TestCollectEventsAsync(unittest.TestCase):
    """collect_events_async() must never block the caller on network/COM I/O."""

    def setUp(self):
        from whisperfast.core import calendar as calendar_mod

        self.cal = calendar_mod
        self.cal._cache["ts"] = 0.0
        self.cal._cache["events"] = []
        self.cal._async_refresh_in_flight = False

    def test_returns_cached_snapshot_immediately_then_refreshes_in_background(self):
        cal = self.cal
        settings = {"calendar_ics_enabled": True, "calendar_ics_path": "x.ics"}
        call_started = threading.Event()
        release = threading.Event()

        def slow_load_ics(_path):
            call_started.set()
            release.wait(2)
            return [_stub_event("late event", "ics")]

        with patch.object(cal, "load_ics_path", side_effect=slow_load_ics), \
                patch.object(cal, "load_outlook_events", return_value=[]), \
                patch.object(cal, "_google_events_from_settings", return_value=[]):
            first = cal.collect_events_async(settings)
            self.assertEqual(first, [])  # nothing cached yet; must not block
            self.assertTrue(call_started.wait(2), "background refresh should have started")
            release.set()
            deadline = time.time() + 2
            while time.time() < deadline and not cal.get_cached_events():
                time.sleep(0.05)
        self.assertEqual([e["title"] for e in cal.get_cached_events()], ["late event"])


if __name__ == "__main__":
    unittest.main()
