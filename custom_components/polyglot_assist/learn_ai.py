"""Learning from AI answers — the pure part (no Home Assistant needed).

A question the AI answered more than once by READING a device (e.g. "how warm
is it outside?" → the AI looked at the outdoor sensor and said "It is 12.4
degrees outside.") becomes a proposal for a fast local command:

* the sentences are the questions that were really asked;
* the answer is the AI's own wording, with the value replaced by a live value
  block (so the proposal never contains Jinja — answer.py compiles it);
* the entity is the one whose value appears in the AI's answer. With several
  candidates and no unique match, nothing is proposed.

The proposal is "question" kind (reads, never switches) and goes through the
normal checks (corpus, collisions, exposure/protection of what it reads).
"""
from __future__ import annotations

import re
from typing import Any

MIN_ASKED = 2
MAX_PROPOSALS = 3
STATE_TOOLS = ("GetLiveContext", "HassGetState")
NUM_RE = re.compile(r"(?<![\w.,])-?\d+(?:[.,]\d+)?(?![\w])")


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s'’-]", " ", (text or "").lower()).split())


def only_reads(entry: dict[str, Any]) -> bool:
    """The AI only READ state in this turn (no switching tool, no web search …)."""
    if "reads_only" in entry:  # computed from ALL tool calls when the turn was recorded
        return bool(entry["reads_only"])
    calls = entry.get("targets") or []
    if len(calls) >= 5:  # only the first 5 calls are kept: the rest is unknown
        return False
    return bool(calls) and all(any(str(c.get("tool", "")).endswith(t) for t in STATE_TOOLS) for c in calls)


def find_ai_answers(entries: list[dict[str, Any]], min_asked: int = MIN_ASKED) -> list[dict[str, Any]]:
    """Questions answered by the AI from ONE readable device, asked ≥ min_asked times."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        if e.get("result") == "assistant" and not e.get("error") and only_reads(e) and e.get("read") and e.get("speech"):
            key = _norm(e.get("text", ""))
            if key:
                groups.setdefault(key, []).append(e)
    out = []
    for key, items in groups.items():
        if len(items) < min_asked:
            continue
        entity = pick_entity(items)
        if entity is None:
            continue
        # the value read in the SAME turn as the answer we keep (it may differ from today's state)
        value = next((r.get("s") for r in (items[-1].get("read") or []) if r.get("e") == entity), None)
        out.append({
            "key": key, "texts": list(dict.fromkeys(i.get("text", "") for i in items))[:5], "count": len(items),
            "lang": items[-1].get("lang"), "entity": entity, "said": items[-1].get("speech", ""), "value": value,
            "ms": round(sum(i.get("ms") or 0 for i in items) / len(items)),
            "conversations": len({i.get("conversation") for i in items if i.get("conversation")}),
        })
    out.sort(key=lambda x: -x["count"])
    return out[:MAX_PROPOSALS]


def _variants(value: Any) -> list[str]:
    """How a recorded state can be written in an answer (12.44 → 12.44, 12,44, 12.4, 12,4, 12)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return []
    out = []
    for digits in (2, 1, 0):
        s = f"{f:.{digits}f}"
        out += [s, s.replace(".", ",")]
    if f == int(f):
        out.append(str(int(f)))
    return list(dict.fromkeys(out))


def pick_entity(items: list[dict[str, Any]]) -> str | None:
    """The one entity whose recorded value is in EVERY AI answer (or the only one read)."""
    candidates: set[str] | None = None
    for item in items:
        found = set()
        reads = item.get("read") or []
        numbers = set(NUM_RE.findall(item.get("speech") or ""))
        for r in reads:
            if set(_variants(r.get("s"))) & numbers:
                found.add(r["e"])
        if len(reads) == 1 and not found and not numbers:
            found.add(reads[0]["e"])  # a non-numeric answer from the only device read ("the window is open")
        candidates = found if candidates is None else candidates & found
    if candidates and len(candidates) == 1:
        return next(iter(candidates))
    return None


def answer_blocks_from(said: str, entity: str, value: Any, unit_words: list[str]) -> list[dict[str, Any]] | None:
    """The AI's answer with the value (and the unit word right after it) as a live value block.

    "Draußen sind es gerade 12,4 Grad." + 12.44 → ["Draußen sind es gerade ", {e, unit, r=1}, "."]
    """
    variants = _variants(value)
    if not variants:
        return None
    for m in NUM_RE.finditer(said or ""):
        token = m.group(0)
        if token not in variants:
            continue
        decimals = len(token.split(",")[-1].split(".")[-1]) if ("," in token or "." in token) else 0
        start, end = m.span()
        rest = said[end:]
        fmt = "value"
        for word in sorted((w for w in unit_words if w), key=len, reverse=True):
            mm = re.match(r"\s*" + re.escape(word) + r"(?![\w])", rest, re.I)
            if mm:
                end += mm.end()
                fmt = "unit"
                break
        return [{"t": "satz", "p": [p for p in (said[:start], {"e": entity, "f": fmt, "r": min(decimals, 2)}, said[end:]) if p != ""]}]
    return None


def command_name(entity_name: str) -> str:
    """A CamelCase command name from the device's name ("Außentemperatur" → "AussentemperaturFrage")."""
    table = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "é": "e", "è": "e", "à": "a"})
    words = re.findall(r"[A-Za-z0-9]+", (entity_name or "").translate(table))
    base = "".join(w[:1].upper() + w[1:] for w in words)[:48] or "Abfrage"
    if not base[0].isalpha():
        base = "Q" + base
    return base + "Frage"


def sentence_from(text: str) -> str:
    """A said question as a literal template (lowercase, no punctuation, no template syntax)."""
    return " ".join(re.sub(r"[^\w\s'’-]", " ", (text or "").lower()).split())
