"""Learning NEW room commands from repeated AI actions (no HA needed).

All sentences, rooms and devices here are invented examples.
"""
import json
from datetime import datetime, timezone

import golden_lib

A = golden_lib._load("learn_actions")
P = golden_lib._load("proposals")

NOW = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)


def _run(text, ts, args, tool="assist__intent__HassTurnOn", conv="c1", lang="de", **kw):
    return {"ts": ts, "text": text, "lang": lang, "result": "assistant", "conversation": conv,
            "targets": [{"tool": tool, "args": str(args), "target": {k: v for k, v in args.items()
                                                                    if k in ("name", "domain", "device_class", "area")}}],
            **kw}


OPEN_BED = _run("Rollladen im Schlafzimmer auf.", "2026-03-09T07:38:16+00:00",
                {"area": "Schlafzimmer", "device_class": ["shutter"]})
OPEN_LIVING = _run("Mach die Rollläden im Wohnzimmer hoch", "2026-03-10T08:01:00+00:00",
                   {"area": "Wohnzimmer", "domain": ["cover"]}, conv="c2")


def test_different_words_same_action_are_one_group():
    groups = A.find_repeated_actions([OPEN_BED, OPEN_LIVING], now=NOW)
    assert len(groups) == 1
    g = groups[0]
    assert g["pattern"] == "cover_area_open" and g["count"] == 2
    assert g["rooms"] == ["Schlafzimmer", "Wohnzimmer"]
    assert [r["text"] for r in g["runs"]] == [OPEN_BED["text"], OPEN_LIVING["text"]]


def test_open_and_close_are_different_commands():
    close = _run("Rollläden Küche zu", "2026-03-10T09:00:00+00:00", {"area": "Küche", "device_class": ["shutter"]},
                 tool="assist__intent__HassTurnOff", conv="c3")
    keys = {g["key"] for g in A.find_repeated_actions([OPEN_BED, OPEN_LIVING, close, {**close, "conversation": "c4"}], now=NOW)}
    assert keys == {"cover_area_open", "cover_area_close"}


def test_threshold_two_runs_on_two_days_or_in_two_conversations():
    assert A.find_repeated_actions([OPEN_BED], now=NOW) == []  # once is not a habit
    same_evening = [OPEN_BED, {**OPEN_BED, "ts": "2026-03-09T07:40:00+00:00", "text": "Rollladen Bad auf"}]
    assert A.find_repeated_actions(same_evening, now=NOW) == []  # one day, one conversation
    other_conv = [OPEN_BED, {**OPEN_BED, "ts": "2026-03-09T07:40:00+00:00", "conversation": "c9"}]
    assert len(A.find_repeated_actions(other_conv, now=NOW)) == 1
    other_day = [OPEN_BED, {**OPEN_BED, "ts": "2026-03-10T07:00:00+00:00"}]
    assert len(A.find_repeated_actions(other_day, now=NOW)) == 1


def test_only_the_last_seven_days_count():
    old = {**OPEN_BED, "ts": "2026-03-01T07:00:00+00:00", "conversation": "c0"}
    assert A.find_repeated_actions([old, OPEN_BED], now=NOW) == []


def test_security_relevant_actions_never_form_a_group():
    runs = []
    for i, (args, tool) in enumerate([
        ({"area": "Diele", "domain": ["lock"]}, "assist__intent__HassTurnOn"),
        ({"area": "Garage", "device_class": ["garage"]}, "assist__intent__HassTurnOn"),
        ({"area": "Einfahrt", "device_class": ["gate"]}, "assist__intent__HassTurnOff"),
        ({"area": "Flur", "domain": ["alarm_control_panel"]}, "assist__intent__HassTurnOn"),
    ]):
        runs += [_run(f"x{i}", "2026-03-09T07:00:00+00:00", args, tool=tool, conv=f"a{i}"),
                 _run(f"y{i}", "2026-03-10T07:00:00+00:00", args, tool=tool, conv=f"b{i}")]
    assert A.find_repeated_actions(runs, now=NOW) == []


def test_named_devices_mixed_calls_and_errors_are_not_room_commands():
    named = _run("Couch an", "2026-03-09T07:00:00+00:00", {"name": "Couch", "area": "Wohnzimmer", "domain": ["light"]})
    failed = {**OPEN_BED, "error": True}
    mixed = {**OPEN_LIVING, "targets": OPEN_LIVING["targets"] + [
        {"tool": "assist__intent__HassTurnOn", "args": "{'area': 'Bad', 'domain': ['light']}", "target": {"area": "Bad", "domain": ["light"]}}]}
    for run in (named, failed, mixed):
        assert A.run_pattern(run) is None


def test_script_with_a_room_and_fixed_numbers():
    runs = [_run("Volle Helligkeit Küche", "2026-03-09T20:00:00+00:00", {"ziel": "Küche", "helligkeit": 100},
                 tool="assist__script__licht_nur_an_aendern"),
            _run("Stell das Wohnzimmer ganz hell", "2026-03-10T20:00:00+00:00", {"ziel": "Wohnzimmer", "helligkeit": 100},
                 tool="assist__script__licht_nur_an_aendern", conv="c2"),
            _run("Wohnzimmer 30 Prozent", "2026-03-10T21:00:00+00:00", {"ziel": "Wohnzimmer", "helligkeit": 30},
                 tool="assist__script__licht_nur_an_aendern", conv="c3")]
    groups = A.find_repeated_actions(runs, now=NOW)
    assert len(groups) == 1 and groups[0]["count"] == 2  # 100 % twice; 30 % is another command
    d = groups[0]["details"]
    assert (d["script"], d["room_arg"], d["constants"]) == ("licht_nur_an_aendern", "ziel", {"helligkeit": 100})


def test_an_existing_command_covering_what_was_said_means_nothing_is_missing():
    group = A.find_repeated_actions([OPEN_BED, OPEN_LIVING], now=NOW)[0]
    known = {"rollladen im schlafzimmer auf.": {"RollladenRaum"}}
    assert A.covered(group, lambda text, lang: known.get(text.lower(), set())) == "RollladenRaum"
    assert A.covered(group, lambda text, lang: set()) is None


def test_reply_parsing_keeps_only_the_chosen_room_list():
    reply = "```json\n" + json.dumps({
        "name": "RolllaedenRaumAuf", "list": "raum", "description": "Rollläden eines Raums öffnen",
        "sentences": {"de": ["(rollladen|rollläden) [im|in der] {raum} (auf|hoch)", "mach {raum} {licht}", "{name} auf"],
                      "en": ["open the shutters in [the] {raum}"], "xx": ["{raum} ouvrir"]},
        "speech": {"de": {"ok": "Rollläden fahren hoch", "none": "In ROOM gibt es keine Rollläden."},
                   "en": "Opening {raum}"},
    }) + "\n```"
    out = A.parse_reply(reply, languages=["de", "en", "fr"], lists=["raum"])
    assert out["name"] == "RolllaedenRaumAuf" and out["list"] == "raum"
    assert out["sentences"] == {"de": ["(rollladen|rollläden) [im|in der] {raum} (auf|hoch)"],
                                "en": ["open the shutters in [the] {raum}"]}
    # plain texts only; a placeholder in the answer is dropped (Polyglot builds the template)
    assert out["speech"] == {"de": {"ok": "Rollläden fahren hoch", "none": "In ROOM gibt es keine Rollläden."}}
    assert A.parse_reply('{"none": "already covered"}', languages=["de"], lists=["raum"]).startswith("ai_none")
    assert A.parse_reply('{"name": "X1x", "list": "other", "sentences": {}}', languages=["de"], lists=["raum"]) == "unknown list"


# ── the action is built by code and checked by rebuilding it ────────────────

COVER_PATTERN = {"id": "cover_area_open", "slot": "raum",
                 "map": {"Schlafzimmer": ["cover.sz_ost", "cover.sz_sued"], "Wohnzimmer": ["cover.wz"]}}


def _item(pattern, action=None, speech=None):
    return {"op": "add_intent", "pattern": pattern,
            "payload": {"name": "RolllaedenRaumAuf", "intent": {
                "kind": "command", "sentences": {"de": ["rollläden {raum} auf"]},
                "action": P.pattern_action(pattern) if action is None else action,
                "speech": P.pattern_speech(pattern, ["de"]) if speech is None else speech}}}


def test_pattern_action_shape_and_check():
    action = P.pattern_action(COVER_PATTERN)
    assert action[0] == {"variables": {"polyglot_targets": {"Schlafzimmer": ["cover.sz_ost", "cover.sz_sued"],
                                                            "Wohnzimmer": ["cover.wz"]}}}
    assert action[1]["condition"] == "template" and "raum in polyglot_targets" in action[1]["value_template"]
    assert action[2]["action"] == "cover.open_cover"
    assert P.check_item(_item(COVER_PATTERN)) is None
    assert sorted(P.pattern_entities(COVER_PATTERN)) == ["cover.sz_ost", "cover.sz_sued", "cover.wz"]


def test_a_changed_action_or_unsafe_devices_are_refused():
    tampered = P.pattern_action(COVER_PATTERN)
    tampered[2] = {"action": "lock.unlock", "target": {"entity_id": "{{ polyglot_targets[raum] }}"}}
    assert "differs" in P.check_item(_item(COVER_PATTERN, tampered))
    garage = {**COVER_PATTERN, "map": {"Garage": ["cover.garagentor"]}}
    assert "security-relevant" in P.check_item(_item(garage))
    car = {**COVER_PATTERN, "map": {"Garage": ["cover.tesla_kofferraum"]}}
    assert "security-relevant" in P.check_item(_item(car))
    wrong_domain = {**COVER_PATTERN, "map": {"Bad": ["light.bad"]}}
    assert "domain" in P.check_item(_item(wrong_domain))
    assert P.check_item(_item({**COVER_PATTERN, "id": "lock_area"}, action=[])) == "unknown pattern"
    bad_slot = {**COVER_PATTERN, "slot": "raum }}{{ states"}
    assert P.check_item(_item(bad_slot, action=[])) == "malformed pattern"


def test_script_pattern_passes_only_the_room_and_numbers():
    pattern = {"id": "script_room", "slot": "licht_raum", "script": "licht_nur_an_aendern",
               "room_arg": "ziel", "constants": {"helligkeit": 100}}
    assert P.pattern_action(pattern) == [{"action": "script.licht_nur_an_aendern",
                                          "data": {"ziel": "{{ licht_raum }}", "helligkeit": 100}}]
    assert P.check_item(_item(pattern)) is None
    assert P.check_item(_item({**pattern, "constants": {"code": "1234"}})) is not None
    assert "security-relevant" in P.check_item(_item({**pattern, "script": "garagentor_auf"}))


def _say(template, **slots):
    import jinja2  # the same syntax HA renders (HA adds its own globals, not needed here)

    return jinja2.Environment().from_string(template).render(**slots)


def test_a_room_without_such_devices_gets_an_honest_answer_and_no_action():
    pattern = {**COVER_PATTERN, "answers": {"de": {"ok": "Rollläden fahren hoch", "none": "In ROOM gibt es keine Rollläden."},
                                            "en": {"ok": "Opening the shutters"}}}
    speech = P.pattern_speech(pattern, ["de", "en", "fr"])
    assert _say(speech["de"], raum="Schlafzimmer") == "Rollläden fahren hoch"
    assert _say(speech["de"], raum="Bad") == "In Bad gibt es keine Rollläden."
    assert _say(speech["en"], raum="Bad") == "There are no such devices in Bad."  # no "none" given → default
    assert _say(speech["fr"], raum="Wohnzimmer") == "C'est fait."  # nothing given → defaults
    assert _say(speech["fr"], raum="Bad") == "Il n'y a pas d'appareils de ce type dans Bad."
    # and the action does nothing for that room: the condition before the step is false
    action = P.pattern_action(pattern)
    assert _say(action[1]["value_template"], raum="Bad", polyglot_targets=action[0]["variables"]["polyglot_targets"]) == "False"
    assert P.check_item(_item(pattern, speech=P.pattern_speech(pattern, ["de"]))) is None


def test_the_answer_is_built_not_taken_from_the_proposal():
    pattern = {**COVER_PATTERN, "answers": {"de": {"ok": "Erledigt", "none": "Kein ROOM"}}}
    assert "answer differs" in P.check_item(_item(pattern, speech={"de": "{{ states('lock.door') }}"}))
    sneaky = {**COVER_PATTERN, "answers": {"de": {"ok": "x{% endif %}{{ 7*7 }}", "none": "In ROOM \" + 1"}}}
    speech = P.pattern_speech(sneaky, ["de"])["de"]
    assert "7*7" not in speech and _say(speech, raum="Bad") == "In ROOM gibt es dafür keine Geräte.".replace("ROOM", "Bad")
    assert P.check_item(_item({**COVER_PATTERN, "map": {'Bad"': ["cover.bad"]}})) == "invalid room value"


def test_script_commands_keep_a_plain_answer():
    pattern = {"id": "script_room", "slot": "licht_raum", "script": "licht_nur_an_aendern",
               "room_arg": "ziel", "constants": {"helligkeit": 100}, "answers": {"de": {"ok": "Volle Helligkeit"}}}
    assert P.pattern_speech(pattern, ["de"]) == {"de": "Volle Helligkeit"}
