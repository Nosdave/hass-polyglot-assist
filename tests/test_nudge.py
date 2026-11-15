"""Second pass when the fallback claims an action without a tool (nudge.py)."""
from __future__ import annotations

import asyncio
import importlib.util
from dataclasses import dataclass, field
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "custom_components" / "polyglot_assist"
spec = importlib.util.spec_from_file_location("_pg_nudge", PKG / "nudge.py")
nudge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nudge)


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


class Fallback:
    """Answers per pass from a script: each item is (tool_calls, text). Writes like a real agent."""

    def __init__(self, log: Log, passes: list[tuple[list[str], str]], error: bool = False) -> None:
        self.log, self.passes, self.error, self.seen = log, list(passes), error, []

    async def __call__(self, model_text):
        # what the model sees as the latest user message in this pass
        self.seen.append(self.log.content[-1].content)
        tools, text = self.passes.pop(0)
        for t in tools:
            self.log.content.append(Assistant(tool_calls=[Call(t)]))
            if self.log.delta_listener:
                self.log.delta_listener(self.log, {"role": "assistant", "tool_calls": [t]})
        self.log.content.append(Assistant(content=text))
        if self.log.delta_listener:
            self.log.delta_listener(self.log, {"role": "assistant", "content": text})
        return {"speech": text, "error": self.error}


def _turn(history: bool):
    log = Log(content=[User("Schalt Wohnzimmer auf Weiß."), Assistant(content="Erledigt.")] if history else [])
    log.content.append(User("Schalt sie auf 6000 Kelvin."))
    return log, len(log.content)


def _run(log, before, fb, enabled=True):
    return asyncio.run(nudge.async_answer(
        log, before, "Schalt sie auf 6000 Kelvin.", fb, enabled=enabled,
        is_error=lambda r: r["error"], speech_of=lambda r: r["speech"]))


def test_text_only_in_running_conversation_gets_one_second_pass():
    log, before = _turn(history=True)
    fb = Fallback(log, [([], "Sind jetzt auf 6000 Kelvin."), (["script__licht"], "Auf 6000 Kelvin gestellt.")])
    result, nudged = _run(log, before, fb)
    assert nudged == {"tools": 1, "first": "Sind jetzt auf 6000 Kelvin."}
    assert result["speech"] == "Auf 6000 Kelvin gestellt."
    assert fb.seen[1].startswith("Schalt sie auf 6000 Kelvin.") and nudge.NUDGE in fb.seen[1]


def test_history_keeps_own_words_and_only_the_second_answer():
    log, before = _turn(history=True)
    fb = Fallback(log, [([], "Sind jetzt auf 6000 Kelvin."), (["script__licht"], "Auf 6000 Kelvin gestellt.")])
    _run(log, before, fb)
    texts = [c.content for c in log.content]
    assert texts.count("Schalt sie auf 6000 Kelvin.") == 1           # the user's own words, once
    assert not any(nudge.NUDGE in (t or "") for t in texts)          # the note is not kept
    assert "Sind jetzt auf 6000 Kelvin." not in texts                # first (claimed) answer is gone
    assert texts[-1] == "Auf 6000 Kelvin gestellt."


def test_no_second_pass_without_history():
    log, before = _turn(history=False)
    fb = Fallback(log, [([], "Gerne.")])
    result, nudged = _run(log, before, fb)
    assert nudged is None and len(fb.seen) == 1 and result["speech"] == "Gerne."


def test_no_second_pass_when_a_tool_was_called():
    log, before = _turn(history=True)
    fb = Fallback(log, [(["script__licht"], "Gestellt.")])
    _, nudged = _run(log, before, fb)
    assert nudged is None and len(fb.seen) == 1


def test_at_most_one_second_pass():
    log, before = _turn(history=True)
    fb = Fallback(log, [([], "Erledigt."), ([], "Die Lichter sind bereits so eingestellt.")])
    result, nudged = _run(log, before, fb)
    assert len(fb.seen) == 2 and nudged["tools"] == 0
    assert result["speech"] == "Die Lichter sind bereits so eingestellt."


def test_switched_off_and_errors_are_left_alone():
    log, before = _turn(history=True)
    fb = Fallback(log, [([], "Erledigt.")])
    assert _run(log, before, fb, enabled=False)[1] is None and len(fb.seen) == 1
    log, before = _turn(history=True)
    fb = Fallback(log, [([], "Error talking to API")], error=True)
    assert _run(log, before, fb)[1] is None and len(fb.seen) == 1


def test_external_calls_do_not_count_as_tools():
    log = Log(content=[User("a"), Assistant(content="b"), User("Schalt sie auf 6000 Kelvin.")])
    log.content.append(Assistant(tool_calls=[Call("intent__HassTurnOn", external=True)]))
    log.content.append(Assistant(content="ok"))
    assert nudge.answered_text_only(log.content, 3)


def test_deltas_of_a_redone_answer_are_never_passed_on():
    got = []
    log, before = _turn(history=True)
    log.delta_listener = lambda _log, d: got.append(d)
    own = log.delta_listener
    fb = Fallback(log, [([], "Sind jetzt auf 6000 Kelvin."), (["script__licht"], "Auf 6000 Kelvin gestellt.")])
    _run(log, before, fb)
    spoken = [d.get("content") for d in got if d.get("content")]
    assert spoken == ["Auf 6000 Kelvin gestellt."]
    assert log.delta_listener is own


def test_held_deltas_pass_on_unchanged_for_a_normal_answer():
    got = []
    log, before = _turn(history=True)
    log.delta_listener = lambda _log, d: got.append(d)
    fb = Fallback(log, [(["script__licht"], "Gestellt.")])
    _run(log, before, fb)
    assert [d.get("content") or d.get("tool_calls") for d in got] == [["script__licht"], "Gestellt."]
