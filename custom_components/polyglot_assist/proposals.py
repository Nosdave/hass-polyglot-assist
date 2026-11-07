"""Validation of proposed sentence templates and names (no Home Assistant).

Proposals come from an LLM, i.e. they are untrusted input. Everything here is
bounded: size limits before parsing, a variant count BEFORE sampling (hassil's
sampler materialises cross products — one nested template reached 25 GB in a
test), sampling with shrunk lists, and capped path enumeration.
"""
from __future__ import annotations

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


def _has_required_literal(expr: Any, _depth: int = 0) -> bool:
    """True if some word is mandatory (not optional, not a slot)."""
    if _depth > 20:
        return False
    if isinstance(expr, Sentence):
        return _has_required_literal(expr.expression, _depth + 1)
    if isinstance(expr, TextChunk):
        return bool(expr.text.strip())
    if isinstance(expr, Alternative):
        if expr.is_optional:
            return False
        kids = _children(expr)
        return bool(kids) and all(_has_required_literal(c, _depth + 1) for c in kids)
    if isinstance(expr, (Sequence, Group, Permutation)):
        return any(_has_required_literal(c, _depth + 1) for c in _children(expr))
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
    if not _has_required_literal(parsed):
        return _err("needs at least one fixed word (not only optional parts or placeholders)")

    parsed_rules = parse_rules(rules or {})
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
