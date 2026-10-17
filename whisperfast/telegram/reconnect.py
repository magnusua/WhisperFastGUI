"""Keep the Telegram listener alive across long network outages."""
from __future__ import annotations

import asyncio
import time
from typing import Callable, Optional

# First pause after a drop, then 10s, 20s, and 30s for as long as the network is down.
RECONNECT_MIN_S = 5.0
RECONNECT_MAX_S = 30.0

_NET_HINTS = (
    "connection",
    "timed out",
    "timeout",
    "network",
    "unreachable",
    "getaddrinfo",
    "reset",
    "refused",
    "aborted",
    "offline",
    "dns",
    "temporary failure",
)


def is_network_error(exc: BaseException) -> bool:
    """True for socket failures and Telegram client errors that mean the link is down."""
    if isinstance(exc, OSError):
        return True
    text = str(exc).lower()
    return any(hint in text for hint in _NET_HINTS)


def next_reconnect_wait(current: float, *, was_online: bool) -> float:
    """Seconds to wait before the next connect. A live session starts the pause over at 5s."""
    if was_online:
        return 0.0
    try:
        current = float(current)
    except (TypeError, ValueError):
        current = 0.0
    if current < RECONNECT_MIN_S:
        return RECONNECT_MIN_S
    return min(current * 2.0, RECONNECT_MAX_S)


def stopped(stop: Optional[Callable[[], bool]]) -> bool:
    try:
        return bool(stop and stop())
    except Exception:
        return False


def sleep_until_stop(
    stop: Optional[Callable[[], bool]],
    seconds: float,
    step: float = 0.4,
) -> bool:
    """Wait up to ``seconds``. Return False as soon as ``stop`` is true."""
    deadline = time.monotonic() + max(0.0, float(seconds))
    while True:
        if stopped(stop):
            return False
        left = deadline - time.monotonic()
        if left <= 0:
            return True
        time.sleep(min(step, left))


async def async_sleep_until_stop(
    stop: Optional[Callable[[], bool]],
    seconds: float,
    step: float = 0.4,
) -> bool:
    """Async form of ``sleep_until_stop`` so the account listener can be stopped mid-wait."""
    deadline = time.monotonic() + max(0.0, float(seconds))
    while True:
        if stopped(stop):
            return False
        left = deadline - time.monotonic()
        if left <= 0:
            return True
        await asyncio.sleep(min(step, left))
