"""Validation of proposed sentence templates and names (no Home Assistant).

Proposals come from an LLM, i.e. they are untrusted input. Everything here is
bounded: size limits before parsing, a variant count BEFORE sampling (hassil's
sampler materialises cross products — one nested template reached 25 GB in a
test), sampling with shrunk lists, and capped path enumeration.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from typing import Any, Callable, Iterable

from hassil.expression import (
    Alternative,
    Group,
    ListReference,
    Permutation,
    RuleReference,
    Sentence,
    Sequence,
    TextChunk,
)
from hassil.intents import Intents, RangeSlotList, SlotList, TextSlotList, WildcardSlotList
from hassil.parse_expression import parse_sentence
from hassil.sample import sample_sentence
from hassil.util import remove_punctuation

MAX_SENTENCE_CHARS = 250
MAX_GROUPS = 8
MAX_VARIANTS = 10000
MAX_PERMUTATION = 4
MAX_SAMPLES = 120
MAX_PATHS = 256
MAX_NAME_CHARS = 40
MAX_NAME_WORDS = 4
_NAME_FORBIDDEN = re.compile(r"[{}<>\[\]()|;:]")


def norm(text: str) -> str:
    """Normalisation used for every equality check (like the runtime)."""
    return " ".join(remove_punctuation(text).casefold().split())


# ── tree helpers ────────────────────────────────────────────────────────────


def _children(expr: Any) -> list[Any]:
    return list(getattr(expr, "items", []) or [])


def count_variants(
    expr: Any, sizes: dict[str, int], rules: dict[str, Sentence], _depth: int = 0,
    skip_optionals: bool = False,
) -> int:
    """Upper bound of expansions (capped at MAX_VARIANTS + 1)."""
    cap = MAX_VARIANTS + 1
    if _depth > 20:
        return cap
    if isinstance(expr, Sentence):
        return count_variants(expr.expression, sizes, rules, _depth + 1, skip_optionals)
    if isinstance(expr, TextChunk):
        return 1
    if isinstance(expr, ListReference):
        return max(1, sizes.get(expr.list_name, 1))
    if isinstance(expr, RuleReference):
        rule = rules.get(expr.rule_name)
        return count_variants(rule, sizes, rules, _depth + 1, skip_optionals) if rule else 1
    if isinstance(expr, Alternative):
        if skip_optionals and expr.is_optional:
            return 1
        return min(cap, sum(count_variants(c, sizes, rules, _depth + 1, skip_optionals) for c in _children(expr)))
    if isinstance(expr, Permutation):
        if len(_children(expr)) > MAX_PERMUTATION:
            return cap
        total = math.factorial(len(_children(expr)))
        for child in _children(expr):
            total = min(cap, total * count_variants(child, sizes, rules, _depth + 1, skip_optionals))
        return total
    if isinstance(expr, (Sequence, Group)):
        total = 1
        for child in _children(expr):
            total = min(cap, total * count_variants(child, sizes, rules, _depth + 1, skip_optionals))
        return total
    return 1


def slot_paths(
    expr: Any, rules: dict[str, Sentence], _depth: int = 0
) -> set[frozenset[tuple[str, str]]]:
    """Every possible set of (list, slot) a sentence fills, one per path."""
    if _depth > 20:
        return {frozenset()}
    if isinstance(expr, Sentence):
        return slot_paths(expr.expression, rules, _depth + 1)
    if isinstance(expr, ListReference):
        return {frozenset({(expr.list_name, expr.slot_name)})}
    if isinstance(expr, RuleReference):
        rule = rules.get(expr.rule_name)
        return slot_paths(rule, rules, _depth + 1) if rule else {frozenset()}
    if isinstance(expr, Alternative):
        out: set[frozenset[tuple[str, str]]] = set()
        for child in _children(expr):
            out |= slot_paths(child, rules, _depth + 1)
            if len(out) > MAX_PATHS:
                break
        return out or {frozenset()}
    if isinstance(expr, (Sequence, Group, Permutation)):
        acc: set[frozenset[tuple[str, str]]] = {frozenset()}
        for child in _children(expr):
            nxt = slot_paths(child, rules, _depth + 1)
            acc = {a | b for a in acc for b in nxt}
            if len(acc) > MAX_PATHS:
                acc = set(sorted(acc, key=lambda x: sorted(x))[:MAX_PATHS])
        return acc
    return {frozenset()}


def _list_refs(expr: Any, _depth: int = 0) -> list[str]:
    if _depth > 20:
        return []
    if isinstance(expr, Sentence):
        return _list_refs(expr.expression, _depth + 1)
    if isinstance(expr, ListReference):
        return [expr.list_name]
    return [r for c in _children(expr) for r in _list_refs(c, _depth + 1)]


def _has_required_literal(expr: Any, _depth: int = 0, rules: dict[str, Sentence] | None = None) -> bool:
    """True if some word is mandatory (not optional, not a slot)."""
    if _depth > 20:
        return False
    if isinstance(expr, Sentence):
        return _has_required_literal(expr.expression, _depth + 1, rules)
    if isinstance(expr, RuleReference):
        rule = (rules or {}).get(expr.rule_name)
        return _has_required_literal(rule, _depth + 1, rules) if rule else False
    if isinstance(expr, TextChunk):
        return bool(expr.text.strip())
    if isinstance(expr, Alternative):
        if expr.is_optional:
            return False
        kids = _children(expr)
        return bool(kids) and all(_has_required_literal(c, _depth + 1, rules) for c in kids)
    if isinstance(expr, (Sequence, Group, Permutation)):
        return any(_has_required_literal(c, _depth + 1, rules) for c in _children(expr))
    return False


# ── shrunk lists for sampling ───────────────────────────────────────────────


def _value_text(value: Any) -> str | None:
    text_in = getattr(value, "text_in", None)
    text = getattr(text_in, "text", None)
    if text is None and text_in is not None:
        text = getattr(text_in, "original_text", None)
    return text if isinstance(text, str) and text.strip() else None


def shrink_lists(
    slot_lists: dict[str, SlotList], per_list: int = 3, empty: set[str] | None = None
) -> dict[str, SlotList]:
    """At most ``per_list`` values per list: first, middle, last.

    Names of lists without any value are added to ``empty`` (if given).
    """
    out: dict[str, SlotList] = {}
    for name, sl in slot_lists.items():
        if isinstance(sl, TextSlotList):
            texts = [t for t in (_value_text(v) for v in sl.values) if t]
            if len(texts) > per_list:
                texts = [texts[0], texts[len(texts) // 2], texts[-1]]
            out[name] = TextSlotList.from_strings(texts or ["x"], allow_template=False, name=name)
            if not texts and empty is not None:
                empty.add(name)
        elif isinstance(sl, RangeSlotList):
            mid = sl.start + ((sl.stop - sl.start) // (2 * sl.step)) * sl.step
            vals = sorted({sl.start, mid, sl.stop})
            out[name] = TextSlotList.from_strings([str(v) for v in vals], allow_template=False, name=name)
        elif isinstance(sl, WildcardSlotList):
            out[name] = TextSlotList.from_strings(["milch"], allow_template=False, name=name)
    return out


def bounded_samples(
    sentence: Sentence,
    slot_lists: dict[str, SlotList],
    rules: dict[str, Sentence],
    language: str | None,
    limit: int = MAX_SAMPLES,
) -> list[str]:
    """Evenly spread samples over all (bounded) variants."""
    return bounded_samples_info(sentence, slot_lists, rules, language, limit)[0]


def bounded_samples_info(
    sentence: Sentence,
    slot_lists: dict[str, SlotList],
    rules: dict[str, Sentence],
    language: str | None,
    limit: int = MAX_SAMPLES,
) -> tuple[list[str], bool, set[str]]:
    """Samples, whether optional words were skipped, and empty lists used."""
    empty: set[str] = set()
    small = shrink_lists(slot_lists, empty=empty)
    sizes = {name: len(getattr(sl, "values", [])) or 1 for name, sl in small.items()}
    skip = False
    if count_variants(sentence, sizes, rules) > MAX_VARIANTS:
        # big but legitimate templates: check them without the optional words
        if count_variants(sentence, sizes, rules, skip_optionals=True) > MAX_VARIANTS:
            raise ValueError("too many variants")
        skip = True
    seen: dict[str, None] = {}
    for text in sample_sentence(
        sentence, slot_lists=small, expansion_rules=rules, language=language, skip_optionals=skip
    ):
        text = " ".join(text.split())
        if text:
            seen[text] = None
    texts = list(seen)
    if len(texts) > limit:
        step = len(texts) / limit
        texts = [texts[int(i * step)] for i in range(limit)]
    return texts, skip, empty


# ── proposal check ──────────────────────────────────────────────────────────


def parse_rules(rules: dict[str, str]) -> dict[str, Sentence]:
    out: dict[str, Sentence] = {}
    for name, text in rules.items():
        try:
            out[name] = parse_sentence(text)
        except Exception:  # noqa: BLE001
            continue
    return out


def _rule_refs(expr: Any) -> list[str]:
    """Names of all <rule> references in a parsed sentence."""
    if isinstance(expr, Sentence):
        return _rule_refs(expr.expression)
    if isinstance(expr, RuleReference):
        return [expr.rule_name]
    out: list[str] = []
    for item in _children(expr):
        out += _rule_refs(item)
    return out


def check_proposal(
    intent_name: str,
    sentence: str,
    source_sentences: list[str],
    matches: Callable[[str], set[str]],
    slot_lists: dict[str, SlotList],
    rules: dict[str, str] | None = None,
    language: str | None = None,
    other_samples: Iterable[tuple[str, str]] = (),
    static_lists: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate one proposed template for ``intent_name``.

    ``matches(text)`` returns ALL intents the current matcher recognises for
    a text (every language, hint = target language). ``other_samples`` are
    (intent, text) samples of the other intents in the target language, used
    to check the reverse direction (the proposal swallowing their sentences).

    status: ok | warn (redundant / not fully verifiable) | error.
    """
    result: dict[str, Any] = {"sentence": sentence, "status": "ok", "notes": [], "checked": 0}

    def _err(note: str) -> dict[str, Any]:
        return {**result, "status": "error", "notes": [note]}

    if not isinstance(sentence, str) or not sentence.strip():
        return _err("empty")
    if len(sentence) > MAX_SENTENCE_CHARS:
        return _err(f"longer than {MAX_SENTENCE_CHARS} characters")
    if sentence.count("(") + sentence.count("[") + sentence.count(";") > MAX_GROUPS:
        return _err("too many alternatives/optional parts")
    try:
        parsed = parse_sentence(sentence)
    except Exception as err:  # noqa: BLE001
        return _err(f"not parsable: {err}")
    refs = _list_refs(parsed)
    if len(refs) != len(set(refs)):
        return _err("a placeholder appears twice")
    missing = [r for r in refs if r not in slot_lists]
    if missing:
        return _err(f"unknown placeholder {{{missing[0]}}}")
    parsed_rules = parse_rules(rules or {})
    unknown_rules = sorted(r for r in _rule_refs(parsed) if r not in parsed_rules)
    if unknown_rules:  # also catches markup like <b> or <script>, which hassil reads as rules
        return _err(f"unknown rule <{unknown_rules[0]}>")
    if not _has_required_literal(parsed, rules=parsed_rules):
        return _err("needs at least one fixed word (not only optional parts or placeholders)")

    wanted = slot_paths(parsed, parsed_rules)
    source_paths: set[frozenset[tuple[str, str]]] = set()
    for src in source_sentences:
        try:
            source_paths |= slot_paths(parse_sentence(src), parsed_rules)
        except Exception:  # noqa: BLE001
            continue
    if source_paths and not wanted <= source_paths:
        odd = sorted(next(iter(wanted - source_paths)))
        return _err(f"placeholders {odd} differ from every source sentence")

    skipped, empty = False, set()
    try:
        samples, skipped, empty = bounded_samples_info(parsed, slot_lists, parsed_rules, language)
    except ValueError:
        return _err("too many variants")
    except Exception as err:  # noqa: BLE001
        result["status"] = "warn"
        result["notes"].append(f"not verifiable: {err}")
        samples = []
    result["checked"] = len(samples)
    if not samples:
        result["status"] = "warn"
        result["notes"].append("not verified (no samples)")
        return result

    collisions: list[dict[str, str]] = []
    same = 0
    for text in samples:
        hit = matches(text)
        if intent_name in hit:
            same += 1
        for other in sorted(hit - {intent_name}):
            collisions.append({"text": text, "intent": other})

    # reverse direction: would the proposal swallow sentences of other intents?
    if other_samples:
        try:
            alone = Intents.from_dict(
                {
                    "language": language or "xx",
                    "intents": {intent_name: {"data": [{"sentences": [sentence]}]}},
                    **({"lists": static_lists} if static_lists else {}),
                    **({"expansion_rules": rules} if rules else {}),
                }
            )
            from hassil.recognize import recognize_best  # noqa: PLC0415

            for other_intent, text in other_samples:
                if other_intent == intent_name:
                    continue
                if recognize_best(text, alone, slot_lists=slot_lists) is not None:
                    collisions.append({"text": text, "intent": other_intent, "direction": "swallows"})
                    if len(collisions) >= 10:
                        break
        except Exception as err:  # noqa: BLE001
            result["notes"].append(f"reverse check skipped: {err}")

    result["samples"] = samples[:5]
    used_empty = sorted(empty & set(refs))
    if collisions:
        result["status"] = "error"
        result["notes"].append("collides with other commands")
        result["collisions"] = collisions[:5]
    elif same == len(samples):
        result["status"] = "warn"
        result["notes"].append("already covered by existing sentences")
    elif skipped or used_empty:
        result["status"] = "warn"
        if skipped:
            result["notes"].append("checked without optional words (large template)")
        if used_empty:
            result["notes"].append(f"list {used_empty[0]} is empty — checked with a placeholder")
    return result


def clean_alternatives(sentence: str) -> str:
    """(x|x|y) → (x|y) — LLMs like to repeat alternatives."""

    def _fix(m: re.Match[str]) -> str:
        parts = list(dict.fromkeys(p.strip() for p in m.group(1).split("|")))
        return "(" + "|".join(parts) + ")" if len(parts) > 1 else parts[0]

    def _fix_opt(m: re.Match[str]) -> str:
        parts = list(dict.fromkeys(p.strip() for p in m.group(1).split("|")))
        return "[" + "|".join(parts) + "]"

    sentence = re.sub(r"\(([^()\[\]]*\|[^()\[\]]*)\)", _fix, sentence)
    return re.sub(r"\[([^()\[\]]*)\]", _fix_opt, sentence)


def check_name(name: Any, matches: Callable[[str], set[str]]) -> str | None:
    """None if ``name`` is a usable spoken name, else the reason."""
    if not isinstance(name, str) or not name.strip():
        return "empty"
    if len(name) > MAX_NAME_CHARS or len(name.split()) > MAX_NAME_WORDS:
        return "too long for a name"
    if _NAME_FORBIDDEN.search(name):
        return "contains template characters"
    if matches(name):
        return "is itself a command"
    return None


# ── proposal operations (applied by the queue) ──────────────────────────────

OPS = (
    "add_intent", "add_sentences", "remove_sentences", "set_field",
    "add_names", "remove_names", "set_list_value", "remove_list_value",
)
SETTABLE_FIELDS = ("category", "kind", "safety", "response_mode", "description", "llm_equivalent")
ORIGINS = ("new_devices", "starter", "monitor", "correction", "translation", "manual")


class QueueError(Exception):
    """Proposal cannot be applied (message safe to show)."""


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in value.items()}
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return sorted(value)
    if isinstance(value, list):
        return [_canonical(v) for v in value]
    return value


def proposal_id(item: dict[str, Any]) -> str:
    raw = json.dumps(
        _canonical({k: item.get(k) for k in ("op", "payload")}), sort_keys=True, ensure_ascii=False, default=str
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _intent(data: dict[str, Any], name: str) -> dict[str, Any]:
    intent_cfg = (data.get("intents") or {}).get(name)
    if not isinstance(intent_cfg, dict):
        raise QueueError(f"command {name} no longer exists")
    return intent_cfg


def apply_op(data: dict[str, Any], item: dict[str, Any]) -> None:
    """Apply one proposal to an intents document (in place). Raises QueueError."""
    try:
        _apply_op(data, item)
    except QueueError:
        raise
    except (KeyError, TypeError, AttributeError) as err:
        raise QueueError(f"malformed proposal ({err})") from None


def _apply_op(data: dict[str, Any], item: dict[str, Any]) -> None:
    op, p = item.get("op"), item.get("payload") or {}
    if op == "add_intent":
        intents = data.setdefault("intents", {})
        if p["name"] in intents:
            raise QueueError(f"a command named {p['name']} already exists")
        intents[p["name"]] = copy.deepcopy(p["intent"])
        category = p["intent"].get("category")
        if category and category != "Sonstiges":
            cats = data.setdefault("categories", [])
            if category not in cats:
                cats.append(category)
    elif op in ("add_sentences", "remove_sentences"):
        sentences = _intent(data, p["intent"]).setdefault("sentences", {})
        current = list(sentences.get(p["lang"]) or [])
        if op == "add_sentences":
            sentences[p["lang"]] = current + [s for s in p["sentences"] if s not in current]
        else:
            missing = [s for s in p["sentences"] if s not in current]
            if missing:
                raise QueueError(f"sentence {missing[0]!r} is no longer there")
            sentences[p["lang"]] = [s for s in current if s not in p["sentences"]]
            if not sentences[p["lang"]]:
                del sentences[p["lang"]]
    elif op == "set_field":
        if p["field"] not in SETTABLE_FIELDS:
            raise QueueError(f"field {p['field']} cannot be set by a proposal")
        target = _intent(data, p["intent"])
        if p.get("value") is None:
            target.pop(p["field"], None)
        else:
            target[p["field"]] = copy.deepcopy(p["value"])
        if p["field"] == "category" and p.get("value") and p["value"] != "Sonstiges":
            cats = data.setdefault("categories", [])
            if p["value"] not in cats:
                cats.append(p["value"])
    elif op in ("add_names", "remove_names"):
        per_lang = data.setdefault("names", {}).setdefault(p["target"], {})
        current = list(per_lang.get(p["lang"]) or [])
        if op == "add_names":
            per_lang[p["lang"]] = current + [n for n in p["names"] if n not in current]
        else:
            if not any(n in current for n in p["names"]):
                raise QueueError("these names are no longer there")
            per_lang[p["lang"]] = [n for n in current if n not in p["names"]]
            if not per_lang[p["lang"]]:
                del per_lang[p["lang"]]
            if not per_lang:
                del data["names"][p["target"]]
    elif op in ("set_list_value", "remove_list_value"):
        lst = (data.get("lists") or {}).get(p["list"])
        if not isinstance(lst, dict) or not isinstance(lst.get("values"), list):
            raise QueueError(f"list {p['list']} has no editable values")
        values = [v for v in lst["values"] if not (isinstance(v, dict) and v.get("in") == p["in"]) and v != p["in"]]
        if op == "set_list_value":
            values.append({"in": p["in"], "out": p["out"]})
        elif len(values) == len(lst["values"]):
            raise QueueError(f"{p['in']!r} is no longer in list {p['list']}")
        lst["values"] = values
    else:
        raise QueueError(f"unknown proposal type {op}")




# ── actions a proposal may bring along ──────────────────────────────────────
# Proposals are generated (partly by an LLM). They may only carry simple,
# harmless actions built from this allowlist; anything security-relevant
# (locks, alarms, garage doors, valves, …) can only be created by hand.
SAFE_ACTIONS = {
    "script.turn_on", "scene.turn_on", "button.press", "input_button.press",
    "light.turn_on", "light.turn_off", "light.toggle",
    "switch.turn_on", "switch.turn_off", "switch.toggle",
    "fan.turn_on", "fan.turn_off", "media_player.media_play", "media_player.media_pause",
    "media_player.turn_on", "media_player.turn_off",
}
ENTITY_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
TARGET_KEYS = {"entity_id", "device_id", "area_id", "floor_id", "label_id"}
# Words for doors, gates, garages, locks, openers, alarms and cars (de/en/fr).
# German compounds ("Gartentor", "Haustüröffner", "Kellertür", "Rolltor")
# are caught as word parts; "tor" only when not part of Motor/Monitor/
# Ventilator/Sektor …
PROTECTED_WORDS = re.compile(
    r"tür|tuer|schloss|schlöss|garage|öffner|oeffner|riegel|"
    r"(?<![aiouck])tor(e|en|s)?(?=$|[^a-zäöü]|antrieb|steuer|öffn|oeffn|motor|relais|schalt|kontakt|sensor)|"
    r"(?<![a-zäöü])tor(?!te|tel|f|nad|nist)|"
    r"einfahrt|pforte|schranke|driveway|barrier|portillon|barrière|"
    r"door|(?<![a-z])gates?(?![a-z])|(?<![c])lock|unlock|opener|deadbolt|"
    r"(?<![a-z])porte|portail|serrure|verrou|"
    r"alarm|sirene|siren|"
    r"(?<![a-z])(auto|car|cars|fahrzeug|vehicle|voiture|summer)(?![a-z])|"
    r"tesla|frunk|(?<![a-z])trunk|kofferraum|(?<![a-z])coffre|ladeklappe",
    re.I,
)


def looks_protected(text: str) -> bool:
    return bool(text) and PROTECTED_WORDS.search(text.replace("_", " ").replace("-", " ").lower()) is not None


MAX_STEPS = 4
MAX_STEP_ENTITIES = 20
# data a proposal step may carry, per action (everything else: no data at all)
STEP_DATA = {
    "light.turn_on": {"brightness_pct": (0, 100), "color_temp_kelvin": (1500, 9000)},
}


def iter_step_entities(step: Any) -> list[str] | None:
    """The entity ids a step targets (string or list), or None if the target is not plain."""
    if not isinstance(step, dict):
        return None
    target = step.get("target")
    if not isinstance(target, dict) or set(target) - {"entity_id"}:
        return None
    entity = target.get("entity_id")
    entities = [entity] if isinstance(entity, str) else entity
    if not isinstance(entities, list) or not entities or not all(isinstance(e, str) for e in entities):
        return None
    return entities


def action_entity_reason(action: Any, reason_of: Callable[[str], str | None]) -> str | None:
    """First reason ``reason_of`` gives for ANY entity of ANY step (string or list targets).

    A step whose target is not a plain entity list is refused as well — nothing
    can slip past the per-entity check by its shape.
    """
    for step in action if isinstance(action, list) else [action]:
        if not step:
            continue
        entities = iter_step_entities(step)
        if entities is None:
            return "only plain entity targets can be accepted from a proposal"
        for entity in entities:
            reason = reason_of(entity)
            if reason:
                return reason
    return None


def check_action_safe(action: Any) -> str | None:
    """Static check: a few allowed steps on plain entity targets, else the reason.

    The dynamic check against the real entities (domain, device class, names,
    what a script/scene touches, exposure) happens when a proposal is accepted
    (accept.dynamic_reason → safety.action_target_reason) — for EVERY entity
    of every step (iter_step_entities).
    """
    steps = action if isinstance(action, list) else [action]
    if not steps or len(steps) > MAX_STEPS:
        return f"a proposal may contain 1 to {MAX_STEPS} simple steps"
    for step in steps:
        if not isinstance(step, dict) or set(step) - {"action", "target", "data"}:
            return "only simple action steps are allowed in proposals"
        service = step.get("action")
        if service not in SAFE_ACTIONS:
            return f"{service} cannot be created by a proposal — add it by hand"
        entities = iter_step_entities(step)
        if entities is None:
            return "a proposal step targets entities by id only (no areas, devices or labels)"
        if len(entities) > MAX_STEP_ENTITIES or len(set(entities)) != len(entities):
            return f"a proposal step targets 1 to {MAX_STEP_ENTITIES} different entities"
        for entity in entities:
            if not ENTITY_RE.match(entity) or entity.split(".", 1)[0] != service.split(".", 1)[0]:
                return "every target must be an entity of the action's domain"
            if looks_protected(entity):
                return f"{entity} looks security-relevant — add it by hand"
        data = step.get("data")
        if data in (None, {}):
            continue
        allowed = STEP_DATA.get(service, {})
        if not isinstance(data, dict) or set(data) - set(allowed):
            return f"{service} may carry only {', '.join(sorted(allowed)) or 'no data'} in a proposal"
        for key, value in data.items():
            low, high = allowed[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
                return f"{key} must be a number from {low} to {high}"
    return None


_LANG = re.compile(r"^[a-z]{2,3}(-[a-z]{2,4})?$")


def check_item(item: dict[str, Any]) -> str | None:
    """Static safety check of a queue item before it may be accepted."""
    p = item.get("payload") or {}
    if "lang" in p and (not isinstance(p["lang"], str) or not _LANG.match(p["lang"])):
        return "invalid language code"
    if item.get("op") in ("add_names", "remove_names") and not re.match(r"^[a-z_]+\.[a-z0-9_]+$", str(p.get("target", ""))):
        return "invalid name target"
    if item.get("op") == "add_intent":
        for lang in ((p.get("intent") or {}).get("sentences") or {}):
            if not isinstance(lang, str) or not _LANG.match(lang):
                return "invalid language code"
    if item.get("op") == "add_intent":
        intent = (item.get("payload") or {}).get("intent") or {}
        if "action" in intent:
            reason = check_action_safe(intent["action"])
            if reason:
                return reason
        if intent.get("safety") not in (None, "normal", "sensitive", "disabled"):
            return "invalid safety level"
        for text in (intent.get("speech") or {}).values():
            if not isinstance(text, str) or "{%" in text or len(text) > 200:
                return "answers must be short plain text"
        if "llm_equivalent" in intent and _bad_equivalent(intent["llm_equivalent"]):
            return "invalid llm_equivalent"
    if item.get("op") == "set_field" and (item.get("payload") or {}).get("field") == "llm_equivalent":
        if _bad_equivalent((item.get("payload") or {}).get("value")):
            return "invalid llm_equivalent"
    return None


def _bad_equivalent(value: Any) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get("tool"), str) or not re.match(r"^[A-Za-z0-9_]+$", value["tool"]):
        return True
    args = value.get("args") or {}
    return not isinstance(args, dict) or any(not isinstance(v, str) or len(v) > 80 for v in args.values())


SAFETY_ORDER = {"normal": 0, None: 0, "sensitive": 1, "disabled": 2}


def check_item_against(item: dict[str, Any], data: dict[str, Any]) -> str | None:
    """Checks that need the current intents document (no Home Assistant)."""
    op, p = item.get("op"), item.get("payload") or {}
    intents = data.get("intents") or {}
    if op in ("add_sentences", "remove_sentences", "set_field") and isinstance(intents.get(p.get("intent")), dict):
        current = intents[p["intent"]].get("safety")
        if op == "set_field" and p.get("field") == "safety":
            if SAFETY_ORDER.get(p.get("value"), 2) < SAFETY_ORDER.get(current, 2):
                return "a proposal can only make a command stricter — lower the protection by hand"
        elif current not in (None, "normal"):
            return "security-relevant or switched-off command — change it by hand"
    return None
