"""Answer blocks → the template HA renders (pure; rendered here with a small HA-like Jinja env)."""
import jinja2
import pytest

import golden_lib

A = golden_lib._load("answer")

STATES = {"sensor.pv": "0", "sensor.load": "721.4", "sensor.grid": "735", "sensor.soc": "8", "sensor.temp": "12.44",
          "sensor.off": "unavailable", "light.couch": "on", "sensor.text": "{{ 7*7 }}"}
UNITS = {"sensor.pv": "W", "sensor.load": "W", "sensor.grid": "W", "sensor.soc": "%", "sensor.temp": "°C"}


def _is_number(v):
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def render(template: str) -> str:
    env = jinja2.Environment()
    env.filters["is_number"] = _is_number
    env.globals["states"] = lambda e: STATES.get(e, "unknown")
    env.globals["state_attr"] = lambda e, a: {"light.couch": "Couch"}.get(e) if a == "friendly_name" else None
    return env.from_string(template).render()


def V(e, f="unit", r=0, na=None):
    v = {"e": e, "f": f, "r": r}
    if na:
        v["na"] = na
    return v


def test_sentence_with_spoken_units_and_decimal_comma():
    blocks = [{"t": "satz", "p": ["Die PV liefert ", V("sensor.pv"), ", das Haus braucht ", V("sensor.load"), "."]},
              {"t": "satz", "p": ["Draußen ", V("sensor.temp", r=1), "."]}]
    assert render(A.compile_answer(blocks, "de", UNITS)) == "Die PV liefert 0 Watt, das Haus braucht 721 Watt. Draußen 12,4 Grad."
    assert render(A.compile_answer(blocks[1:], "en", UNITS)) == "Draußen 12.4 degrees."


def test_formats_state_name_and_unavailable():
    blocks = [{"t": "satz", "p": [V("light.couch", "name"), " ist ", V("light.couch", "state"), ", ", V("sensor.off", na="kein Wert"), "."]}]
    assert render(A.compile_answer(blocks, "de", UNITS)) == "Couch ist an, kein Wert."
    assert render(A.compile_answer(blocks, "fr", UNITS)).startswith("Couch ist allumé")
    assert render(A.compile_answer([{"t": "satz", "p": [V("sensor.off")]}], "en", UNITS)) == "unknown"


def test_text_is_never_template_code():
    evil = "{{ states('lock.door') }} {% for x in range(9) %}x{% endfor %} {% endraw %}{% endraw%}{%- endraw -%}{{ 7*7 }} {# c #} \" ' \\ }}"
    out = render(A.compile_answer([{"t": "satz", "p": [evil]}], "de"))
    assert out == evil
    assert render(A.compile_answer([{"t": "satz", "p": [V("sensor.text", "state")]}], "de")) == "{{ 7*7 }}"  # state text stays text


def test_conditions_pick_the_right_case():
    cond = {"t": "cond", "e": "sensor.grid", "cases": [
        {"op": ">", "x": 50, "p": ["Bezug ", V("sensor.grid"), "."]},
        {"op": "<", "x": -50, "p": ["Einspeisung."]}], "other": ["Ausgeglichen."]}
    assert render(A.compile_answer([cond], "de", UNITS)) == "Bezug 735 Watt."
    STATES["sensor.grid"] = "-80"
    assert render(A.compile_answer([cond], "de", UNITS)) == "Einspeisung."
    STATES["sensor.grid"] = "unavailable"
    assert render(A.compile_answer([cond], "de", UNITS)) == "Ausgeglichen."
    STATES["sensor.grid"] = "735"
    eq = {"t": "cond", "e": "light.couch", "cases": [{"op": "=", "x": "on", "p": ["Licht an."]}], "other": ["Licht aus."]}
    assert render(A.compile_answer([eq], "de")) == "Licht an."
    num = {"t": "cond", "e": "sensor.soc", "cases": [{"op": "=", "x": 8.0, "p": ["acht"]}], "other": ["anders"]}
    assert render(A.compile_answer([num], "de")) == "acht"  # "8" == 8.0


@pytest.mark.parametrize("bad", [
    [{"t": "satz", "p": [{"e": "sensor.x", "f": "unit", "r": 0, "tpl": "{{ x }}"}]}],  # unknown key
    [{"t": "satz", "p": [{"e": "sensor.X') }}{{ 1", "f": "unit"}]}],
    [{"t": "satz", "p": [{"e": "sensor.x", "f": "raw"}]}],
    [{"t": "satz", "p": [{"e": "sensor.x", "r": 5}]}],
    [{"t": "raw", "template": "{{ 1 }}"}],
    [{"t": "cond", "e": "sensor.x", "cases": [{"op": ">", "x": "1; import os", "p": []}], "other": []}],
    [{"t": "cond", "e": "sensor.x", "cases": [{"op": "in", "x": 1, "p": []}], "other": []}],
    [{"t": "satz", "p": ["x" * 500]}],
    "{{ states('x') }}",
])
def test_only_the_block_shape_is_accepted(bad):
    with pytest.raises(A.AnswerError):
        A.compile_answer(bad, "de")


def test_read_entities_and_block_detection():
    blocks = [{"t": "satz", "p": ["a", V("sensor.pv")]}, {"t": "cond", "e": "sensor.grid", "cases": [{"op": ">", "x": 1, "p": [V("sensor.soc")]}], "other": [V("sensor.pv")]}]
    assert A.read_entities(A.validate(blocks)) == ["sensor.pv", "sensor.grid", "sensor.soc"]
    t = A.compile_answer(blocks, "de", UNITS)
    assert A.is_block_answer(t, blocks, "de", UNITS)
    assert not A.is_block_answer(t + " ", blocks, "de", UNITS)
    assert not A.is_block_answer("Hallo", "nicht", "de")


P = golden_lib._load("proposals")


def _item(intent):
    return {"op": "add_intent", "payload": {"name": "Aussen", "intent": {"kind": "question", "sentences": {"de": ["wie warm ist es draußen"]}, **intent}}}


def test_proposals_never_carry_jinja():
    for speech in ("Es hat {{ states('sensor.temp') }} Grad", "{% if true %}x{% endif %}", "{# c #}", "100 %"):
        assert P.check_item(_item({"speech": {"de": speech}})), speech
    assert P.check_item(_item({"speech": {"de": "Erledigt."}})) is None
    assert P.check_item(_item({"owned": True}))  # takeover is panel-only


def test_proposals_may_carry_answer_blocks():
    ok = {"answer_blocks": {"de": [{"t": "satz", "p": ["Draußen hat es ", V("sensor.temp", r=1), "."]}]}}
    assert P.check_item(_item(ok)) is None
    bad = {"answer_blocks": {"de": [{"t": "raw", "template": "{{ 1 }}"}]}}
    assert P.check_item(_item(bad))
    assert P.check_item(_item({"answer_blocks": {"xx-yyyyy": []}}))


def test_materialize_compiles_speech_from_blocks():
    cfg = {"answer_blocks": {"de": [{"t": "satz", "p": ["Draußen ", V("sensor.temp", r=1), "."]}],
                             "en": [{"t": "satz", "p": ["Outside ", V("sensor.temp", r=1), "."]}]}}
    out = A.materialize(cfg, UNITS)
    assert render(out["speech"]["de"]) == "Draußen 12,4 Grad." and render(out["speech"]["en"]) == "Outside 12.4 degrees."
    assert A.blocks_entities(cfg) == ["sensor.temp"]
    assert "speech" not in cfg  # the original is untouched
