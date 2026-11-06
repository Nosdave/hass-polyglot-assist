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


def test_referenced_lists_and_spec_validation():
    data = {"expansion_rules": {"r": "{zone} x"},
            "lists": {"zone": {"values": ["a"]}, "lamps": {"entities": {"domain": "light", "exclude": {"a": "b"}}},
                      "t": {"template": 5}, "both": {"areas": {}, "values": ["x"]}},
            "intents": {"S": {"sentences": {"en": ["{lamps:target} <r>", "{area}"]}}}}
    assert corpus.referenced_lists(data) == {"lamps", "area", "zone"}
    problems = corpus.validate(data)
    assert any("entities.exclude must be" in p for p in problems)
    assert any("list t: template must be a string" in p for p in problems)
    assert any("list both: use exactly one" in p for p in problems)


def test_safety_disabled_takes_no_part_in_matching():
    data = {"intents": {
        "Door": {"safety": "disabled", "sentences": {"en": ["open the door"]}},
        "Lamp": {"safety": "sensitive", "sentences": {"en": ["lamp on"]}},
        "Bad": {"safety": "maybe", "sentences": {"en": ["x"]}},
    }}
    fn = _matcher(data)
    assert fn("open the door") is None
    assert fn("lamp on")[0] == "Lamp"
    s = corpus.count_summary(data)
    assert s["intents"] == 3 and s["active"] == 1  # unknown level "maybe" fails closed
    assert s["disabled"] == ["Bad", "Door"] and s["sensitive"] == ["Lamp"]
    assert any("unknown safety level" in p for p in corpus.validate(data))


def test_unparsable_sentence_is_reported():
    data = {"intents": {"A": {"sentences": {"de": ["mach [was", "licht an"]}}},
            "expansion_rules": {"r": "(a|b"}}
    problems = corpus.validate(data)
    assert any("cannot parse" in p and "mach [was" in p for p in problems)
    assert any("expansion rule <r>: cannot parse" in p for p in problems)


def test_safety_fails_closed_on_typos():
    data = {"intents": {"Door": {"safety": "Disabled", "sentences": {"en": ["open the door"]}}}}
    assert _matcher(data)("open the door") is None
    assert corpus.count_summary(data)["disabled"] == ["Door"]


def test_run_corpus_reports_case_index():
    data = {"intents": {"A": {"sentences": {"en": ["a"]}}}}
    cases = corpus.parse_corpus({"cases": [{"text": "a", "intent": "A"}, {"text": "a", "intent": None}]})
    assert [f["index"] for f in corpus.run_corpus(_matcher(data), cases)["failed"]] == [1]


def test_check_proposal_statuses():
    data = {
        "lists": {"zone": {"values": [{"in": "couch", "out": "light.couch"}, {"in": "bar", "out": "light.bar"}]}},
        "intents": {
            "ZoneOn": {"sentences": {"de": ["{zone} an"], "en": ["turn on [the] {zone}"]}},
            "AllOff": {"sentences": {"en": ["all lights off"]}},
        },
    }
    m = matcher_mod.MultilangMatcher(corpus.build_intents(data))
    lists = corpus.build_intents(data)["en"].slot_lists

    def fn(text):
        r = m.match(text, None)
        return r and (r.intent_name, r.lang, {})

    src = data["intents"]["ZoneOn"]["sentences"]["de"] + data["intents"]["ZoneOn"]["sentences"]["en"]
    ok = corpus.check_proposal("ZoneOn", "allume [le|la] {zone}", src, fn, lists, language="fr")
    assert ok["status"] == "ok", ok
    assert corpus.check_proposal("ZoneOn", "allume (le|la", src, fn, lists)["status"] == "error"
    assert "slots" in corpus.check_proposal("ZoneOn", "allume tout", src, fn, lists)["notes"][0]
    coll = corpus.check_proposal("ZoneOn", "all lights off", [], fn, lists)
    assert coll["status"] == "error" and coll["collisions"][0]["intent"] == "AllOff"
    assert corpus.check_proposal("ZoneOn", "turn on {zone}", src, fn, lists)["status"] == "warn"
    assert corpus.capture_lists("x {a} {b:c}") == frozenset({"a", "c"})


def test_names_table_and_validation():
    data = {"names": {"light.couch": {"en": ["sofa", "couch"], "fr": "canapé"},
                      "area.wohnzimmer": {"fr": ["salon"]},
                      "light.bar": {"en": ["Sofa"]},
                      "bad": ["x"]},
            "intents": {"S": {"sentences": {"en": ["x"]}}}}
    t = corpus.names_table(data)
    assert t["light.couch"] == ["sofa", "couch", "canapé"]
    problems = corpus.validate(data)
    assert any("'Sofa' is used for light.couch and light.bar" in p for p in problems)
    assert any(p.startswith("names.bad") for p in problems)
