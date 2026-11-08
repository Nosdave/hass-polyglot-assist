"""Spoken answers built from blocks (pure — no Home Assistant needed).

An answer per language is a list of blocks:

* ``{"t": "satz", "p": [ "text", {value}, "text", … ]}`` — a sentence: text
  pieces with entity values in between;
* ``{"t": "cond", "e": entity, "cases": [{"op": ">"|"<"|"=", "x": number|str,
  "p": [...]}, …], "other": [...]}`` — "depending on the value".

A value is ``{"e": entity_id, "f": "unit"|"value"|"state"|"name", "r": 0..2,
"na": "text if unavailable"}``.

The Jinja template that Home Assistant renders is ALWAYS generated here, never
written by an AI or a proposal: literal text is a quoted string expression (a
"{{" or "{% endraw %}" typed by someone stays text), values use a fixed set of expressions,
units are spoken words per language ("W" → "Watt"), numbers use the decimal
comma where the language does.
"""
from __future__ import annotations

import json
import re
from typing import Any

FORMATS = ("unit", "value", "state", "name")
OPS = (">", "<", "=")
MAX_BLOCKS = 12
MAX_PARTS = 24
MAX_CASES = 6
MAX_TEXT = 400
ENTITY_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
DECIMAL_COMMA = {"de", "fr", "nl", "it", "es", "pt", "da", "sv", "nb", "fi", "pl", "cs", "ru"}

# how a unit is SPOKEN (TTS reads "W" or "°C" badly); unknown units stay as they are
SPOKEN_UNITS: dict[str, dict[str, str]] = {
    "de": {"W": "Watt", "kW": "Kilowatt", "Wh": "Wattstunden", "kWh": "Kilowattstunden", "°C": "Grad", "°F": "Grad Fahrenheit",
           "%": "Prozent", "min": "Minuten", "h": "Stunden", "s": "Sekunden", "km/h": "Kilometer pro Stunde",
           "mm": "Millimeter", "lx": "Lux", "V": "Volt", "A": "Ampere", "€": "Euro", "ppm": "ppm", "hPa": "Hektopascal"},
    "en": {"W": "watts", "kW": "kilowatts", "Wh": "watt hours", "kWh": "kilowatt hours", "°C": "degrees", "°F": "degrees Fahrenheit",
           "%": "percent", "min": "minutes", "h": "hours", "s": "seconds", "km/h": "kilometres per hour",
           "mm": "millimetres", "lx": "lux", "V": "volts", "A": "amps", "€": "euros", "ppm": "ppm", "hPa": "hectopascals"},
    "fr": {"W": "watts", "kW": "kilowatts", "Wh": "wattheures", "kWh": "kilowattheures", "°C": "degrés", "°F": "degrés Fahrenheit",
           "%": "pour cent", "min": "minutes", "h": "heures", "s": "secondes", "km/h": "kilomètres heure",
           "mm": "millimètres", "lx": "lux", "V": "volts", "A": "ampères", "€": "euros", "ppm": "ppm", "hPa": "hectopascals"},
}
# how common states are SPOKEN
SPOKEN_STATES: dict[str, dict[str, str]] = {
    "de": {"on": "an", "off": "aus", "open": "offen", "closed": "geschlossen", "home": "zu Hause", "not_home": "weg",
           "playing": "spielt", "paused": "pausiert", "idle": "bereit", "unavailable": "nicht verfügbar", "unknown": "unbekannt"},
    "en": {"on": "on", "off": "off", "open": "open", "closed": "closed", "home": "home", "not_home": "away",
           "playing": "playing", "paused": "paused", "idle": "idle", "unavailable": "unavailable", "unknown": "unknown"},
    "fr": {"on": "allumé", "off": "éteint", "open": "ouvert", "closed": "fermé", "home": "à la maison", "not_home": "absent",
           "playing": "en lecture", "paused": "en pause", "idle": "prêt", "unavailable": "indisponible", "unknown": "inconnu"},
}
DEFAULT_NA = {"de": "unbekannt", "en": "unknown", "fr": "inconnu"}


class AnswerError(ValueError):
    """Blocks that cannot become an answer (message safe to show)."""


def _base(lang: str) -> str:
    return (lang or "en").split("-")[0].lower()


def spoken_unit(unit: str | None, lang: str) -> str:
    if not unit:
        return ""
    return SPOKEN_UNITS.get(_base(lang), {}).get(unit, unit)


# ── validation ───────────────────────────────────────────────────────────────
def _check_text(text: Any) -> str:
    if not isinstance(text, str) or len(text) > MAX_TEXT:
        raise AnswerError("text pieces must be short text")
    return text


def _check_value(v: Any) -> dict[str, Any]:
    if not isinstance(v, dict) or set(v) - {"e", "f", "r", "na"}:
        raise AnswerError("a value is {e, f, r, na}")
    if not isinstance(v.get("e"), str) or not ENTITY_RE.match(v["e"]):
        raise AnswerError("a value needs an entity id")
    out = {"e": v["e"], "f": v.get("f", "unit"), "r": v.get("r", 0)}
    if out["f"] not in FORMATS:
        raise AnswerError(f"format must be one of {', '.join(FORMATS)}")
    if not isinstance(out["r"], int) or isinstance(out["r"], bool) or not 0 <= out["r"] <= 2:
        raise AnswerError("rounding must be 0, 1 or 2")
    if v.get("na") is not None:
        out["na"] = _check_text(v["na"])[:60]
    return out


def _check_parts(parts: Any) -> list:
    if not isinstance(parts, list) or len(parts) > MAX_PARTS:
        raise AnswerError(f"a sentence has at most {MAX_PARTS} pieces")
    return [_check_text(p) if isinstance(p, str) else _check_value(p) for p in parts]


def validate(blocks: Any) -> list[dict[str, Any]]:
    """Normalised blocks, or AnswerError — the ONLY shape an answer may have."""
    if not isinstance(blocks, list) or len(blocks) > MAX_BLOCKS:
        raise AnswerError(f"an answer has at most {MAX_BLOCKS} blocks")
    out: list[dict[str, Any]] = []
    for b in blocks:
        if not isinstance(b, dict):
            raise AnswerError("unknown block")
        if b.get("t") == "satz":
            out.append({"t": "satz", "p": _check_parts(b.get("p"))})
        elif b.get("t") == "cond":
            if not isinstance(b.get("e"), str) or not ENTITY_RE.match(b["e"]):
                raise AnswerError("'depending on' needs an entity")
            cases = b.get("cases")
            if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES:
                raise AnswerError(f"1 to {MAX_CASES} cases")
            norm_cases = []
            for c in cases:
                if not isinstance(c, dict) or c.get("op") not in OPS:
                    raise AnswerError("a case needs >, < or =")
                x = c.get("x")
                if c["op"] in (">", "<") and (isinstance(x, bool) or not isinstance(x, (int, float))):
                    raise AnswerError("> and < compare with a number")
                if c["op"] == "=" and not isinstance(x, (str, int, float)) or isinstance(x, bool):
                    raise AnswerError("= compares with a value")
                if isinstance(x, str):
                    x = _check_text(x)[:60]
                norm_cases.append({"op": c["op"], "x": x, "p": _check_parts(c.get("p"))})
            out.append({"t": "cond", "e": b["e"], "cases": norm_cases, "other": _check_parts(b.get("other") or [])})
        else:
            raise AnswerError("unknown block")
    return out


def read_entities(blocks: list[dict[str, Any]]) -> list[str]:
    """Every entity an answer reads (for the exposure/protection check)."""
    found: list[str] = []

    def add(e: str) -> None:
        if e not in found:
            found.append(e)

    def parts(ps):
        for p in ps:
            if isinstance(p, dict):
                add(p["e"])

    for b in blocks:
        if b["t"] == "satz":
            parts(b["p"])
        else:
            add(b["e"])
            for c in b["cases"]:
                parts(c["p"])
            parts(b["other"])
    return found


# ── compiling to the Jinja template HA renders ──────────────────────────────
def _raw(text: str) -> str:
    """Literal text as a quoted Jinja string expression — no text can ever end it or become code."""
    if not text:
        return ""
    return "{{ " + _lit(text) + " }}"


def _lit(text: str) -> str:
    """A Python/Jinja string literal (json quoting is valid Jinja)."""
    return json.dumps(text, ensure_ascii=False)


def _value(v: dict[str, Any], lang: str, unit_of: dict[str, str]) -> str:
    e, f, r = v["e"], v["f"], v["r"]
    lang = _base(lang)
    na = v.get("na") or DEFAULT_NA.get(lang, "unknown")
    s = f"states({_lit(e)})"
    if f == "name":
        return f"{{{{ state_attr({_lit(e)}, 'friendly_name') or {_lit(e)} }}}}"
    if f == "state":
        table = SPOKEN_STATES.get(lang, {})
        return (f"{{% set _s = {s} %}}{{% if _s in ['unavailable', 'unknown', 'none', ''] %}}{_raw(na)}"
                f"{{% else %}}{{{{ {json.dumps(table, ensure_ascii=False)}.get(_s, _s) }}}}{{% endif %}}")
    if r == 0:
        num = "(_s | float | round(0) | int)"
    else:
        num = f"('%.{r}f' | format(_s | float))"
        if lang in DECIMAL_COMMA:
            num = f"{num} | replace('.', ',')"
    unit = spoken_unit(unit_of.get(e), lang) if f == "unit" else ""
    tail = f" ~ {_lit(' ' + unit)}" if unit else ""
    return (f"{{% set _s = {s} %}}{{% if not (_s | is_number) %}}{_raw(na)}"
            f"{{% else %}}{{{{ ({num}){tail} }}}}{{% endif %}}")


def _parts(parts: list, lang: str, unit_of: dict[str, str]) -> str:
    return "".join(_raw(p) if isinstance(p, str) else _value(p, lang, unit_of) for p in parts)


def compile_answer(blocks: list[dict[str, Any]], lang: str, unit_of: dict[str, str] | None = None) -> str:
    """Blocks (validated here) → the template. ``unit_of``: entity → unit_of_measurement."""
    blocks = validate(blocks)
    unit_of = unit_of or {}
    out: list[str] = []
    for b in blocks:
        if b["t"] == "satz":
            out.append(_parts(b["p"], lang, unit_of))
            continue
        e = _lit(b["e"])
        branches = []
        for i, c in enumerate(b["cases"]):
            kw = "if" if i == 0 else "elif"
            if c["op"] == "=" and isinstance(c["x"], str):
                cond = f"states({e}) == {_lit(c['x'])}"
            elif c["op"] == "=":  # numbers compare as numbers ("20" == "20.0")
                cond = f"_c is not none and _c == {c['x']!r}"
            else:
                cond = f"_c is not none and _c {c['op']} {c['x']!r}"
            branches.append(f"{{% {kw} {cond} %}}{_parts(c['p'], lang, unit_of)}")
        out.append(f"{{% set _c = states({e}) | float(none) %}}" + "".join(branches)
                   + f"{{% else %}}{_parts(b['other'], lang, unit_of)}{{% endif %}}")
    return " ".join(x for x in out if x)


def is_block_answer(template: str, blocks: Any, lang: str, unit_of: dict[str, str] | None = None) -> bool:
    """True when ``template`` is exactly what ``blocks`` compile to (the editor may show blocks)."""
    try:
        return compile_answer(blocks, lang, unit_of) == template
    except AnswerError:
        return False


def materialize(intent_cfg: dict[str, Any], unit_of: dict[str, str] | None = None) -> dict[str, Any]:
    """A copy of ``intent_cfg`` whose ``speech`` is compiled from its ``answer_blocks``.

    Proposals carry blocks only; the template is generated HERE, at acceptance,
    with the real units — so no AI and no proposal ever supplies Jinja.
    """
    blocks = intent_cfg.get("answer_blocks")
    if not blocks:
        return intent_cfg
    if not isinstance(blocks, dict):
        raise AnswerError("answer_blocks must map languages to blocks")
    out = dict(intent_cfg)
    out["answer_blocks"] = {lang: validate(b) for lang, b in blocks.items()}
    out["speech"] = {**(intent_cfg.get("speech") or {}),
                     **{lang: compile_answer(b, lang, unit_of) for lang, b in out["answer_blocks"].items()}}
    return out


def blocks_entities(intent_cfg: dict[str, Any]) -> list[str]:
    """Entities every language's answer blocks read."""
    found: list[str] = []
    for blocks in (intent_cfg.get("answer_blocks") or {}).values():
        for e in read_entities(validate(blocks)):
            if e not in found:
                found.append(e)
    return found
