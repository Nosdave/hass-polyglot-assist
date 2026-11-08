"""Learning from AI answers (pure part). Invented example data — no household data in this repo."""
import golden_lib

L = golden_lib._load("learn_ai")
A = golden_lib._load("answer")

READ = [{"tool": "homeassistant__GetLiveContext", "args": "{'name': 'Outdoor'}"}]


def _e(text, said, read, conv="c1", tools=READ, lang="en"):
    return {"text": text, "result": "assistant", "targets": tools, "speech": said, "read": read, "lang": lang,
            "conversation": conv, "ms": 1800}


TEMP = [{"e": "sensor.outdoor", "s": "12.44"}]


def test_asked_twice_with_the_value_in_the_answer_becomes_a_candidate():
    entries = [_e("How warm is it outside?", "It is 12.4 degrees outside.", TEMP),
               _e("how warm is it outside", "Right now it's 12 degrees outside.", TEMP, conv="c2")]
    out = L.find_ai_answers(entries)
    assert len(out) == 1 and out[0]["entity"] == "sensor.outdoor" and out[0]["count"] == 2


def test_once_or_switching_or_unfiltered_is_not_learned():
    assert L.find_ai_answers([_e("How warm is it outside?", "12.4 degrees.", TEMP)]) == []
    switching = [{"tool": "HassTurnOn", "args": "{}"}]
    assert L.find_ai_answers([_e("Light on", "Done.", TEMP, tools=switching)] * 2) == []
    assert L.find_ai_answers([_e("How warm?", "12.4 degrees.", [])] * 2) == []  # nothing known was read


def test_ambiguous_entities_are_not_guessed():
    two = [{"e": "sensor.a", "s": "12.4"}, {"e": "sensor.b", "s": "12.4"}]
    assert L.find_ai_answers([_e("How warm is it?", "12.4 degrees.", two)] * 2) == []
    distinct = [{"e": "sensor.a", "s": "12.4"}, {"e": "sensor.b", "s": "7"}]
    out = L.find_ai_answers([_e("How warm is it?", "It is 12,4 degrees.", distinct)] * 2)
    assert out and out[0]["entity"] == "sensor.a"  # the value in the answer decides (decimal comma too)


def test_answer_blocks_from_the_ai_wording():
    blocks = L.answer_blocks_from("Draußen sind es gerade 12,4 Grad.", "sensor.outdoor", "12.44", ["Grad", "°C"])
    assert blocks == [{"t": "satz", "p": ["Draußen sind es gerade ", {"e": "sensor.outdoor", "f": "unit", "r": 1}, "."]}]
    assert A.validate(blocks)
    plain = L.answer_blocks_from("The battery is at 8 percent", "sensor.soc", "8", ["percent", "%"])
    assert plain[0]["p"][1] == {"e": "sensor.soc", "f": "unit", "r": 0}
    nounit = L.answer_blocks_from("Level 8 now", "sensor.x", "8", ["percent"])
    assert nounit[0]["p"][1]["f"] == "value"
    assert L.answer_blocks_from("No number here", "sensor.x", "8", []) is None


def test_names_and_sentences():
    assert L.command_name("Außentemperatur") == "AussentemperaturFrage"
    assert L.command_name("3D printer") == "Q3DPrinterFrage"
    assert L.sentence_from("Wie warm ist es draußen?") == "wie warm ist es draußen"
    assert "{" not in L.sentence_from("what is {x} [y] (a|b)")
