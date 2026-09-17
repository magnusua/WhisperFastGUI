"""Auto-record / calendar trigger state machine (no audio callback)."""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

# Actions returned by tick()
START_AUTO = "start_auto"
START_CALENDAR = "start_calendar"
STOP_GONE = "stop_gone"
STOP_SILENCE = "stop_silence"
STOP_EVENT_END = "stop_event_end"
NONE = ""


class AutoRecordMachine:
    """Priority: manual stop > manual start > auto-record > calendar.

    A second trigger never starts a second session while one is running.
    Silence autostop applies only to auto/calendar sessions, never manual.
    """

    def __init__(self):
        self.pending_start_at: Optional[float] = None
        self.pending_stop_at: Optional[float] = None
        self.silence_since: Optional[float] = None
        self.pending_kind: str = ""  # auto | calendar

    def reset(self) -> None:
        self.pending_start_at = None
        self.pending_stop_at = None
        self.silence_since = None
        self.pending_kind = ""

    def tick(
        self,
        *,
        now: Optional[float] = None,
        auto_enabled: bool,
        match: Optional[dict],
        calendar_event: Optional[dict],
        recording: bool,
        trigger: str,
        elapsed_s: float,
        last_sound_age_s: float,
        start_delay_s: float,
        stop_delay_s: float,
        min_duration_s: float,
        silence_stop_s: float,
        calendar_enabled: bool,
    ) -> str:
        now = time.time() if now is None else now
        trigger = (trigger or "").strip().lower()
        is_manual = recording and trigger == "manual"
        if is_manual:
            self.reset()
            return NONE

        want_auto = bool(auto_enabled and match)
        want_cal = bool(calendar_enabled and calendar_event)

        if recording:
            return self._tick_recording(
                now=now,
                want_auto=want_auto,
                want_cal=want_cal,
                trigger=trigger,
                elapsed_s=elapsed_s,
                last_sound_age_s=last_sound_age_s,
                stop_delay_s=stop_delay_s,
                min_duration_s=min_duration_s,
                silence_stop_s=silence_stop_s,
            )

        self.pending_stop_at = None
        self.silence_since = None
        if not auto_enabled and not calendar_enabled:
            self.reset()
            return NONE
        if want_auto:
            return self._arm_start(now, START_AUTO, "auto", start_delay_s)
        if want_cal:
            return self._arm_start(now, START_CALENDAR, "calendar", start_delay_s)
        self.pending_start_at = None
        self.pending_kind = ""
        return NONE

    def _arm_start(self, now: float, action: str, kind: str, delay: float) -> str:
        delay = max(0.0, float(delay or 0))
        if self.pending_kind != kind:
            self.pending_start_at = now
            self.pending_kind = kind
        if self.pending_start_at is None:
            self.pending_start_at = now
        if now - self.pending_start_at >= delay:
            self.reset()
            return action
        return NONE

    def _tick_recording(
        self,
        *,
        now: float,
        want_auto: bool,
        want_cal: bool,
        trigger: str,
        elapsed_s: float,
        last_sound_age_s: float,
        stop_delay_s: float,
        min_duration_s: float,
        silence_stop_s: float,
    ) -> str:
        self.pending_start_at = None
        self.pending_kind = ""
        still_wanted = want_auto if trigger == "auto" else (want_cal if trigger == "calendar" else True)
        if trigger == "auto" and want_auto:
            still_wanted = True
        if trigger == "calendar" and (want_cal or want_auto):
            still_wanted = True

        if elapsed_s < max(0.0, float(min_duration_s or 0)):
            self.pending_stop_at = None
            self.silence_since = None
            return NONE

        if still_wanted:
            self.pending_stop_at = None
        else:
            if self.pending_stop_at is None:
                self.pending_stop_at = now
            if now - self.pending_stop_at >= max(0.0, float(stop_delay_s or 0)):
                kind = STOP_EVENT_END if trigger == "calendar" else STOP_GONE
                self.reset()
                return kind

        if silence_stop_s and float(silence_stop_s) > 0:
            if last_sound_age_s >= float(silence_stop_s):
                if self.silence_since is None:
                    self.silence_since = now - last_sound_age_s
                if now - (self.silence_since or now) >= float(silence_stop_s):
                    self.reset()
                    return STOP_SILENCE
            else:
                self.silence_since = None
        return NONE
