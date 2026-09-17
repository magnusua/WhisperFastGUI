"""Token/cost accounting for AI post-processing. Monthly ceiling pauses AI only."""
from __future__ import annotations

import threading
from datetime import date
from typing import Any, Callable, Dict, Optional, Tuple

_tls = threading.local()

# USD per 1M tokens (input, output). Local / unknown providers are free.
_PRICE_PER_MTOK: Dict[str, Tuple[float, float]] = {
    "gemini": (0.10, 0.40),
    "claude": (3.00, 15.00),
    "copilot": (2.50, 10.00),
    "cursor": (0.0, 0.0),
    "ollama": (0.0, 0.0),
    "openai_compat": (0.15, 0.60),
}


def current_month() -> str:
    return date.today().strftime("%Y-%m")


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, int(len(text) / 4))


def cost_usd(provider_id: str, prompt_tokens: int, completion_tokens: int) -> float:
    inp, out = _PRICE_PER_MTOK.get((provider_id or "").lower(), (0.0, 0.0))
    return (prompt_tokens / 1_000_000.0) * inp + (completion_tokens / 1_000_000.0) * out


def apply_usage(
    settings: Dict[str, Any],
    provider_id: str,
    prompt_tokens: int,
    completion_tokens: int,
    usd: Optional[float] = None,
) -> Dict[str, Any]:
    """Mutate a settings-like dict with this month's spend. Returns the same dict."""
    month = current_month()
    if (settings.get("ai_spend_month") or "") != month:
        settings["ai_spend_month"] = month
        settings["ai_spend_usd"] = 0.0
        settings["ai_prompt_tokens"] = 0
        settings["ai_completion_tokens"] = 0
    add = cost_usd(provider_id, prompt_tokens, completion_tokens) if usd is None else float(usd)
    settings["ai_spend_usd"] = float(settings.get("ai_spend_usd") or 0.0) + add
    settings["ai_prompt_tokens"] = int(settings.get("ai_prompt_tokens") or 0) + int(prompt_tokens)
    settings["ai_completion_tokens"] = int(settings.get("ai_completion_tokens") or 0) + int(
        completion_tokens
    )
    return settings


def set_usage_callback(callback: Optional[Callable[[str, int, int, float], None]]) -> None:
    """Thread-local hook invoked from LLM adapters (see notify_usage)."""
    _tls.callback = callback
    _tls.notified = False


def notify_usage(provider_id: str, prompt_tokens: int, completion_tokens: int) -> None:
    if prompt_tokens <= 0 and completion_tokens <= 0:
        return
    usd = cost_usd(provider_id, prompt_tokens, completion_tokens)
    _tls.notified = True
    cb = getattr(_tls, "callback", None)
    if callable(cb):
        try:
            cb(provider_id, int(prompt_tokens), int(completion_tokens), usd)
        except Exception:
            pass


def notify_usage_estimated(provider_id: str, prompt_tokens: int, completion_tokens: int) -> None:
    """Fallback when the adapter did not report usage for this call."""
    if getattr(_tls, "notified", False):
        _tls.notified = False
        return
    notify_usage(provider_id, prompt_tokens, completion_tokens)
    _tls.notified = False


def budget_exceeded(settings: Dict[str, Any]) -> bool:
    budget = float(settings.get("ai_month_budget") or 0.0)
    if budget <= 0:
        return False
    month = current_month()
    if (settings.get("ai_spend_month") or "") != month:
        return False
    return float(settings.get("ai_spend_usd") or 0.0) >= budget
