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
from hassil.parse_expression import parse_sentence

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


def names_table(data: dict[str, Any]) -> dict[str, list[str]]:
    """``names:`` → {target_id: [all names of all languages]} (strings only)."""
    out: dict[str, list[str]] = {}
    for target, per_lang in (data.get("names") or {}).items():
        if not isinstance(per_lang, dict):
            continue
        names = [
            n.strip()
            for values in per_lang.values()
            for n in (values if isinstance(values, list) else [values])
            if isinstance(n, str) and n.strip()
        ]
        if names:
            out[str(target)] = list(dict.fromkeys(names))
    return out


def static_lists(data: dict[str, Any]) -> dict[str, Any]:
    """Lists hassil can build itself (values / range / wildcard)."""
    dyn = dynamic_lists(data)
    return {k: v for k, v in (data.get("lists") or {}).items() if k not in dyn}


def referenced_lists(data: dict[str, Any]) -> set[str]:
    """List names used by any sentence or expansion rule (not slot aliases)."""
    texts: list[str] = list(string_rules(data).values())
    for cfg in (data.get("intents") or {}).values():
        if isinstance(cfg, dict) and isinstance(cfg.get("sentences"), dict):
            for lst in cfg["sentences"].values():
                if isinstance(lst, list):
                    texts.extend(x for x in lst if isinstance(x, str))
    return {ref for t in texts for ref in _LIST_REF.findall(t)}


def runtime_list_names(data: dict[str, Any]) -> set[str]:
    """Names supplied at runtime: built-ins not overridden + dynamic lists."""
    static = set(static_lists(data))
    return {b for b in BUILTIN_LISTS if b not in static} | set(dynamic_lists(data))


def slot_lists_from_fixture(raw: dict[str, Any] | None) -> dict[str, SlotList]:
    """Simulated runtime lists for offline tests: {list: [str | {in, out}]}."""
    out: dict[str, SlotList] = {}
    for name, items in (raw or {}).items():
        tuples = []
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and "in" in item:
                tuples.append((str(item["in"]), item.get("out", item["in"])))
            elif isinstance(item, str):
                tuples.append((item, item))
        out[name] = TextSlotList.from_tuples(tuples, allow_template=False, name=name)
    return out


SAFETY_LEVELS = ("normal", "sensitive", "disabled")


def is_active(intent_cfg: dict[str, Any]) -> bool:
    """Fail closed: anything but normal/sensitive/unset counts as disabled."""
    return intent_cfg.get("safety") in (None, "normal", "sensitive")


def bucket_by_language(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Split the user-facing ``intents:`` section into hassil dicts per language.

    Intents with ``safety: disabled`` take no part in matching.
    """
    per_lang: dict[str, dict[str, Any]] = {}
    for intent_name, intent_cfg in (data.get("intents") or {}).items():
        if not isinstance(intent_cfg, dict) or not is_active(intent_cfg):
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
        kinds = [k for k in DYNAMIC_KEYS if k in spec]
        if len(kinds) != 1 or any(k in spec for k in ("values", "range", "wildcard")):
            problems.append(f"list {list_name}: use exactly one of {', '.join(DYNAMIC_KEYS)}")
            continue
        body = spec[kinds[0]]
        if kinds[0] == "template":
            if not isinstance(body, str):
                problems.append(f"list {list_name}: template must be a string")
        elif body is not None and not isinstance(body, dict):
            problems.append(f"list {list_name}: {kinds[0]} must be a mapping")
        elif kinds[0] == "entities":
            for key, value in (body or {}).items():
                if key not in ("domain", "area", "exclude", "exposed"):
                    problems.append(f"list {list_name}: unknown key entities.{key}")
                elif key == "exposed" and not isinstance(value, bool):
                    problems.append(f"list {list_name}: entities.exposed must be true/false")
                elif key != "exposed" and not (
                    isinstance(value, str)
                    or (isinstance(value, list) and all(isinstance(v, str) for v in value))
                ):
                    problems.append(f"list {list_name}: entities.{key} must be a string or list of strings")
    known_rules = set(string_rules(data))
    names = data.get("names") or {}
    if not isinstance(names, dict):
        problems.append("names must be a mapping of entity_id / area.<id> → {lang: [names]}")
    else:
        seen: dict[str, str] = {}
        for target, per_lang in names.items():
            if not isinstance(per_lang, dict):
                problems.append(f"names.{target}: must be a mapping of language → list of names")
                continue
            if "." not in str(target):
                problems.append(f"names.{target}: use an entity_id or area.<area_id>")
            for name in names_table({"names": {target: per_lang}}).get(str(target), []):
                key = name.casefold()
                if key in seen and seen[key] != target:
                    problems.append(f"names: {name!r} is used for {seen[key]} and {target}")
                seen.setdefault(key, str(target))
    raw_rules = data.get("expansion_rules") or {}
    if not isinstance(raw_rules, dict):
        problems.append("expansion_rules must be a mapping")
        raw_rules = {}
    for rule_name, rule in raw_rules.items():
        if not isinstance(rule, str):
            problems.append(f"expansion rule <{rule_name}> is not a string (ignored)")
            continue
        try:
            parse_sentence(rule)
        except Exception as err:  # noqa: BLE001
            problems.append(f"expansion rule <{rule_name}>: cannot parse: {err}")
            continue
        problems.extend(_check_refs(f"expansion rule <{rule_name}>", rule, known_lists, known_rules))
    for name, cfg in intents.items():
        if cfg is not None and not isinstance(cfg, dict):
            problems.append(f"{name}: intent must be a mapping")
            continue
        safety = (cfg or {}).get("safety")
        if safety is not None and safety not in SAFETY_LEVELS:
            problems.append(f"{name}: unknown safety level {safety!r} (treated as disabled)")
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
                try:
                    parse_sentence(sentence)
                except Exception as err:  # noqa: BLE001 — one broken template kills the language
                    problems.append(f"{name}/{lang}: cannot parse \"{sentence}\": {err}")
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
    intents = data.get("intents") or {}
    per_lang = bucket_by_language(data)

    def _with(level: str) -> list[str]:
        if level == "disabled":
            return sorted(n for n, c in intents.items() if isinstance(c, dict) and not is_active(c))
        return sorted(n for n, c in intents.items() if isinstance(c, dict) and c.get("safety") == level)

    return {
        "intents": len(intents),
        "active": len({n for lang in per_lang.values() for n in lang}),
        "disabled": _with("disabled"),
        "sensitive": _with("sensitive"),
        "lists": len(data.get("lists") or {}),
        "dynamic_lists": sorted(dynamic_lists(data)),
        "sentences": {
            lang: sum(len(v["data"][0]["sentences"]) for v in lang_intents.values())
            for lang, lang_intents in per_lang.items()
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
    for index, case in enumerate(cases):
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
                    "index": index,
                    "text": case.text,
                    "expected": {"intent": case.intent, "lang": case.lang, "slots": case.slots},
                    "got": (
                        {"intent": got[0], "lang": got[1], "slots": got[2]} if got else None
                    ),
                }
            )
    return {"total": len(cases), "passed": len(cases) - len(failed), "failed": failed}


# ── translation proposals (pure validation, used by translate.py) ───────────


def capture_lists(sentence: str) -> frozenset[str]:
    """List names a sentence fills (``{list}`` / ``{list:slot}`` → slot name)."""
    out = set()
    for m in re.finditer(r"(?<!\{)\{([^{}:]+)(?::([^{}]+))?\}(?!\})", sentence):
        out.add((m.group(2) or m.group(1)).strip())
    return frozenset(out)


def check_proposal(
    intent_name: str,
    sentence: str,
    source_sentences: list[str],
    match_fn: Callable[[str], "tuple[str, str, dict[str, Any]] | None"],
    slot_lists: dict[str, SlotList] | None = None,
    expansion_rules: dict[str, str] | None = None,
    language: str | None = None,
    max_samples: int = 40,
) -> dict[str, Any]:
    """Validate one proposed template sentence for ``intent_name``.

    error: unparsable, different slots than every source sentence, or a
    sample expansion already matches ANOTHER intent (collision).
    warn:  every sample already matches this intent (redundant).
    """
    from hassil.parse_expression import parse_sentence  # noqa: PLC0415
    from hassil.sample import sample_sentence  # noqa: PLC0415

    result: dict[str, Any] = {"sentence": sentence, "status": "ok", "notes": []}
    try:
        parsed = parse_sentence(sentence)
    except Exception as err:  # noqa: BLE001
        return {**result, "status": "error", "notes": [f"not parsable: {err}"]}
    wanted = capture_lists(sentence)
    if source_sentences and wanted not in {capture_lists(s) for s in source_sentences}:
        return {**result, "status": "error",
                "notes": [f"slots {sorted(wanted)} differ from every source sentence"]}
    rules = {}
    for name, text in (expansion_rules or {}).items():
        try:
            rules[name] = parse_sentence(text)
        except Exception:  # noqa: BLE001
            continue
    samples: list[str] = []
    try:
        for text in sample_sentence(parsed, slot_lists=slot_lists or {},
                                    expansion_rules=rules, language=language):
            text = " ".join(text.split())
            if text and text not in samples:
                samples.append(text)
            if len(samples) >= max_samples:
                break
    except Exception as err:  # noqa: BLE001 — e.g. a runtime list without values
        result["notes"].append(f"could not sample: {err}")
    collisions, same = [], 0
    for text in samples:
        got = match_fn(text)
        if got is None:
            continue
        if got[0] == intent_name:
            same += 1
        else:
            collisions.append({"text": text, "intent": got[0]})
    result["samples"] = samples[:5]
    if collisions:
        result["status"] = "error"
        result["notes"].append("collides with other intents")
        result["collisions"] = collisions[:5]
    elif samples and same == len(samples):
        result["status"] = "warn"
        result["notes"].append("already covered by existing sentences")
    return result
