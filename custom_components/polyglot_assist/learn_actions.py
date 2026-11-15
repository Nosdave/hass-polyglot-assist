"""Learning NEW commands from repeated AI actions — the pure part (no Home Assistant needed).

When the AI keeps doing the same KIND of thing for sentences Polyglot does not
know — "open the shutters in <room>", "lights in <room> off", "run script X
for <room>" — that is a missing command, even if the words differ every time
("Rollladen im Schlafzimmer auf", "Mach die Rollläden im Wohnzimmer hoch").

* Runs are grouped by what the AI DID (tool + normalised core arguments), not
  by the words; the room is the placeholder, so it is left out of the key.
* A group counts when it happened ≥ MIN_RUNS times within WINDOW_DAYS, on at
  least two different days or in two different conversations (one chatty
  evening is not a habit).
* Only a small allowlist of action PATTERNS is learned. The action of a
  proposal is built by code from the pattern (proposals.pattern_action); the
  AI only writes the sentences, the answer and picks the room list. Anything
  security-relevant (locks, alarms, gates, garage doors, vehicles …) never
  forms a group.
"""
from __future__ import annotations

import ast
import json
import re
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any

WINDOW_DAYS = 7
MIN_RUNS = 2
MAX_GROUPS = 3
MAX_CALLS = 5  # the monitor keeps the first 5 calls of a turn: more → unknown
READ_TOOLS = ("GetLiveContext", "HassGetState")
NAME_RE = re.compile(r"^[A-Z][A-Za-z0-9]{2,59}$")
LANG_RE = re.compile(r"^[a-z]{2,3}(-[a-z]{2,4})?$")
# cover device classes that are window coverings (same set as safety.SAFE_COVER_CLASSES)
WINDOW_CLASSES = {"blind", "shade", "shutter", "curtain", "awning", "window"}
# never learned, whatever the AI did (also checked again per entity when accepting)
UNSAFE_DOMAINS = {"lock", "alarm_control_panel", "valve", "siren", "camera", "automation", "button",
                  "input_button", "scene", "vacuum", "lawn_mower", "climate", "water_heater", "update"}
UNSAFE_CLASSES = {"garage", "gate", "door", "damper"}
SCRIPT_CONSTANT_RE = re.compile(r"^[a-z_][a-z0-9_]{0,30}$")

# pattern id → what it does (for the AI and the panel); the action itself is built by
# proposals.pattern_action — the only place that knows services and shapes
PATTERNS: dict[str, str] = {
    "cover_area_open": "open the window coverings (shutters, blinds) of one room",
    "cover_area_close": "close the window coverings (shutters, blinds) of one room",
    "light_area_on": "turn on the lights of one room",
    "light_area_off": "turn off the lights of one room",
    "script_room": "run a script for one room (the room is passed to the script)",
}


def tool_name(tool: Any) -> str:
    """'assist__intent__HassTurnOn' → 'HassTurnOn' (prefixes differ per API)."""
    return str(tool or "").rsplit("__", 1)[-1]


def _script_id(tool: Any) -> str | None:
    """'assist__script__licht_x' → 'licht_x' (the tool of an exposed script)."""
    parts = str(tool or "").split("__")
    if len(parts) >= 2 and parts[-2] == "script" and re.match(r"^[a-z0-9_]+$", parts[-1]):
        return parts[-1]
    return None


def call_args(call: dict[str, Any]) -> dict[str, Any] | None:
    """The arguments of a recorded call (``target`` is whole, ``args`` may be a cut repr)."""
    args: Any = call.get("args")
    if isinstance(args, str):
        try:
            args = ast.literal_eval(args)
        except (ValueError, SyntaxError):
            args = None
    if not isinstance(args, dict):
        args = None
    target = call.get("target") if isinstance(call.get("target"), dict) else {}
    if args is None and not target:
        return None
    return {**(args or {}), **target}


def _as_list(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    return sorted({str(v).strip().lower() for v in values if isinstance(v, str) and v.strip()})


def classify(call: dict[str, Any]) -> tuple[str, str, dict[str, Any]] | None:
    """(pattern, group key, details) of one tool call — or None (not a learnable pattern).

    The room (area / script argument) is the placeholder: it is NOT part of the key.
    """
    args = call_args(call)
    if args is None:
        return None
    tool = tool_name(call.get("tool"))
    script = _script_id(call.get("tool"))
    domains = _as_list(args.get("domain"))
    classes = _as_list(args.get("device_class"))
    if set(domains) & UNSAFE_DOMAINS or set(classes) & UNSAFE_CLASSES:
        return None
    if tool in ("HassTurnOn", "HassTurnOff"):
        area = args.get("area")
        if not isinstance(area, str) or not area.strip() or args.get("name") or args.get("floor"):
            return None  # a named device, a floor or no room: not a room command
        on = tool == "HassTurnOn"
        if classes and set(classes) <= WINDOW_CLASSES and domains in ([], ["cover"]):
            pattern = "cover_area_open" if on else "cover_area_close"
        elif domains == ["cover"] and not classes:
            pattern = "cover_area_open" if on else "cover_area_close"
        elif domains == ["light"] and not classes:
            pattern = "light_area_on" if on else "light_area_off"
        else:
            return None
        return pattern, pattern, {"room": area.strip()}
    if script:
        # exactly one text argument (the room); the others must be fixed numbers or short words
        texts = {k: v for k, v in args.items() if isinstance(v, str) and k not in ("domain", "device_class")}
        consts = {k: v for k, v in args.items() if k not in texts}
        if len(texts) != 1 or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in consts.values()):
            return None
        (room_arg, room), = texts.items()
        if not room.strip() or not SCRIPT_CONSTANT_RE.match(room_arg):
            return None
        key = "script_room:" + script + ":" + room_arg + ":" + json.dumps(consts, sort_keys=True)
        return "script_room", key, {"room": room.strip(), "script": script, "room_arg": room_arg, "constants": consts}
    return None


def _ts(entry: dict[str, Any]) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(entry.get("ts")))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def run_pattern(entry: dict[str, Any]) -> tuple[str, str, list[dict[str, Any]]] | None:
    """The ONE pattern of an assistant turn: every switching call must be of the same group."""
    if entry.get("result") != "assistant" or entry.get("error") or not entry.get("text"):
        return None
    calls = [c for c in (entry.get("targets") or []) if not str(c.get("tool", "")).endswith(READ_TOOLS)]
    if not calls or len(entry.get("targets") or []) >= MAX_CALLS:
        return None
    found = [classify(c) for c in calls]
    if any(f is None for f in found) or len({f[1] for f in found}) != 1:
        return None
    return found[0][0], found[0][1], [f[2] for f in found]


def find_repeated_actions(
    entries: list[dict[str, Any]], *, now: datetime | None = None, days: int = WINDOW_DAYS,
    min_runs: int = MIN_RUNS, tz: tzinfo = timezone.utc,
) -> list[dict[str, Any]]:
    """Groups of assistant turns that did the same learnable thing, newest-evidence first."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    groups: dict[str, dict[str, Any]] = {}
    for e in entries:
        ts = _ts(e)
        if ts is None or ts < since:
            continue
        found = run_pattern(e)
        if found is None:
            continue
        pattern, key, details = found
        g = groups.setdefault(key, {"key": key, "pattern": pattern, "runs": []})
        g["runs"].append({"text": str(e.get("text"))[:200], "lang": e.get("lang"), "ts": ts.isoformat(),
                          "conversation": e.get("conversation"), "rooms": [d["room"] for d in details],
                          "details": details[0], "targets": (e.get("targets") or [])[:3]})
    out = []
    for g in groups.values():
        runs = sorted(g["runs"], key=lambda r: r["ts"])
        if not qualifies(runs, min_runs=min_runs, tz=tz):
            continue
        out.append({**g, "runs": runs[-8:], "count": len(runs),
                    "days": len({datetime.fromisoformat(r["ts"]).astimezone(tz).date() for r in runs}),
                    "conversations": len({r["conversation"] for r in runs if r.get("conversation")}),
                    "rooms": sorted({room for r in runs for room in r["rooms"]}),
                    "langs": sorted({r["lang"] for r in runs if isinstance(r.get("lang"), str)}),
                    "details": runs[-1]["details"]})
    out.sort(key=lambda g: (-g["count"], g["key"]))
    return out[:MAX_GROUPS]


def qualifies(runs: list[dict[str, Any]], *, min_runs: int = MIN_RUNS, tz: tzinfo = timezone.utc) -> bool:
    """≥ min_runs runs, on ≥ 2 different days OR in ≥ 2 different conversations."""
    if len(runs) < min_runs:
        return False
    days = {datetime.fromisoformat(r["ts"]).astimezone(tz).date() for r in runs}
    convs = {r["conversation"] for r in runs if r.get("conversation")}
    return len(days) >= 2 or len(convs) >= 2


def covered(group: dict[str, Any], matches) -> str | None:
    """An existing command already recognises a sentence of the group → its name (no proposal)."""
    for run in group.get("runs") or []:
        hit = matches(run["text"], run.get("lang"))
        if hit:
            return sorted(hit)[0] if isinstance(hit, (set, list, tuple)) else str(hit)
    return None


def fingerprint(group: dict[str, Any]) -> str:
    """Stable id of the missing command: once rejected, never proposed again."""
    return "repeated_action:" + group["key"]


# ── the AI's reply ───────────────────────────────────────────────────────────

_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")


def _json_object(text: str) -> Any:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None


def parse_reply(text: str, *, languages: list[str], lists: list[str]) -> dict[str, Any] | str:
    """The AI's JSON answer → {name, list, description, sentences{lang:[…]}, speech{lang:…}} or a reason."""
    data = _json_object(text)
    if not isinstance(data, dict):
        return "no JSON object"
    if data.get("none"):
        return "ai_none: " + str(data.get("none"))[:120]
    name, lst = data.get("name"), data.get("list")
    if not isinstance(name, str) or not NAME_RE.match(name):
        return "invalid name"
    if lst not in lists:
        return "unknown list"
    sentences: dict[str, list[str]] = {}
    for lang, items in (data.get("sentences") or {}).items() if isinstance(data.get("sentences"), dict) else []:
        if lang not in languages or not isinstance(items, list):
            continue
        good = []
        for s in items[:6]:
            if not isinstance(s, str) or not s.strip() or len(s) > 300 or "<" in s or ">" in s:
                continue
            refs = _PLACEHOLDER.findall(s)
            if refs != [lst]:  # exactly the chosen room list, once — nothing else
                continue
            good.append(" ".join(s.split()))
        if good:
            sentences[lang] = list(dict.fromkeys(good))
    if not sentences:
        return "no usable sentences"
    speech: dict[str, str] = {}
    for lang, said in (data.get("speech") or {}).items() if isinstance(data.get("speech"), dict) else []:
        if lang in sentences and isinstance(said, str) and said.strip() and len(said) <= 120 \
                and not any(ch in said for ch in "{}%#<>"):
            speech[lang] = said.strip()
    description = data.get("description")
    description = " ".join(description.split())[:160] if isinstance(description, str) else ""
    return {"name": name, "list": lst, "description": description, "sentences": sentences, "speech": speech}


def evidence_lines(group: dict[str, Any]) -> list[str]:
    """The runs as fenced data lines for the prompt (what was said, what the AI did)."""
    out = []
    for r in group.get("runs") or []:
        calls = "; ".join(f"{tool_name(c.get('tool'))} {str(c.get('target') or c.get('args'))[:120]}"
                          for c in r.get("targets") or [])
        out.append(f"- [{r.get('lang') or '?'}] {r['text']} → {calls}")
    return out
