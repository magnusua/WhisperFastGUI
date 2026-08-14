"""Cursor provider: SDK chain або Chat CLI fallback."""
from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional

from whisperfast.postprocess.cursor_postprocess import (
    open_cursor_chat_fallback,
    resolve_cursor_api_key,
    run_sdk_chain,
)


class CursorProvider:
    id = "cursor"
    label_key = "ai_provider_cursor"

    def has_api_credentials(self, credentials: Dict[str, Any]) -> bool:
        return bool(resolve_cursor_api_key((credentials.get("cursor_api_key") or "").strip()))

    def process(
        self,
        txt_path: str,
        prompts: List,
        credentials: Dict[str, Any],
        log_func: Optional[Callable[..., None]] = None,
        on_file_created: Optional[Callable[[str], None]] = None,
        resolve_output_path: Optional[Callable[[str], str]] = None,
    ) -> List[str]:
        api_key = resolve_cursor_api_key((credentials.get("cursor_api_key") or "").strip())
        if not prompts:
            return []

        if not self.has_api_credentials(credentials):
            if log_func:
                try:
                    from whisperfast.i18n import t
                    log_func(t("cursor_no_api_key_fallback"))
                except ImportError:
                    pass
            open_cursor_chat_fallback(txt_path, prompts[0][2], log_func)
            return []

        try:
            import cursor_sdk  # noqa: F401
        except ImportError:
            if log_func:
                try:
                    from whisperfast.i18n import t
                    log_func(t("cursor_sdk_missing"))
                except ImportError:
                    pass
            open_cursor_chat_fallback(txt_path, prompts[0][2], log_func)
            return []

        created = run_sdk_chain(
            txt_path,
            prompts,
            api_key,
            log_func=log_func,
            on_file_created=on_file_created,
            resolve_output_path=resolve_output_path,
        )
        if log_func:
            try:
                from whisperfast.i18n import t
                name = os.path.basename(txt_path)
                if created:
                    log_func(t("cursor_chain_done", count=len(created), name=name))
                else:
                    log_func(t("cursor_chain_no_output", name=name))
            except ImportError:
                pass
        return created
