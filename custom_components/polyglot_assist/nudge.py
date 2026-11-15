"""Second pass when the fallback claims an action without calling a tool.

Measured with a local Qwen model (15.11.2026): inside a running conversation,
after a turn that really switched something, the model takes a repeated or
corrected request ("I said white", "set them to 6000 K") for "already done"
and answers "they are now set to …" WITHOUT any tool call — 0 of 8 runs.
Its own earlier claims in the history reinforce this. A system message in the
middle of the history is refused by the chat template (HTTP 400), and a rule
in the system prompt helps only partly. What works: answer once more with a
short check note appended to the user's latest message (6000 K: 8/8 tool
calls; small talk and questions: no tool calls, same answer).

So, for one turn, and only inside a running conversation:

1. while the fallback answers, its deltas are held back (not spoken yet) as
   long as no tool call has appeared — once one does, everything is passed
   on and the turn runs normally;
2. if the turn ended with text only, that answer is removed from the chat
   log and the fallback answers once more, with the note appended to the
   user's message for this pass only (afterwards the log holds the user's
   own words and only the second answer);
3. otherwise the held-back deltas are passed on unchanged.

The functions here work on the ChatLog duck-typed (``content`` list of
objects with ``role``/``content``/``tool_calls``, ``delta_listener``), so
they are testable without Home Assistant.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Callable

NUDGE = (
    "Check: you answered without calling any tool in this turn. If the user's latest message asks to switch, set, "
    "dim or change something, call the right tool now (earlier turns do not count — the user says it did not happen "
    "or wants it again). If it only asks a question or is small talk, give the same answer again without a tool."
)


def has_history(content: list[Any], before: int) -> bool:
    """True if a user turn exists before this turn's user message (index ``before - 1``)."""
    return any(getattr(c, "role", None) == "user" for c in content[: max(0, before - 1)])


def tool_calls_in(content: list[Any]) -> int:
    """Tool calls the model itself made (Polyglot's own recorded hits are ``external``)."""
    return sum(1 for c in content for tc in (getattr(c, "tool_calls", None) or []) if not getattr(tc, "external", False))


def answered_text_only(content: list[Any], before: int) -> bool:
    """This turn's new entries: at least one assistant text, no tool call at all.

    Nothing new at all (e.g. the fallback answered in another conversation)
    is NOT text-only — there is nothing to redo here.
    """
    new = content[before:]
    texts = [c for c in new if getattr(c, "role", None) == "assistant" and getattr(c, "content", None)]
    return bool(texts) and tool_calls_in(new) == 0


def nudged_text(text: str) -> str:
    return f"{text}\n\n({NUDGE})"


class HeldDeltas:
    """Hold the fallback's deltas back until a tool call shows up (then pass everything on)."""

    def __init__(self, chat_log: Any) -> None:
        self._log = chat_log
        self._target: Callable[[Any, dict], None] | None = chat_log.delta_listener
        self._held: list[dict] = []
        self.live = self._target is None  # nobody listens → nothing to hold back
        if not self.live:
            chat_log.delta_listener = self._listen

    def _listen(self, chat_log: Any, delta: dict) -> None:
        if self.live:
            self._target(chat_log, delta)
            return
        self._held.append(delta)
        if delta.get("tool_calls"):  # a real action — no second pass, speak as usual from here on
            self.flush()

    def flush(self) -> None:
        """Pass on what was held back and go live."""
        held, self._held = self._held, []
        self.live = True
        if self._target is not None:
            for delta in held:
                self._target(self._log, delta)

    def discard(self) -> None:
        """Forget what was held back (the answer is redone) and go live."""
        self._held = []
        self.live = True

    def restore(self) -> None:
        """Give the chat log its own listener back."""
        if self._target is not None:
            self._log.delta_listener = self._target


def drop_turn_answer(content: list[Any], before: int) -> None:
    """Remove what the fallback added in this turn (its text-only answer)."""
    del content[before:]


def swap_user_text(content: list[Any], index: int, text: str) -> Any:
    """Replace the text of the user message at ``index``; returns the old entry."""
    old = content[index]
    content[index] = dataclasses.replace(old, content=text)
    return old


async def async_answer(
    chat_log: Any,
    before: int,
    user_text: str,
    call: Callable[[str | None], Any],
    *,
    enabled: bool,
    is_error: Callable[[Any], bool],
    speech_of: Callable[[Any], str],
) -> tuple[Any, dict[str, Any] | None]:
    """Run the fallback for this turn (``call(model_text)``), with at most ONE nudged second pass.

    Returns ``(result, nudged)``; ``nudged`` is None when no second pass ran,
    else ``{"tools": <tool calls of the second pass>, "first": <first answer>}``.
    """
    ok = (enabled and has_history(chat_log.content, before) and before >= 1
          and getattr(chat_log.content[before - 1], "role", None) == "user"
          and getattr(chat_log.content[before - 1], "content", None) == user_text)
    held = HeldDeltas(chat_log) if ok else None
    nudged = None
    try:
        result = await call(None)
        if held is not None and not is_error(result) and answered_text_only(chat_log.content, before):
            held.discard()
            first = speech_of(result)
            drop_turn_answer(chat_log.content, before)
            note = nudged_text(user_text)
            own = swap_user_text(chat_log.content, before - 1, note)
            try:
                result = await call(note)
            finally:
                chat_log.content[before - 1] = own  # the log keeps the user's own words
            nudged = {"tools": tool_calls_in(chat_log.content[before:]), "first": first[:120]}
    finally:
        if held is not None:
            if not held.live:  # normal answer (or an error): pass on what was held back
                held.flush()
            held.restore()
    return result, nudged
