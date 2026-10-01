"""Ask which AI prompts to run after Telegram private media arrives."""
from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence

from whisperfast.i18n import t
from whisperfast.settings import normalize_default_prompt_nums

PROMPT_ASK_TIMEOUT_S = 20.0
_pending: Dict[int, asyncio.Future] = {}
_SPLIT = re.compile(r"[,;\s]+")


def parse_prompt_reply(
    text: str,
    *,
    all_nums: Sequence[int],
    defaults: Sequence[int],
) -> List[int]:
    """``0`` = all prompts; comma/space list of numbers; empty/invalid → defaults."""
    raw = str(text or "").strip()
    if not raw:
        return list(defaults)
    parts = [p for p in _SPLIT.split(raw) if p]
    if not parts:
        return list(defaults)
    nums: List[int] = []
    seen = set()
    for part in parts:
        try:
            n = int(part)
        except (TypeError, ValueError):
            return list(defaults)
        if n == 0:
            return [int(x) for x in all_nums if int(x) > 0]
        if n > 0 and n not in seen:
            seen.add(n)
            nums.append(n)
    return nums if nums else list(defaults)


def format_prompt_question(
    prompts: Sequence[tuple],
    *,
    defaults: Sequence[int],
    timeout_s: float = PROMPT_ASK_TIMEOUT_S,
    on_timeout: str = "defaults",
) -> str:
    """Human-readable list of prompt numbers for the Telegram reply."""
    if on_timeout == "none":
        no_reply = t("telegram_prompt_ask_no_ai")
    else:
        no_reply = t(
            "telegram_prompt_ask_defaults",
            nums=", ".join(str(n) for n in defaults) if defaults else "—",
        )
    lines = [
        t("telegram_prompt_ask_header", timeout=int(timeout_s)),
        no_reply,
        t("telegram_prompt_ask_zero"),
        "",
    ]
    for num, name, _body in prompts:
        label = str(name or "").strip() or f"#{num}"
        lines.append(f"{int(num)} — {label}")
    return "\n".join(lines).strip()


def take_pending_reply(chat_id: int, text: str) -> bool:
    """If this chat is waiting for prompt numbers, feed the text and return True."""
    try:
        key = int(chat_id)
    except (TypeError, ValueError):
        return False
    fut = _pending.get(key)
    if fut is None or fut.done():
        return False
    fut.set_result(str(text or ""))
    return True


def is_waiting_for_prompts(chat_id: int) -> bool:
    try:
        key = int(chat_id)
    except (TypeError, ValueError):
        return False
    fut = _pending.get(key)
    return fut is not None and not fut.done()


def defaults_from_settings(settings: Mapping[str, Any]) -> List[int]:
    return normalize_default_prompt_nums(settings.get("ai_default_prompt_nums"))


def should_ask_prompts(settings: Mapping[str, Any], *, is_private: bool) -> bool:
    if not is_private:
        return False
    if not bool(settings.get("telegram_ask_prompts", True)):
        return False
    if not bool(settings.get("send_txt_to_ai", settings.get("send_txt_to_cursor"))):
        return False
    return True


async def ask_prompt_nums(
    event,
    settings: Mapping[str, Any],
    *,
    timeout_s: float = PROMPT_ASK_TIMEOUT_S,
    on_timeout: str = "defaults",
) -> List[int]:
    """Send the prompt question and wait for a reply.

    ``on_timeout``:
    - ``defaults`` — empty/timeout uses ``ai_default_prompt_nums`` (private media).
    - ``none`` — empty/timeout skips AI (social links).
    """
    from whisperfast.postprocess.cursor_postprocess import parse_redactor_prompts

    defaults = defaults_from_settings(settings)
    prompts = parse_redactor_prompts()
    all_nums = [int(row[0]) for row in prompts if int(row[0]) > 0]
    if not prompts:
        return [] if on_timeout == "none" else defaults

    chat_id = int(getattr(event, "chat_id", 0) or 0)
    question = format_prompt_question(
        prompts,
        defaults=defaults,
        timeout_s=timeout_s,
        on_timeout=on_timeout,
    )
    try:
        await event.reply(question)
    except Exception:
        try:
            client = event.client
            await client.send_message(chat_id, question, reply_to=int(event.message.id))
        except Exception:
            return [] if on_timeout == "none" else defaults

    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    previous = _pending.get(chat_id)
    if previous is not None and not previous.done():
        previous.set_result("")
    _pending[chat_id] = fut
    try:
        reply = await asyncio.wait_for(asyncio.shield(fut), timeout=float(timeout_s))
    except asyncio.TimeoutError:
        reply = ""
    finally:
        if _pending.get(chat_id) is fut:
            _pending.pop(chat_id, None)

    fallback = [] if on_timeout == "none" else defaults
    return parse_prompt_reply(reply, all_nums=all_nums, defaults=fallback)


def same_media_stem(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return (
        os.path.splitext(os.path.basename(a))[0].casefold()
        == os.path.splitext(os.path.basename(b))[0].casefold()
    )


def resolve_prompt_nums_for_txt(app, txt_path: str) -> Optional[List[int]]:
    """Find Telegram-selected prompt numbers for a transcript path."""
    if not txt_path:
        return None
    txt = os.path.abspath(txt_path)
    ctrl = getattr(app, "queue_ctrl", None)
    queue = getattr(ctrl, "queue", None) if ctrl is not None else None
    if queue:
        for item in queue:
            nums = item.get("telegram_prompt_nums")
            if nums is None:
                continue
            src = str(item.get("path") or "")
            if same_media_stem(src, txt):
                return [int(n) for n in nums if int(n) > 0]
    try:
        from whisperfast.telegram.origin import lookup

        row = lookup(txt)
        if row and row.get("prompt_nums") is not None:
            return [int(n) for n in row["prompt_nums"] if int(n) > 0]
        if queue:
            for item in queue:
                src = str(item.get("path") or "")
                if not same_media_stem(src, txt):
                    continue
                row = lookup(src)
                if row and row.get("prompt_nums") is not None:
                    return [int(n) for n in row["prompt_nums"] if int(n) > 0]
    except Exception:
        pass
    return None
