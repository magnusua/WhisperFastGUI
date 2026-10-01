"""Daily restart and long-uptime update scheduling helpers."""
from __future__ import annotations

import unittest
from datetime import datetime

from whisperfast.runtime_schedule import (
    next_daily_at,
    seconds_until_daily,
    should_fire_daily,
    uptime_update_due,
)


class TestDailyRestart(unittest.TestCase):
    def test_next_slot_is_tomorrow_when_already_past(self):
        now = datetime(2026, 10, 1, 15, 30, 0)
        nxt = next_daily_at(2, 0, now)
        self.assertEqual(nxt, datetime(2026, 10, 2, 2, 0, 0))
        self.assertGreater(seconds_until_daily(2, 0, now), 0)

    def test_next_slot_is_today_when_before(self):
        now = datetime(2026, 10, 1, 1, 0, 0)
        nxt = next_daily_at(2, 0, now)
        self.assertEqual(nxt, datetime(2026, 10, 1, 2, 0, 0))

    def test_should_not_fire_before_slot(self):
        now = datetime(2026, 10, 1, 1, 59, 0)
        fire, day = should_fire_daily(enabled=True, last_fire_day="", now=now)
        self.assertFalse(fire)
        self.assertEqual(day, "2026-10-01")

    def test_should_fire_after_slot_once(self):
        now = datetime(2026, 10, 1, 2, 0, 0)
        fire, day = should_fire_daily(enabled=True, last_fire_day="", now=now)
        self.assertTrue(fire)
        self.assertEqual(day, "2026-10-01")
        fire2, _ = should_fire_daily(enabled=True, last_fire_day=day, now=now)
        self.assertFalse(fire2)

    def test_disabled_never_fires(self):
        now = datetime(2026, 10, 1, 3, 0, 0)
        fire, _ = should_fire_daily(enabled=False, last_fire_day="", now=now)
        self.assertFalse(fire)


class TestUptimeUpdate(unittest.TestCase):
    def test_due_after_threshold(self):
        self.assertFalse(uptime_update_due(100.0, now=100.0 + 3600, already_checked=False))
        self.assertTrue(
            uptime_update_due(100.0, now=100.0 + 10 * 3600, already_checked=False)
        )
        self.assertFalse(
            uptime_update_due(100.0, now=100.0 + 10 * 3600, already_checked=True)
        )


if __name__ == "__main__":
    unittest.main()
