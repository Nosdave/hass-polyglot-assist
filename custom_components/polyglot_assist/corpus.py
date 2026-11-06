"""Pure helpers for building, validating and regression-testing intents.

This module deliberately imports nothing from Home Assistant so it can be used
from the integration, from the offline CLI (``scripts/polyglot_test.py``) and
from the CI test-suite alike. Only ``hassil`` and ``PyYAML`` are required.

Corpus file format (YAML)::

    cases:
      - text: "turn on the kitchen light"
        intent: LightOn            # expected intent, or null for "must not match"
        lang: en                   # optional: expected language
        slots:                     # optional: expected slot values (subset check)
          room: light.kitchen
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from hassil.intents import Intents

# {list} or {list:slot}
_LIST_REF = re.compile(r"\{([A-Za-z0-9_]+)(?::[A-Za-z0-9_]+)?\}")
# <rule>
_RULE_REF = re.compile(r"<([A-Za-z0-9_]+)>")


def bucket_by_language(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Split the user-facing ``intents:`` section into hassil dicts per language."""
    per_lang: dict[str, dict[str, Any]] = {}
    for intent_name, intent_cfg in (data.get("intents") or {}).items():
        sentences = (intent_cfg or {}).get("sentences", {}) or {}
        if not isinstance(sentences, dict):
            continue
        for lang, sent_list in sentences.items():
            per_lang.setdefault(lang, {})[intent_name] = {
                "data": [{"sentences": list(sent_list or [])}],
            }
    return per_lang


def build_intents(data: dict[str, Any]) -> dict[str, Intents]:
    """Build one ``hassil.Intents`` per language from a parsed intents file.

    Raises on the first language that hassil cannot parse, so callers decide
    whether to log-and-skip (runtime) or fail (tests/CI).
    """
    lists_section = data.get("lists") or {}
    rules_section = data.get("expansion_rules") or {}
    result: dict[str, Intents] = {}
    for lang, intents_dict in bucket_by_language(data).items():
        payload: dict[str, Any] = {"language": lang, "intents": intents_dict}
        if lists_section:
            payload["lists"] = lists_section
        if rules_section:
            payload["expansion_rules"] = rules_section
        result[lang] = Intents.from_dict(payload)
    return result


def validate(data: dict[str, Any], extra_lists: set[str] | None = None) -> list[str]:
    """Return human-readable problems that hassil only reports at match time.

    hassil raises ``MissingListError`` lazily, *while matching*. The runtime
    matcher swallows that per language, which silently disables matching for
    every utterance that reaches the broken template. Catch it up front.
    ``extra_lists`` names lists that are supplied at runtime (e.g. ``area``).
    """
    problems: list[str] = []
    intents = data.get("intents") or {}
    if not intents:
        problems.append("no intents defined")
    known_lists = set((data.get("lists") or {}).keys()) | (extra_lists or set())
    known_rules = set((data.get("expansion_rules") or {}).keys())
    for name, cfg in intents.items():
        sentences = (cfg or {}).get("sentences")
        if not isinstance(sentences, dict) or not sentences:
            problems.append(f"{name}: no sentences")
            continue
        for lang, sent_list in sentences.items():
            for sentence in sent_list or []:
                for ref in _LIST_REF.findall(sentence):
                    if ref not in known_lists:
                        problems.append(f"{name}/{lang}: unknown list {{{ref}}} in \"{sentence}\"")
                for ref in _RULE_REF.findall(sentence):
                    if ref not in known_rules:
                        problems.append(f"{name}/{lang}: unknown rule <{ref}> in \"{sentence}\"")
    return problems


def count_summary(data: dict[str, Any]) -> dict[str, Any]:
    """Intent and sentence counts per language (for diagnostics/sensors)."""
    per_lang = bucket_by_language(data)
    return {
        "intents": len(data.get("intents") or {}),
        "lists": len(data.get("lists") or {}),
        "sentences": {
            lang: sum(len(v["data"][0]["sentences"]) for v in intents.values())
            for lang, intents in per_lang.items()
        },
    }


@dataclass(slots=True)
class Case:
    """One corpus expectation."""

    text: str
    intent: str | None
    lang: str | None = None
    slots: dict[str, Any] = field(default_factory=dict)


def parse_corpus(raw: Any) -> list[Case]:
    """Parse a corpus document (already YAML-decoded)."""
    items = raw.get("cases") if isinstance(raw, dict) else raw
    cases: list[Case] = []
    for item in items or []:
        if not isinstance(item, dict) or "text" not in item:
            raise ValueError(f"invalid corpus entry: {item!r}")
        cases.append(
            Case(
                text=str(item["text"]),
                intent=item.get("intent"),
                lang=item.get("lang"),
                slots=dict(item.get("slots") or {}),
            )
        )
    return cases


# match_fn(text) -> (intent_name, lang, {slot: value}) or None
MatchFn = Callable[[str], "tuple[str, str, dict[str, Any]] | None"]


def run_corpus(match_fn: MatchFn, cases: list[Case]) -> dict[str, Any]:
    """Run every case and report failures (expected vs. got)."""
    failed: list[dict[str, Any]] = []
    for case in cases:
        got = match_fn(case.text)
        got_intent = got[0] if got else None
        ok = got_intent == case.intent
        if ok and got and case.lang and got[1] != case.lang:
            ok = False
        if ok and got and case.slots:
            got_slots = got[2]
            ok = all(str(got_slots.get(k)) == str(v) for k, v in case.slots.items())
        if not ok:
            failed.append(
                {
                    "text": case.text,
                    "expected": {"intent": case.intent, "lang": case.lang, "slots": case.slots},
                    "got": (
                        {"intent": got[0], "lang": got[1], "slots": got[2]} if got else None
                    ),
                }
            )
    return {"total": len(cases), "passed": len(cases) - len(failed), "failed": failed}
