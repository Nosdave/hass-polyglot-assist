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

from .proposals import looks_protected  # one word list for built-in path and proposals

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
    # names first: a "Rolltor" with device class shutter is still a gate
    for text in texts:
        if looks_protected(text):
            return f"looks security-relevant ({text})"
    if domain == "cover":
        device_class = state.attributes.get("device_class")
        if device_class not in SAFE_COVER_CLASSES and not (
            device_class is None and any(WINDOW_WORDS.search(t) for t in texts)
        ):
            return "cover that is not clearly a window covering"
    return None


def sensitive_entities(config: dict[str, Any]) -> set[str]:
    """Entities referenced by commands marked sensitive or disabled."""
    out: set[str] = set()
    lists = config.get("lists") or {}
    for cfg in (config.get("intents") or {}).values():
        if not isinstance(cfg, dict) or cfg.get("safety") in (None, "normal"):
            continue
        for step in cfg.get("action") or []:
            if not isinstance(step, dict):
                continue
            for container in (step, step.get("target") or {}, step.get("data") or {}):
                value = container.get("entity_id") if isinstance(container, dict) else None
                for entity in value if isinstance(value, list) else [value]:
                    if isinstance(entity, str):
                        out.update(e.strip() for e in entity.split(",") if e.strip())
        texts = [s for lst in (cfg.get("sentences") or {}).values() if isinstance(lst, list) for s in lst if isinstance(s, str)]
        for ref in {m for t in texts for m in re.findall(r"(?<!\{)\{([^{}:]+)", t)}:
            spec = lists.get(ref.strip())
            for value in (spec or {}).get("values", []) if isinstance(spec, dict) else []:
                target = value.get("out") if isinstance(value, dict) else None
                if isinstance(target, str) and re.match(r"^[a-z_]+\.[a-z0-9_]+$", target):
                    out.add(target)
    return out


# Exact services a script in a proposal may call (scene.apply/create, which
# can set any entity's state incl. locks, are NOT allowed)
SCRIPT_SERVICES = {
    *(f"{d}.{v}" for d in ("light", "switch", "fan", "input_boolean", "media_player", "humidifier", "vacuum")
      for v in ("turn_on", "turn_off", "toggle")),
    "cover.open_cover", "cover.close_cover", "cover.stop_cover", "cover.set_cover_position",
    "climate.set_temperature", "climate.set_hvac_mode", "climate.turn_on", "climate.turn_off",
    "media_player.media_play", "media_player.media_pause", "media_player.media_stop",
    "media_player.volume_set", "media_player.select_source",
    "input_number.set_value", "input_select.select_option", "input_text.set_value",
    "number.set_value", "select.select_option", "fan.set_percentage",
    "scene.turn_on", "script.turn_on", "script.turn_off",
    "timer.start", "timer.cancel", "counter.increment",
    "tts.speak", "notify.persistent_notification", "persistent_notification.create",
}
_ENTITY = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
_TARGET_KEYS = ("area_id", "device_id", "floor_id", "label_id")


def _contains(obj: Any, keys: tuple[str, ...]) -> bool:
    if isinstance(obj, dict):
        return any(k in keys or _contains(v, keys) for k, v in obj.items())
    if isinstance(obj, list):
        return any(_contains(v, keys) for v in obj)
    return False


def _has_template(obj: Any) -> bool:
    if isinstance(obj, str):
        return "{{" in obj or "{%" in obj
    if isinstance(obj, dict):
        return any(_has_template(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_template(v) for v in obj)
    return False


def _entities_of(step: dict[str, Any]) -> list[str] | None:
    """Literal entity ids of a service step, None if not literal."""
    found: list[str] = []
    for container in (step, step.get("target") or {}, step.get("data") or {}):
        if not isinstance(container, dict) or "entity_id" not in container:
            continue
        value = container["entity_id"]
        values = value if isinstance(value, list) else [value]
        for v in values:
            if not isinstance(v, str) or not _ENTITY.match(v.strip()):
                return None
            found.append(v.strip())
    return found


def script_reason(
    hass: HomeAssistant, entity_id: str, *, sensitive: set[str], extra_names: dict[str, list[str]],
    _depth: int = 0,
) -> str | None:
    """None if a script only runs simple, static, harmless service calls."""
    if _depth > 3:
        return "scripts nested too deeply — add it by hand"
    component = hass.data.get("script")
    entity = component.get_entity(entity_id) if component is not None and hasattr(component, "get_entity") else None
    raw = getattr(entity, "raw_config", None)
    if not isinstance(raw, dict) or not isinstance(raw.get("sequence"), list):
        return f"cannot inspect what {entity_id} does — add it by hand"
    for step in raw["sequence"]:
        if not isinstance(step, dict):
            return f"{entity_id}: unknown step — add it by hand"
        keys = set(step) - {"alias", "enabled", "continue_on_error", "response_variable"}
        if keys <= {"delay"} and not _has_template(step):
            continue
        service = step.get("action", step.get("service"))
        if not isinstance(service, str) or keys - {"action", "service", "target", "data", "entity_id", "metadata"}:
            return f"{entity_id} has conditions, branches or device actions — add it by hand"
        if _has_template(step) or "{" in service:
            return f"{entity_id} uses templates — add it by hand"
        if _contains(step, _TARGET_KEYS):
            return f"{entity_id} targets areas/devices/floors/labels — add it by hand"
        domain = service.split(".", 1)[0]
        if service not in SCRIPT_SERVICES and not (domain == "script" and _ENTITY.match(service)):
            return f"{entity_id} calls {service} — add it by hand"
        entities = _entities_of(step)
        if entities is None:
            return f"{entity_id}: targets are not plain entity ids — add it by hand"
        if domain == "script" and service not in ("script.turn_on", "script.turn_off"):
            entities = [service]  # script.<name> called as an action
        if service in ("scene.turn_on", "script.turn_on") and not entities:
            return f"{entity_id}: {service} without a plain entity target — add it by hand"
        for inner in entities:
            inner_domain = inner.split(".", 1)[0]
            if inner_domain == "script":
                reason = script_reason(hass, inner, sensitive=sensitive, extra_names=extra_names, _depth=_depth + 1)
            elif inner_domain == "scene":
                reason = scene_reason(hass, inner, sensitive=sensitive, extra_names=extra_names)
            else:
                reason = protected_reason(hass, inner, sensitive_entities=sensitive, extra_names=extra_names)
            if reason:
                return f"{entity_id} → {inner}: {reason}"
    return None


def scene_reason(hass: HomeAssistant, entity_id: str, *, sensitive: set[str], extra_names: dict[str, list[str]]) -> str | None:
    try:
        from homeassistant.components.homeassistant.scene import entities_in_scene  # noqa: PLC0415

        touched = entities_in_scene(hass, entity_id)
    except Exception:  # noqa: BLE001
        return f"cannot inspect what {entity_id} does — add it by hand"
    if not touched:
        return f"cannot inspect what {entity_id} does — add it by hand"
    for inner in touched:
        reason = protected_reason(hass, inner, sensitive_entities=sensitive, extra_names=extra_names)
        if reason:
            return f"{entity_id} → {inner}: {reason}"
    return None


def action_target_reason(
    hass: HomeAssistant, entity_id: str, *, sensitive: set[str], extra_names: dict[str, list[str]]
) -> str | None:
    """For PROPOSALS: scripts/scenes are allowed only if everything they touch is harmless."""
    domain = entity_id.split(".", 1)[0]
    if domain in ("script", "scene"):
        state = hass.states.get(entity_id)
        if state is None:
            return "unknown entity"
        if entity_id in sensitive:
            return "used by a command marked security-relevant or switched off"
        for text in _texts_of(hass, state, extra_names):
            if looks_protected(text):
                return f"looks security-relevant ({text})"
        if domain == "script":
            return script_reason(hass, entity_id, sensitive=sensitive, extra_names=extra_names)
        return scene_reason(hass, entity_id, sensitive=sensitive, extra_names=extra_names)
    if domain in ("button", "input_button"):
        state = hass.states.get(entity_id)
        if state is None:
            return "unknown entity"
        for text in _texts_of(hass, state, extra_names):
            if looks_protected(text):
                return f"looks security-relevant ({text})"
        return None
    return protected_reason(hass, entity_id, sensitive_entities=sensitive, extra_names=extra_names)
