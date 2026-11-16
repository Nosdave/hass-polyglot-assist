"""What the fallback assistant sees of a Polyglot hit in a running conversation.

Why: since Home Assistant 2026.11 the Assist pipeline writes the spoken answer
of every agent that leaves its turn open into the shared ChatLog
(``assist_pipeline/default_pipeline.py``: "Agents that do not use the chat log
only answer in their result"). For a Polyglot hit that is the confirmation
("Couch ist aus.") or, with tone answers, the literal ``[[polyglot:chime]]``
marker. The fallback LLM then copies the pattern: it confirms the next command
in words — or with the marker, which plays the success tone — without calling a
tool. Replayed on real conversations (fast lane, 57 follow-ups with a hit in
the history, 3 runs each):

* hit as its spoken text:   tool called 91/171, success claimed without a tool 26
* hit as "(no response)":   tool called 132/171, claimed 3
* hit left out entirely:    tool called 128/171, claimed 9

So Polyglot closes the turn itself: a successful command (``kind: command``;
intent_script reports ACTION_DONE for questions too) is recorded as
"(no response)", an answer to a question (or an error) as its text, and the
tone marker never reaches the assistant. Hits recorded as a tool call
(``llm_equivalent``, chatlog.py) are left alone.
"""
from __future__ import annotations

NO_RESPONSE = "(no response)"
CHIME_MARKER = "[[polyglot:chime]]"  # same value as const.CHIME_MARKER (kept HA-free for tests)


def history_text(spoken: str | None, action_done: bool) -> str:
    """Assistant text for the ChatLog after a hit that was not recorded as a tool call."""
    if action_done:
        return NO_RESPONSE
    text = (spoken or "").replace(CHIME_MARKER, "").strip()
    return text or NO_RESPONSE


def strip_marker(speech: str | None) -> str | None:
    """Fallback answer without a copied tone marker; None when nothing is left."""
    if not speech or CHIME_MARKER not in speech:
        return speech
    return " ".join(speech.replace(CHIME_MARKER, " ").split()) or None
