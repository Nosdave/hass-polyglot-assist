"""translations/<lang>.json (options dialog, services, entities) match strings.json."""
import json
import re
from pathlib import Path

BASE = Path(__file__).parents[1] / "custom_components/polyglot_assist"
PH = re.compile(r"\{[a-z_]+\}")


def _flat(d, p=""):
    out = {}
    for k, v in d.items():
        key = f"{p}.{k}" if p else k
        out.update(_flat(v, key) if isinstance(v, dict) else {key: v})
    return out


def test_every_translation_matches_strings_json():
    ref = _flat(json.loads((BASE / "strings.json").read_text("utf-8")))
    files = sorted((BASE / "translations").glob("*.json"))
    assert {f.stem for f in files} >= {"en", "de", "fr"}
    for f in files:
        tr = _flat(json.loads(f.read_text("utf-8")))
        assert set(tr) == set(ref), (f.name, sorted(set(ref) ^ set(tr))[:10])
        for k, v in tr.items():
            assert isinstance(v, str) and v.strip(), (f.name, k)
            assert "<" not in v and ">" not in v, (f.name, k)  # hassfest: no HTML
            assert sorted(PH.findall(v)) == sorted(PH.findall(ref[k])), (f.name, k)
