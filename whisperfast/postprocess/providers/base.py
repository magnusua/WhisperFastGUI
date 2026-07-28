"""Базовий контракт AI-провайдера."""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

LogFunc = Callable[..., None]
PromptTuple = Tuple[int, str, str]  # (num, name, text)


class AIProvider(Protocol):
    id: str
    label_key: str

    def has_api_credentials(self, credentials: Dict[str, Any]) -> bool:
        """Чи достатньо даних для API-режиму (не browser fallback)."""
        ...

    def process(
        self,
        txt_path: str,
        prompts: List[PromptTuple],
        credentials: Dict[str, Any],
        log_func: Optional[LogFunc] = None,
        on_file_created: Optional[Callable[[str], None]] = None,
        resolve_output_path: Optional[Callable[[str], str]] = None,
    ) -> List[str]:
        """Виконує ланцюжок промптів. Повертає створені шляхи."""
        ...
