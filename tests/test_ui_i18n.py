"""Panel texts: every language file has the same nested keys and placeholders."""
import json
import re
from pathlib import Path

FRONTEND = Path(__file__).parents[1] / "custom_components/polyglot_assist/frontend"
I18N = FRONTEND / "i18n"
LANGS = ("en", "de", "fr")
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _load(lang):
    return json.loads((I18N / f"{lang}.json").read_text(encoding="utf-8"))


def _shape(obj):
    """Nested key structure without the texts."""
    return {k: _shape(v) if isinstance(v, dict) else None for k, v in obj.items()}


def _flat(obj, prefix=""):
    out = {}
    for k, v in obj.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flat(v, key))
        else:
            out[key] = v
    return out


def test_files_exist():
    for lang in LANGS:
        assert (I18N / f"{lang}.json").is_file(), lang


def test_same_nested_structure():
    ref = _shape(_load("en"))
    for lang in LANGS:
        assert _shape(_load(lang)) == ref, lang


def test_values_are_non_empty_strings_without_markup():
    for lang in LANGS:
        for key, text in _flat(_load(lang)).items():
            assert isinstance(text, str), (lang, key)
            assert text.strip(), (lang, key)
            assert "<" not in text and ">" not in text, (lang, key)


def test_same_placeholders_per_key():
    flat = {lang: _flat(_load(lang)) for lang in LANGS}
    for key, text in flat["en"].items():
        want = set(PLACEHOLDER.findall(text))
        for lang in LANGS:
            assert set(PLACEHOLDER.findall(flat[lang][key])) == want, (lang, key)


def test_every_literal_key_in_the_panel_exists():
    js = (FRONTEND / "polyglot-panel.js").read_text(encoding="utf-8")
    known = _flat(_load("en"))
    groups = {k.split(".")[0] for k in known}
    used = {m for m in re.findall(r'"([a-z_]+\.[a-z_]+)"', js) if m.split(".")[0] in groups}
    assert used, "no keys found"
    assert not used - set(known), sorted(used - set(known))
