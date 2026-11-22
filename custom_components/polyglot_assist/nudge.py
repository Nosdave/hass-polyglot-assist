"""A short hint against claimed actions without a tool, inside running conversations.

Measured with a local Qwen model (15.11.2026): inside a running conversation,
after a turn that really switched something, the model takes a repeated or
corrected request ("I said white", "set them to 6000 K") for "already done"
and answers "they are now set to …" WITHOUT any tool call. Its own earlier
claims in the history reinforce this. A system message in the middle of the
history is refused by the chat template (HTTP 400), and a rule in the system
prompt helps only partly. A short note appended to the user's latest message
works (6000 K after claimed answers: 8/8 tool calls, was 0–5/8) and leaves
small talk and questions alone.

Replayed on 120 real mid-conversation requests (16.11.2026, 3 runs each):
tool calls 78 % -> 91 %, claimed actions without a tool 36 -> 10. Side
effect: the model also acts on vague follow-ups ("I was talking about the
car the whole time" after a garage request: garage button pressed 4/4 with
the hint, 1/4 without). Keep security-relevant buttons out of the LLM's
exposed entities; complaints and feedback are named in the hint as a further
guard against repeating an action the user complains about.

So when the conversation already has earlier turns, the fallback gets the
user's sentence plus HINT — in its one and only pass. The chat log keeps the
user's own words: the entry is swapped for the hinted text only while the
fallback runs (then HA's chat log sees the same text and adds no second user
message) and swapped back afterwards. Streaming is not touched.

The functions here work on the ChatLog duck-typed (``content`` list of
objects with ``role``/``content``/``tool_calls``), so they are testable
without Home Assistant.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Awaitable, Callable

HINT = ("(Note: if this message asks to switch, set, dim or change something, call the tool in this turn — earlier "
        "turns do not count, even if it seems already done. Complaints, feedback, questions and small talk need no "
        "tool — never repeat an earlier action just because the user complains about it.)")


def has_history(content: list[Any], before: int) -> bool:
    """True if a user turn exists before this turn's user message (index ``before - 1``)."""
    return any(getattr(c, "role", None) == "user" for c in content[: max(0, before - 1)])


def tool_calls_in(content: list[Any]) -> int:
    """Tool calls the model itself made (Polyglot's own recorded hits are ``external``)."""
    return sum(1 for c in content for tc in (getattr(c, "tool_calls", None) or []) if not getattr(tc, "external", False))


def hinted_text(text: str) -> str:
    return f"{text}\n\n{HINT}"


async def async_answer(
    chat_log: Any,
    before: int,
    user_text: str,
    call: Callable[[str | None], Awaitable[Any]],
    *,
    enabled: bool,
) -> tuple[Any, bool]:
    """Run the fallback once (``call(model_text)``); with the hint when the conversation has history.

    Returns ``(result, hinted)``.
    """
    own = chat_log.content[before - 1] if before >= 1 else None
    hint = (enabled and own is not None and getattr(own, "role", None) == "user"
            and getattr(own, "content", None) == user_text and has_history(chat_log.content, before))
    if not hint:
        return await call(None), False
    text = hinted_text(user_text)
    chat_log.content[before - 1] = dataclasses.replace(own, content=text)
    try:
        return await call(text), True
    finally:
        # the log keeps the user's own words (also for later turns); everything after it stays as written
        chat_log.content[before - 1] = own
