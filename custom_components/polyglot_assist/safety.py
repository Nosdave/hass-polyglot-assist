"""Which entities voice may never switch on its own (built-in path, proposals).

An entity is *protected* when any of these holds:

* its domain is security-relevant (locks, alarms, valves, sirens, cameras) or
  it can run arbitrary actions (scripts, scenes, buttons, automations);
* it is a cover that is not clearly a window covering (blinds, shades,
  shutters, curtains, awnings, windows — a cover without device class must
  be named like one);
* its entity id, name, aliases, Polyglot names, area, device name or
  manufacturer/model contain a word for doors, gates, garages, locks,
  openers, alarms or cars (de/en/fr, whole words);
* a Polyglot command marked ``sensitive`` or ``disabled`` refers to it.

Callers resolve the REAL targets first (Home Assistant's own matching) and
refuse if any target is protected — names alone are not enough (two
entities can share a name).
"""
from __future__ import annotations

import re
from typing import Any

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .proposals import PROTECTED_WORDS  # one word list for built-in path and proposals

PROTECTED_DOMAINS = {
    "lock", "alarm_control_panel", "valve", "siren", "camera",
    "script", "scene", "button", "input_button", "automation",
}
SAFE_COVER_CLASSES = {"blind", "shade", "shutter", "curtain", "awning", "window"}
WINDOW_WORDS = re.compile(
    r"\b(rollladen|rollläden|rolladen|rollo|jalousie|markise|vorhang|fenster|screen|verschattung|"
    r"blind|blinds|shade|shades|shutter|shutters|curtain|awning|window|"
    r"volet|volets|store|stores|rideau|rideaux|fenêtre|fenetre)\b",
    re.I,
)



def _texts_of(hass: HomeAssistant, state: State, extra_names: dict[str, list[str]]) -> list[str]:
    texts = [state.entity_id, state.name or ""]
    entry = er.async_get(hass).async_get(state.entity_id)
    area_id = None
    if entry is not None:
        texts += [a for a in (entry.aliases or []) if isinstance(a, str)]
        area_id = entry.area_id
        if entry.device_id and (device := dr.async_get(hass).async_get(entry.device_id)) is not None:
            texts += [device.name_by_user or "", device.name or "", device.manufacturer or "", device.model or ""]
            area_id = area_id or device.area_id
    if area_id and (area := ar.async_get(hass).async_get_area(area_id)) is not None:
        texts += [area.name, *[a for a in (area.aliases or []) if isinstance(a, str)]]
    texts += extra_names.get(state.entity_id, [])
    return [t for t in texts if t]


def protected_reason(
    hass: HomeAssistant,
    entity_id: str,
    *,
    sensitive_entities: set[str] | frozenset[str] = frozenset(),
    extra_names: dict[str, list[str]] | None = None,
) -> str | None:
    """Why voice/proposals must not switch ``entity_id`` on their own, or None."""
    domain = entity_id.split(".", 1)[0]
    if domain in PROTECTED_DOMAINS:
        return f"{domain} entities are not switched by built-in commands or proposals"
    if entity_id in sensitive_entities:
        return "used by a command marked security-relevant or switched off"
    state = hass.states.get(entity_id)
    if state is None:
        return "unknown entity"
    texts = _texts_of(hass, state, extra_names or {})
    if domain == "cover":
        device_class = state.attributes.get("device_class")
        if device_class not in SAFE_COVER_CLASSES and not (
            device_class is None and any(WINDOW_WORDS.search(t) for t in texts)
        ):
            return "cover that is not clearly a window covering"
    for text in texts:
        if PROTECTED_WORDS.search(text.replace("-", " ")):
            return f"looks security-relevant ({text})"
    return None


def sensitive_entities(config: dict[str, Any]) -> set[str]:
    """Entities referenced by commands marked sensitive or disabled."""
    out: set[str] = set()
    lists = config.get("lists") or {}
    for cfg in (config.get("intents") or {}).values():
        if not isinstance(cfg, dict) or cfg.get("safety") in (None, "normal"):
            continue
        for step in cfg.get("action") or []:
            entity = ((step or {}).get("target") or {}).get("entity_id") if isinstance(step, dict) else None
            if isinstance(entity, str):
                out.add(entity)
        texts = [s for lst in (cfg.get("sentences") or {}).values() if isinstance(lst, list) for s in lst if isinstance(s, str)]
        for ref in {m for t in texts for m in re.findall(r"(?<!\{)\{([^{}:]+)", t)}:
            spec = lists.get(ref.strip())
            for value in (spec or {}).get("values", []) if isinstance(spec, dict) else []:
                target = value.get("out") if isinstance(value, dict) else None
                if isinstance(target, str) and re.match(r"^[a-z_]+\.[a-z0-9_]+$", target):
                    out.add(target)
    return out


def script_or_scene_entities(hass: HomeAssistant, entity_id: str, _depth: int = 0) -> set[str]:
    """Entities a script/scene would touch (recursively), best effort."""
    if _depth > 3:
        return set()
    domain = entity_id.split(".", 1)[0]
    found: set[str] = set()
    try:
        if domain == "script":
            from homeassistant.components.script import entities_in_script  # noqa: PLC0415

            found = set(entities_in_script(hass, entity_id))
        elif domain == "scene":
            from homeassistant.components.homeassistant.scene import entities_in_scene  # noqa: PLC0415

            found = set(entities_in_scene(hass, entity_id))
    except Exception:  # noqa: BLE001
        return {"<unknown>"}
    for inner in list(found):
        if inner.split(".", 1)[0] in ("script", "scene"):
            found |= script_or_scene_entities(hass, inner, _depth + 1)
    return found


def action_target_reason(
    hass: HomeAssistant, entity_id: str, *, sensitive: set[str], extra_names: dict[str, list[str]]
) -> str | None:
    """For PROPOSALS: scripts/scenes are allowed only if everything they touch is harmless."""
    domain = entity_id.split(".", 1)[0]
    if domain in ("script", "scene"):
        if hass.states.get(entity_id) is None:
            return "unknown entity"
        if entity_id in sensitive:
            return "used by a command marked security-relevant or switched off"
        for text in _texts_of(hass, hass.states.get(entity_id), extra_names):
            if PROTECTED_WORDS.search(text.replace("-", " ")):
                return f"looks security-relevant ({text})"
        touched = script_or_scene_entities(hass, entity_id)
        if "<unknown>" in touched:
            return f"cannot inspect what {entity_id} does — add it by hand"
        for inner in touched:
            if inner.split(".", 1)[0] in ("script", "scene"):
                continue
            reason = protected_reason(hass, inner, sensitive_entities=sensitive, extra_names=extra_names)
            if reason:
                return f"{entity_id} touches {inner}: {reason}"
        return None
    if domain in ("button", "input_button"):
        state = hass.states.get(entity_id)
        if state is None:
            return "unknown entity"
        for text in _texts_of(hass, state, extra_names):
            if PROTECTED_WORDS.search(text.replace("-", " ")):
                return f"looks security-relevant ({text})"
        return None
    return protected_reason(hass, entity_id, sensitive_entities=sensitive, extra_names=extra_names)
