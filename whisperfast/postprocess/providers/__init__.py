"""AI providers for transcript post-processing."""
from __future__ import annotations

from typing import Dict, List, Tuple

from whisperfast.postprocess.providers.base import AIProvider
from whisperfast.postprocess.providers.copilot import CopilotProvider
from whisperfast.postprocess.providers.cursor_provider import CursorProvider
from whisperfast.postprocess.providers.gemini import GeminiProvider

PROVIDER_CURSOR = "cursor"
PROVIDER_GEMINI = "gemini"
PROVIDER_COPILOT = "copilot"

PROVIDERS: Dict[str, AIProvider] = {
    PROVIDER_CURSOR: CursorProvider(),
    PROVIDER_GEMINI: GeminiProvider(),
    PROVIDER_COPILOT: CopilotProvider(),
}

PROVIDER_ORDER: List[str] = [PROVIDER_CURSOR, PROVIDER_GEMINI, PROVIDER_COPILOT]


def get_provider(provider_id: str) -> AIProvider:
    pid = (provider_id or PROVIDER_CURSOR).strip().lower()
    if pid not in PROVIDERS:
        pid = PROVIDER_CURSOR
    return PROVIDERS[pid]


def provider_choices() -> List[Tuple[str, str]]:
    """[(id, i18n_label_key), ...]"""
    return [
        (PROVIDER_CURSOR, "ai_provider_cursor"),
        (PROVIDER_GEMINI, "ai_provider_gemini"),
        (PROVIDER_COPILOT, "ai_provider_copilot"),
    ]


def normalize_provider_id(provider_id: str) -> str:
    pid = (provider_id or PROVIDER_CURSOR).strip().lower()
    return pid if pid in PROVIDERS else PROVIDER_CURSOR
