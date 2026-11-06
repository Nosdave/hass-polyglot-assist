"""Matcher regression tests (no Home Assistant needed: hassil + PyYAML only)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "custom_components" / "polyglot_assist"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_pg_{name}", PKG / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


corpus = _load("corpus")
matcher_mod = _load("matcher")


def _matcher(data: dict, fixture: dict | None = None):
    m = matcher_mod.MultilangMatcher(corpus.build_intents(data))
    slot_lists = corpus.slot_lists_from_fixture(fixture)

    def fn(text: str, hint: str | None = None):
        res = m.match(text, hint, slot_lists)
        if res is None:
            return None
        return res.intent_name, res.lang, {k: v.get("value") for k, v in res.slots.items()}

    return fn


@pytest.fixture(scope="module")
def example_data() -> dict:
    return yaml.safe_load((ROOT / "config_example" / "intents.yaml").read_text("utf-8"))


def test_example_corpus_passes(example_data):
    cases = corpus.parse_corpus(
        yaml.safe_load((ROOT / "tests" / "corpus_example.yaml").read_text("utf-8"))
    )
    fixture = yaml.safe_load((ROOT / "tests" / "fixture_example.yaml").read_text("utf-8"))
    report = corpus.run_corpus(_matcher(example_data, fixture), cases)
    assert report["failed"] == [], report["failed"]
    assert report["total"] >= 25


def test_example_builds_all_languages(example_data):
    intents = corpus.build_intents(example_data)
    assert set(intents) == {"de", "en", "fr"}


def test_validate_flags_unknown_list_and_rule():
    data = {
        "lists": {"room": {"values": ["kitchen"]}},
        "intents": {
            "A": {"sentences": {"en": ["turn on {room}", "status of {area}", "<verb> it"]}},
            "B": {"sentences": {}},
        },
    }
    problems = corpus.validate(data, builtins=False)
    assert any("unknown list {area}" in p for p in problems)
    assert any("unknown rule <verb>" in p for p in problems)
    assert any(p.startswith("B: no sentences") for p in problems)
    assert not any("{room}" in p for p in problems)
    # lists supplied at runtime are not reported
    assert not any("{area}" in p for p in corpus.validate(data, extra_lists={"area"}, builtins=False))
    # built-in runtime lists (area, name) are known by default
    assert not any("{area}" in p for p in corpus.validate(data))


def test_list_slot_alias_and_values():
    data = {
        "lists": {"zone": {"values": [{"in": "couch", "out": "light.couch"}]}},
        "intents": {"ZoneOn": {"sentences": {"en": ["turn on [the] {zone:target}"]}}},
    }
    assert corpus.validate(data) == []
    fn = _matcher(data)
    assert fn("turn on the couch") == ("ZoneOn", "en", {"target": "light.couch"})


def test_expansion_rules_are_passed_to_hassil():
    data = {
        "expansion_rules": {"verb": "(switch|turn) on"},
        "intents": {"On": {"sentences": {"en": ["<verb> the light"]}}},
    }
    assert corpus.validate(data) == []
    assert _matcher(data)("switch on the light")[0] == "On"


def test_run_corpus_reports_wrong_slot_and_negative():
    data = {
        "lists": {"zone": {"values": [{"in": "couch", "out": "light.couch"}]}},
        "intents": {"ZoneOn": {"sentences": {"en": ["turn on {zone}"]}}},
    }
    cases = corpus.parse_corpus(
        {
            "cases": [
                {"text": "turn on couch", "intent": "ZoneOn", "slots": {"zone": "light.bar"}},
                {"text": "turn on couch", "intent": None},
                {"text": "hello", "intent": None},
            ]
        }
    )
    report = corpus.run_corpus(_matcher(data), cases)
    assert report["passed"] == 1
    assert len(report["failed"]) == 2


def test_count_summary(example_data):
    s = corpus.count_summary(example_data)
    assert s["intents"] == len(example_data["intents"])
    assert set(s["sentences"]) == {"de", "en", "fr"}


def test_parse_corpus_is_strict():
    with pytest.raises(ValueError):
        corpus.parse_corpus({"case": []})
    with pytest.raises(ValueError):
        corpus.parse_corpus({"cases": []})
    with pytest.raises(ValueError, match="explicit 'intent'"):
        corpus.parse_corpus({"cases": [{"text": "hi"}]})
    with pytest.raises(ValueError, match="slots"):
        corpus.parse_corpus({"cases": [{"text": "hi", "intent": None, "slots": ["a"]}]})


def test_missing_expected_slot_fails():
    data = {"intents": {"On": {"sentences": {"en": ["light on"]}}}}
    cases = corpus.parse_corpus({"cases": [{"text": "light on", "intent": "On", "slots": {"x": None}}]})
    assert corpus.run_corpus(_matcher(data), cases)["failed"]


def test_hint_changes_language_like_runtime():
    data = {"intents": {"S": {"sentences": {"de": ["status"], "en": ["status"]}}}}
    fn = _matcher(data)
    assert fn("status", None)[1] == "de"
    assert fn("status", "en")[1] == "en"
    cases = corpus.parse_corpus({"cases": [{"text": "status", "intent": "S", "lang": "en", "hint": "en"}]})
    assert corpus.run_corpus(fn, cases)["failed"] == []


def test_non_string_sentences_do_not_break_language():
    data = {"intents": {"A": {"sentences": {"en": [True, "light on"], "de": "licht an"}}}}
    problems = corpus.validate(data)
    assert any("not a string" in p for p in problems)
    assert any("must be a list" in p for p in problems)
    assert _matcher(data)("light on")[0] == "A"


def test_invalid_expansion_rules_are_ignored_not_fatal():
    data = {
        "expansion_rules": {"bad": ["x"], "verb": "(turn|switch) on"},
        "intents": {"On": {"sentences": {"en": ["<verb> it"]}}},
    }
    assert any("<bad>" in p for p in corpus.validate(data))
    assert _matcher(data)("turn on it")[0] == "On"


DYN = {
    "lists": {
        "static_zone": {"values": [{"in": "couch", "out": "light.couch"}]},
        "lamps": {"entities": {"domain": "light"}},
        "rooms": {"areas": {}},
        "kids": {"template": "{{ ['anna'] }}"},
    },
    "intents": {
        "AreaStatus": {"sentences": {"en": ["which lights are on in [the] {area}"],
                                     "fr": ["quelles lumières sont allumées dans [la|le] {area}"]}},
        "LampOn": {"sentences": {"en": ["turn on [the] {lamps}"]}},
        "ZoneOn": {"sentences": {"en": ["zone {static_zone} on"]}},
    },
}


def test_static_and_dynamic_lists_are_split():
    assert set(corpus.dynamic_lists(DYN)) == {"lamps", "rooms", "kids"}
    assert set(corpus.static_lists(DYN)) == {"static_zone"}
    assert corpus.runtime_list_names(DYN) == {"area", "name", "lamps", "rooms", "kids"}
    # dynamic specs must not reach hassil (it cannot build them)
    corpus.build_intents(DYN)
    assert corpus.validate(DYN) == []


def test_runtime_lists_match_with_fixture():
    fixture = {
        "area": [{"in": "Küche", "out": "Küche"}, {"in": "kitchen", "out": "Küche"}, {"in": "cuisine", "out": "Küche"}],
        "lamps": [{"in": "Stehlampe", "out": "light.stehlampe"}],
    }
    fn = _matcher(DYN, fixture)
    assert fn("which lights are on in the kitchen") == ("AreaStatus", "en", {"area": "Küche"})
    assert fn("quelles lumières sont allumées dans la cuisine") == ("AreaStatus", "fr", {"area": "Küche"})
    assert fn("turn on the stehlampe") == ("LampOn", "en", {"lamps": "light.stehlampe"})
    assert fn("zone couch on")[0] == "ZoneOn"
    # without runtime lists the sentence does not match (and does not crash)
    assert _matcher(DYN)("which lights are on in the kitchen") is None


def test_static_list_overrides_builtin_name():
    data = {"lists": {"area": {"values": [{"in": "garden", "out": "Garten"}]}},
            "intents": {"S": {"sentences": {"en": ["status {area}"]}}}}
    assert "area" not in corpus.runtime_list_names(data)
    assert _matcher(data)("status garden") == ("S", "en", {"area": "Garten"})


def test_dynamic_list_needs_exactly_one_kind():
    data = {"lists": {"x": {"entities": {}, "areas": {}}}, "intents": {"S": {"sentences": {"en": ["{x}"]}}}}
    assert any("exactly one" in p for p in corpus.validate(data))
