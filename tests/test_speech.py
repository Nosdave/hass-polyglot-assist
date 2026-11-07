import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "pg_speech", Path(__file__).parents[1] / "custom_components/polyglot_assist/speech.py")
speech = importlib.util.module_from_spec(spec)
spec.loader.exec_module(speech)


def test_every_answer_has_english_and_the_core_languages():
    for key, texts in speech.SPEECH.items():
        for lang in ("en", "de", "fr"):
            assert texts.get(lang), (key, lang)


def test_placeholders_match_across_languages():
    import string
    for key, texts in speech.SPEECH.items():
        fields = {frozenset(f for _, f, _, _ in string.Formatter().parse(t) if f) for t in texts.values()}
        assert len(fields) == 1, key


def test_say_uses_base_language_and_falls_back_to_english():
    assert speech.say("done", "de-AT") == "Erledigt."
    assert speech.say("done", "fr") == "C'est fait."
    assert speech.say("done", "tlh") == "Done."
    assert speech.say("done", None) == "Done."
    assert "Licht" in speech.say("handler_failed", "de", name="Licht")
