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

from hassil.intents import Intents, SlotList, TextSlotList

# {list} or {list:slot}
_LIST_REF = re.compile(r"(?<!\{)\{([^{}:]+)(?::[^{}]+)?\}(?!\})")
# <rule>
_RULE_REF = re.compile(r"<([^<>\s]*[^<>\s\d][^<>\s]*)>")


# A list with one of these keys is resolved at runtime (see dynlists.py)
DYNAMIC_KEYS = ("entities", "areas", "template")
# Lists every installation gets at runtime unless intents.yaml defines them
BUILTIN_LISTS = ("area", "name")


def dynamic_lists(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Lists declared with ``entities``/``areas``/``template``."""
    return {
        name: spec
        for name, spec in (data.get("lists") or {}).items()
        if isinstance(spec, dict) and any(k in spec for k in DYNAMIC_KEYS)
    }


def static_lists(data: dict[str, Any]) -> dict[str, Any]:
    """Lists hassil can build itself (values / range / wildcard)."""
    dyn = dynamic_lists(data)
    return {k: v for k, v in (data.get("lists") or {}).items() if k not in dyn}


def runtime_list_names(data: dict[str, Any]) -> set[str]:
    """Names supplied at runtime: built-ins not overridden + dynamic lists."""
    static = set(static_lists(data))
    return {b for b in BUILTIN_LISTS if b not in static} | set(dynamic_lists(data))


def slot_lists_from_fixture(raw: dict[str, Any] | None) -> dict[str, SlotList]:
    """Simulated runtime lists for offline tests: {list: [str | {in, out}]}."""
    out: dict[str, SlotList] = {}
    for name, items in (raw or {}).items():
        tuples = []
        for item in items or []:
            if isinstance(item, dict):
                tuples.append((str(item["in"]), item.get("out", item["in"])))
            else:
                tuples.append((str(item), str(item)))
        out[name] = TextSlotList.from_tuples(tuples, allow_template=False, name=name)
    return out


def bucket_by_language(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Split the user-facing ``intents:`` section into hassil dicts per language."""
    per_lang: dict[str, dict[str, Any]] = {}
    for intent_name, intent_cfg in (data.get("intents") or {}).items():
        if not isinstance(intent_cfg, dict):
            continue
        sentences = intent_cfg.get("sentences") or {}
        if not isinstance(sentences, dict):
            continue
        for lang, sent_list in sentences.items():
            if isinstance(sent_list, str):
                continue  # list("abc") would split into letters; validate() reports it
            per_lang.setdefault(lang, {})[intent_name] = {
                # non-strings (YAML `- yes` → True) would break the whole
                # language at match time; validate() reports them
                "data": [{"sentences": [x for x in (sent_list or []) if isinstance(x, str)]}],
            }
    return per_lang


def string_rules(data: dict[str, Any]) -> dict[str, str]:
    """Only string-valued expansion rules (anything else breaks every language)."""
    rules = data.get("expansion_rules") or {}
    if not isinstance(rules, dict):
        return {}
    return {k: v for k, v in rules.items() if isinstance(v, str)}


def build_intents(data: dict[str, Any]) -> dict[str, Intents]:
    """Build one ``hassil.Intents`` per language from a parsed intents file.

    Raises on the first language that hassil cannot parse, so callers decide
    whether to log-and-skip (runtime) or fail (tests/CI).
    """
    lists_section = static_lists(data)
    rules_section = string_rules(data)
    result: dict[str, Intents] = {}
    for lang, intents_dict in bucket_by_language(data).items():
        payload: dict[str, Any] = {"language": lang, "intents": intents_dict}
        if lists_section:
            payload["lists"] = lists_section
        if rules_section:
            payload["expansion_rules"] = rules_section
        result[lang] = Intents.from_dict(payload)
    return result


def validate(
    data: dict[str, Any], extra_lists: set[str] | None = None, builtins: bool = True
) -> list[str]:
    """Return human-readable problems that hassil only reports at match time.

    hassil raises ``MissingListError`` lazily, *while matching*. The runtime
    matcher swallows that per language, which silently disables matching for
    every utterance that reaches the broken template. Catch it up front.
    ``extra_lists`` names further lists supplied at runtime; the built-in
    ``area``/``name`` lists count as known unless ``builtins`` is False.
    """
    problems: list[str] = []
    intents = data.get("intents") or {}
    if not intents:
        problems.append("no intents defined")
    known_lists = (
        set((data.get("lists") or {}).keys())
        | (extra_lists or set())
        | (set(BUILTIN_LISTS) if builtins else set())
    )
    for list_name, spec in dynamic_lists(data).items():
        if sum(k in spec for k in DYNAMIC_KEYS) != 1:
            problems.append(f"list {list_name}: use exactly one of {', '.join(DYNAMIC_KEYS)}")
    known_rules = set(string_rules(data))
    raw_rules = data.get("expansion_rules") or {}
    if not isinstance(raw_rules, dict):
        problems.append("expansion_rules must be a mapping")
        raw_rules = {}
    for rule_name, rule in raw_rules.items():
        if not isinstance(rule, str):
            problems.append(f"expansion rule <{rule_name}> is not a string (ignored)")
            continue
        problems.extend(_check_refs(f"expansion rule <{rule_name}>", rule, known_lists, known_rules))
    for name, cfg in intents.items():
        if cfg is not None and not isinstance(cfg, dict):
            problems.append(f"{name}: intent must be a mapping")
            continue
        sentences = (cfg or {}).get("sentences")
        if sentences is not None and not isinstance(sentences, dict):
            problems.append(f"{name}: 'sentences' must be a mapping keyed by language")
            continue
        if not sentences:
            problems.append(f"{name}: no sentences")
            continue
        for lang, sent_list in sentences.items():
            if isinstance(sent_list, str):
                problems.append(f"{name}/{lang}: sentences must be a list, not a string")
                continue
            for sentence in sent_list or []:
                if not isinstance(sentence, str):
                    problems.append(f"{name}/{lang}: sentence is not a string: {sentence!r}")
                    continue
                problems.extend(_check_refs(f"{name}/{lang}", sentence, known_lists, known_rules))
    return problems


def _check_refs(where: str, text: str, lists: set[str], rules: set[str]) -> list[str]:
    out = [
        f"{where}: unknown list {{{ref}}} in \"{text}\""
        for ref in _LIST_REF.findall(text)
        if ref not in lists
    ]
    out += [
        f"{where}: unknown rule <{ref}> in \"{text}\""
        for ref in _RULE_REF.findall(text)
        if ref not in rules
    ]
    return out


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
    hint: str | None = None  # pipeline language used as matcher hint


def parse_corpus(raw: Any) -> list[Case]:
    """Parse a corpus document (already YAML-decoded).

    Strict on purpose: an empty corpus or a case without an explicit
    ``intent`` key would otherwise pass silently. Error messages carry the
    case index only, never file content.
    """
    items = raw.get("cases") if isinstance(raw, dict) else raw
    if not isinstance(items, list) or not items:
        raise ValueError("corpus has no 'cases' list or it is empty")
    cases: list[Case] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            raise ValueError(f"case #{idx}: needs a 'text' string")
        if "intent" not in item:
            raise ValueError(f"case #{idx}: needs an explicit 'intent' (use null for no match)")
        slots = item.get("slots") or {}
        if not isinstance(slots, dict):
            raise ValueError(f"case #{idx}: 'slots' must be a mapping")
        cases.append(
            Case(
                text=item["text"],
                intent=item["intent"],
                lang=item.get("lang"),
                slots=dict(slots),
                hint=item.get("hint"),
            )
        )
    return cases


# match_fn(text, hint) -> (intent_name, lang, {slot: value}) or None
MatchFn = Callable[[str, "str | None"], "tuple[str, str, dict[str, Any]] | None"]


def run_corpus(
    match_fn: MatchFn, cases: list[Case], default_hint: str | None = None
) -> dict[str, Any]:
    """Run every case and report failures (expected vs. got).

    ``default_hint`` mirrors the pipeline language the runtime passes to the
    matcher (it is tried first); a case's own ``hint`` overrides it.
    """
    failed: list[dict[str, Any]] = []
    for case in cases:
        got = match_fn(case.text, case.hint or default_hint)
        got_intent = got[0] if got else None
        ok = got_intent == case.intent
        if ok and got and case.lang and got[1] != case.lang:
            ok = False
        if ok and got and case.slots:
            got_slots = got[2]
            ok = all(k in got_slots and str(got_slots[k]) == str(v) for k, v in case.slots.items())
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
