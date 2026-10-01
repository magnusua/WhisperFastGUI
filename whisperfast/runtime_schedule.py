"""Pure helpers for daily restart and long-uptime update checks."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, Tuple

DAILY_RESTART_HOUR = 2
DAILY_RESTART_MINUTE = 0
UPTIME_UPDATE_CHECK_S = 10 * 3600
BUSY_RETRY_S = 5 * 60
POLL_S = 30


def next_daily_at(
    hour: int = DAILY_RESTART_HOUR,
    minute: int = DAILY_RESTART_MINUTE,
    now: Optional[datetime] = None,
) -> datetime:
    """Next local wall-clock hit of hour:minute (tomorrow if already past today)."""
    current = now or datetime.now()
    target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if current >= target:
        target = target + timedelta(days=1)
    return target


def seconds_until_daily(
    hour: int = DAILY_RESTART_HOUR,
    minute: int = DAILY_RESTART_MINUTE,
    now: Optional[datetime] = None,
) -> float:
    current = now or datetime.now()
    return max(0.0, (next_daily_at(hour, minute, current) - current).total_seconds())


def should_fire_daily(
    *,
    enabled: bool,
    last_fire_day: str,
    hour: int = DAILY_RESTART_HOUR,
    minute: int = DAILY_RESTART_MINUTE,
    now: Optional[datetime] = None,
) -> Tuple[bool, str]:
    """True when enabled, local time is at/past the daily slot, and not yet fired today.

    Returns (fire, day_key) where day_key is YYYY-MM-DD for the current local day.
    """
    current = now or datetime.now()
    day_key = current.strftime("%Y-%m-%d")
    if not enabled:
        return False, day_key
    if last_fire_day == day_key:
        return False, day_key
    slot = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return current >= slot, day_key


def uptime_update_due(
    started_at: float,
    *,
    now: Optional[float] = None,
    already_checked: bool = False,
    threshold_s: float = UPTIME_UPDATE_CHECK_S,
) -> bool:
    """True once per session when uptime has reached the threshold."""
    if already_checked:
        return False
    stamp = time_now(now)
    if started_at <= 0:
        return False
    return (stamp - started_at) >= threshold_s


def time_now(value: Optional[float] = None) -> float:
    import time

    return float(value if value is not None else time.time())
