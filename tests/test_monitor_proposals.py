"""Monitoring proposals: evidence per proposal, follow-ups, translatable check notes.

monitor.py imports Home Assistant only for its storage/timer part; the few names it
needs are stubbed here so the pure evaluation runs without HA. Invented example data.
"""
import importlib
import importlib.util
import sys
import types
from datetime import datetime, timezone

import golden_lib


def _load_monitor():
    if "_pgpkg.monitor" in sys.modules:
        return sys.modules["_pgpkg.monitor"]
    if importlib.util.find_spec("homeassistant") is None:
        _stub_homeassistant()
    pkg = types.ModuleType("_pgpkg")
    pkg.__path__ = [str(golden_lib.PKG)]
    sys.modules["_pgpkg"] = pkg
    return importlib.import_module("_pgpkg.monitor")


def _stub_homeassistant():
    stubs = {
        "homeassistant": {},
        "homeassistant.core": {"CALLBACK_TYPE": object, "HomeAssistant": object, "callback": lambda f: f},
        "homeassistant.helpers": {},
        "homeassistant.helpers.event": {"async_track_time_change": lambda *a, **k: None},
        "homeassistant.helpers.storage": {"Store": object},
        "homeassistant.util": {},
    }
    for name, attrs in stubs.items():
        if name not in sys.modules:
            mod = types.ModuleType(name)
            mod.__path__ = []
            mod.__dict__.update(attrs)
            sys.modules[name] = mod
    if "homeassistant.util.dt" not in sys.modules:
        dt = types.ModuleType("homeassistant.util.dt")
        dt.parse_datetime = lambda s: datetime.fromisoformat(s)
        dt.utcnow = lambda: datetime.now(timezone.utc)
        sys.modules["homeassistant.util.dt"] = dt
        sys.modules["homeassistant.util"].dt = dt


M = _load_monitor()
corpus = golden_lib._load("corpus")
matcher_mod = golden_lib._load("matcher")
P = golden_lib._load("proposals")

CONFIG = {"intents": {
    "Uhrzeit": {"sentences": {"de": ["wie spät ist es"]}},
    "LichtAn": {"sentences": {"de": ["mach das licht an"]}},
}}
TIME_TOOL = [{"tool": "GetDateTime", "args": "{}"}]
TEMP_TOOL = [{"tool": "GetLiveContext", "args": "{'area': 'Küche'}", "target": {"area": "Küche"}}]


def _e(text, lang="de", tools=TIME_TOOL, ts="2026-01-01T10:00:00+00:00"):
    return {"ts": ts, "text": text, "lang": lang, "result": "assistant", "targets": tools, "conversation": "c1"}


def _items(repeated, lines, builtin_match=None):
    matcher = matcher_mod.MultilangMatcher(corpus.build_intents(CONFIG))
    sl = matcher.static_slot_lists()
    return M.build_monitor_items(repeated, lines, CONFIG, lambda t, lang: matcher.match_all(t, lang, sl), sl,
                                 builtin_match, lambda e: [])


def test_follow_ups_are_not_repeated_sentences():
    assert M.is_follow_up("Und in der Küche?")
    assert M.is_follow_up("and in the bedroom?")
    assert M.is_follow_up("Et dans le salon ?")
    assert not M.is_follow_up("Wie viel Uhr ist es?")
    assert not M.is_follow_up("Und jetzt mach bitte im ganzen Haus alle Lichter aus")  # long: stands on its own
    entries = [_e("Und in der Küche?", tools=TEMP_TOOL)] * 3 + [_e("Wie viel Uhr ist es?")] * 2
    texts = [r["text"] for r in M.Monitor.find_repeated(entries)]
    assert texts == ["Wie viel Uhr ist es?"]


def test_origin_lists_only_the_sentences_this_template_recognises():
    repeated = [
        {"text": "Und in der Küche?", "count": 3, "lang": "de", "targets": TEMP_TOOL},  # e.g. from an older log
        {"text": "Wie viel Uhr ist es?", "count": 2, "lang": "de", "targets": TIME_TOOL},
        {"text": "Quelle heure est-il ?", "count": 3, "lang": "fr", "targets": TIME_TOOL},
        {"text": "What time is it?", "count": 2, "lang": "en", "targets": TIME_TOOL},
    ]
    [item] = _items(repeated, ["Uhrzeit | de | wie viel uhr ist es"])
    assert [e["text"] for e in item["origin"]["evidence"]] == ["Wie viel Uhr ist es?"]
    detail = item["origin"]["detail"]
    assert "Wie viel Uhr ist es?" in detail
    for other in ("Küche", "Quelle heure", "What time"):
        assert other not in detail, detail
    assert item["check"]["status"] != "error", item["check"]


def test_other_languages_are_no_evidence():
    # the German template would not be bound to a sentence that was only said in French
    repeated = [{"text": "wie viel uhr ist es", "count": 2, "lang": "fr", "targets": TIME_TOOL}]
    [item] = _items(repeated, ["Uhrzeit | de | wie viel uhr ist es"])
    assert item["check"]["status"] == "error"
    assert item["origin"]["evidence"] == []


def test_check_notes_carry_a_translation_key_and_keep_the_english_text():
    repeated = [{"text": "Wie viel Uhr ist es?", "count": 2, "lang": "de", "targets": TIME_TOOL}]
    [miss] = _items(repeated, ["Uhrzeit | de | sag mir die zeit"])
    note = "does not match the sentences that were actually said"
    assert miss["check"]["status"] == "error" and note in miss["check"]["notes"]
    assert miss["check"]["i18n"][note] == {"key": "no_said_match"}

    [taken] = _items(repeated, ["Uhrzeit | de | wie viel uhr ist es"], builtin_match=lambda t, lang: "HassGetCurrentTime/de")
    note = next(n for n in taken["check"]["notes"] if n.startswith("would take over"))
    assert taken["check"]["i18n"][note]["key"] == "takes_over_builtin"
    assert taken["check"]["i18n"][note]["params"]["hit"] == "HassGetCurrentTime/de"
    assert set(taken["check"]["i18n"]) <= set(taken["check"]["notes"])  # only keys of notes it shows


def test_every_check_key_has_a_panel_text():
    import json  # noqa: PLC0415
    import re  # noqa: PLC0415

    keys = set()
    for name in ("proposals.py", "monitor.py"):
        src = (golden_lib.PKG / name).read_text("utf-8")
        keys |= set(re.findall(r'(?:_coded|fail|warn)\("([a-z_]+)"', src))
    assert {"no_said_match", "takes_over_builtin", "collides"} <= keys
    for lang in ("de", "en", "fr"):
        texts = json.loads((golden_lib.PKG / "frontend/i18n" / f"{lang}.json").read_text("utf-8"))["checks"]
        assert not keys - set(texts), (lang, sorted(keys - set(texts)))


def test_check_proposal_collision_is_coded():
    matcher = matcher_mod.MultilangMatcher(corpus.build_intents(CONFIG))
    sl = matcher.static_slot_lists()
    r = P.check_proposal("Uhrzeit", "mach das licht an", ["wie spät ist es"],
                         lambda t: matcher.match_all(t, "de", sl), sl, {}, "de")
    assert r["status"] == "error"
    assert P.check_for_queue(r)["i18n"]["collides with other commands"] == {"key": "collides"}
