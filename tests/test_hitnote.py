import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).parents[1] / "custom_components/polyglot_assist"
spec = importlib.util.spec_from_file_location("pg_hitnote", ROOT / "hitnote.py")
hitnote = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hitnote)


def test_marker_matches_const():
    const = (ROOT / "const.py").read_text()
    assert re.search(r'CHIME_MARKER: Final = "(.*)"', const)[1] == hitnote.CHIME_MARKER


def test_successful_command_is_no_response():
    # the confirmation text teaches the assistant to confirm without a tool
    assert hitnote.history_text("Couch ist aus.", True) == "(no response)"
    assert hitnote.history_text(hitnote.CHIME_MARKER, True) == "(no response)"


def test_question_answer_is_no_response():
    # "22,3 Grad" in the history → the follow-up "Und in der Küche?" gets an invented number
    assert hitnote.history_text("22,3 Grad", True) == "(no response)"


def test_error_keeps_its_text():
    assert hitnote.history_text("Das hat leider nicht geklappt.", False) == "Das hat leider nicht geklappt."


def test_marker_never_reaches_the_history():
    assert hitnote.history_text(hitnote.CHIME_MARKER, False) == "(no response)"
    assert hitnote.history_text("", False) == "(no response)"
    assert hitnote.history_text(None, False) == "(no response)"


def test_strip_marker_from_fallback_answer():
    m = hitnote.CHIME_MARKER
    assert hitnote.strip_marker(m) is None
    assert hitnote.strip_marker(f" {m} ") is None
    assert hitnote.strip_marker(f"Okay. {m}") == "Okay."
    assert hitnote.strip_marker("Couch ist aus.") == "Couch ist aus."
    assert hitnote.strip_marker("") == ""
    assert hitnote.strip_marker(None) is None
