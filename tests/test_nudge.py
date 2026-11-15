"""Hint against claimed actions without a tool, inside running conversations (nudge.py)."""
from __future__ import annotations

import asyncio
import importlib.util
from dataclasses import dataclass, field
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "custom_components" / "polyglot_assist"
spec = importlib.util.spec_from_file_location("_pg_nudge", PKG / "nudge.py")
nudge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nudge)

SAID = "Schalt sie auf 6000 Kelvin."


@dataclass(frozen=True)
class User:
    content: str
    role: str = "user"


@dataclass(frozen=True)
class Call:
    tool_name: str
    external: bool = False


@dataclass(frozen=True)
class Assistant:
    content: str | None = None
    tool_calls: list | None = None
    role: str = "assistant"


@dataclass
class Log:
    content: list = field(default_factory=list)
    delta_listener: object = None

    def add_user_like_ha(self, text: str) -> None:
        """What HA's async_get_chat_log does for the nested (fallback) call."""
        if self.content[-1].role != "user" or self.content[-1].content != text:
            self.content.append(User(text))


class Fallback:
    """Writes like a real agent: user message via HA's rule, streamed deltas, tool calls, answer."""

    def __init__(self, log: Log, tools: list[str], text: str, error: bool = False) -> None:
        self.log, self.tools, self.text, self.error = log, tools, text, error
        self.calls = []
        self.seen = None

    async def __call__(self, model_text):
        self.calls.append(model_text)
        self.log.add_user_like_ha(model_text or SAID)
        self.seen = [c.content for c in self.log.content if c.role == "user"]
        for t in self.tools:
            self.log.content.append(Assistant(tool_calls=[Call(t)]))
            if self.log.delta_listener:
                self.log.delta_listener(self.log, {"role": "assistant", "tool_calls": [t]})
        for piece in (self.text[:10], self.text[10:]):
            if self.log.delta_listener:
                self.log.delta_listener(self.log, {"content": piece})
        self.log.content.append(Assistant(content=self.text))
        return {"speech": self.text, "error": self.error}


def _turn(history: bool) -> tuple[Log, int]:
    log = Log(content=[User("Schalt Wohnzimmer auf Weiß."), Assistant(content="Erledigt.")] if history else [])
    log.content.append(User(SAID))  # added by HA for Polyglot's own turn
    return log, len(log.content)


def _run(log, before, fb, enabled=True):
    return asyncio.run(nudge.async_answer(log, before, SAID, fb, enabled=enabled))


def test_hint_only_with_history_and_option_on():
    log, before = _turn(history=True)
    fb = Fallback(log, ["script__licht"], "Gestellt.")
    _, hinted = _run(log, before, fb)
    assert hinted and fb.calls == [nudge.hinted_text(SAID)]
    assert fb.seen[-1] == SAID + "\n\n" + nudge.HINT          # the model sees the hint on the latest message

    log, before = _turn(history=False)
    fb = Fallback(log, [], "Gerne.")
    assert _run(log, before, fb) == ({"speech": "Gerne.", "error": False}, False) and fb.calls == [None]

    log, before = _turn(history=True)
    fb = Fallback(log, [], "Gerne.")
    assert _run(log, before, fb, enabled=False)[1] is False and fb.calls == [None]


def test_one_pass_only():
    log, before = _turn(history=True)
    fb = Fallback(log, [], "Sind jetzt auf 6000 Kelvin.")
    _run(log, before, fb)
    assert len(fb.calls) == 1


def test_history_keeps_the_sentence_without_hint():
    log, before = _turn(history=True)
    fb = Fallback(log, ["script__licht"], "Auf 6000 Kelvin gestellt.")
    _run(log, before, fb)
    users = [c.content for c in log.content if c.role == "user"]
    assert users == ["Schalt Wohnzimmer auf Weiß.", SAID]         # no second user message, no hint
    assert not any(nudge.HINT in (c.content or "") for c in log.content)
    assert log.content[-1].content == "Auf 6000 Kelvin gestellt."


def test_next_turn_sees_no_hint_from_before():
    log, before = _turn(history=True)
    _run(log, before, Fallback(log, ["script__licht"], "Gestellt."))
    log.content.append(User("Und die Küche auch."))
    fb = Fallback(log, ["script__licht"], "Küche gestellt.")
    asyncio.run(nudge.async_answer(log, len(log.content), "Und die Küche auch.", fb, enabled=True))
    assert [s for s in fb.seen[:-1]] == ["Schalt Wohnzimmer auf Weiß.", SAID]


def test_streaming_deltas_pass_unchanged_and_at_once():
    got = []
    log, before = _turn(history=True)
    listener = lambda _log, d: got.append((len(log.content), d))  # noqa: E731
    log.delta_listener = listener
    fb = Fallback(log, ["script__licht"], "Auf 6000 Kelvin gestellt.")
    _run(log, before, fb)
    assert [d for _, d in got] == [{"role": "assistant", "tool_calls": ["script__licht"]},
                                   {"content": "Auf 6000 K"}, {"content": "elvin gestellt."}]
    # each delta arrived while the fallback was still writing (not after it returned)
    assert got[1][0] < len(log.content)
    assert log.delta_listener is listener


def test_error_result_returned_as_is_and_log_restored():
    log, before = _turn(history=True)
    fb = Fallback(log, [], "Error talking to API", error=True)
    result, hinted = _run(log, before, fb)
    assert result["error"] and hinted and len(fb.calls) == 1
    assert log.content[before - 1].content == SAID


def test_log_restored_when_the_fallback_raises():
    log, before = _turn(history=True)

    async def boom(_text):
        raise RuntimeError("down")

    try:
        _run(log, before, boom)
    except RuntimeError:
        pass
    assert log.content[before - 1].content == SAID


def test_external_calls_do_not_count_as_tools():
    content = [Assistant(tool_calls=[Call("intent__HassTurnOn", external=True)]), Assistant(content="ok")]
    assert nudge.tool_calls_in(content) == 0
