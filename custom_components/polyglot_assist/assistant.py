"""Proposal generators: starter set ("Grundstock") and new devices.

Everything here only fills the proposal queue; nothing becomes active
without the user accepting it.

* Commands for exposed scripts, scenes and buttons that Polyglot does not
  cover yet — the action is built here (allowlisted, see proposals.py), the
  model only proposes the sentences.
* Spoken names in the enabled languages for rooms and devices that lack one.
* Category ("Art") and kind (command/question) for existing commands, by
  simple keyword rules (no model needed).
"""
from __future__ import annotations

import re
from typing import Any

from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .corpus import OTHER_CATEGORY, names_table, static_lists
from .proposals import check_item
from .safety import action_target_reason, protected_reason, sensitive_entities
from .translate import async_propose_names, async_propose_new_command

COMMAND_DOMAINS = {"script": "script.turn_on", "scene": "scene.turn_on",
                   "button": "button.press", "input_button": "input_button.press"}
NAME_DOMAINS = ("light", "switch", "cover", "fan", "media_player", "climate", "script", "scene")
MAX_COMMANDS_PER_RUN = 8
MAX_NAME_TARGETS_PER_RUN = 8

# keyword → category (German default names, editable by the user)
CATEGORY_RULES = [
    (r"licht|lamp|light|leuchte", "Licht"),
    (r"energie|strom|batterie|solar|power|energy", "Energie"),
    (r"wetter|weather|regen|temperatur|météo", "Wetter"),
    (r"waesche|wäsche|wasch|trockner|laundry|dryer", "Wäsche"),
    (r"abfahrt|fahrzeit|zug|bus|route|verkehr|commute|transit", "Unterwegs"),
    (r"musik|music|radio|fernseh|\btv\b|media|film", "Medien"),
    (r"(^|[^a-z])(tür|tuer|door|garage|gate|lock|auto|car)([^a-z]|$)", "Zugang & Fahrzeug"),
    (r"rolll|rollo|jalousie|verschatt|\bcover\b|blind|shutter", "Rollläden"),
    (r"einkauf|shopping|einkaufsliste", "Einkauf"),
]
QUESTION_RE = re.compile(r"status|frage|question|\bwie\b|welche|\bwann\b|\binfo|wetter|weather|abfahrt|fertig|\bdone\b", re.I)


def _camel(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss"))
    name = "".join(w[:1].upper() + w[1:] for w in words)[:60]
    return name if name[:1].isalpha() else f"Cmd{name}"


def covered_entities(config: dict[str, Any]) -> set[str]:
    """Entity ids Polyglot already knows: list values, actions, name table."""
    out: set[str] = set(names_table(config))
    for spec in static_lists(config).values():
        for value in (spec or {}).get("values", []) if isinstance(spec, dict) else []:
            target = value.get("out") if isinstance(value, dict) else None
            if isinstance(target, str) and "." in target:
                out.add(target)
    for cfg in (config.get("intents") or {}).values():
        if isinstance(cfg, dict):
            for step in cfg.get("action") or []:
                entity = ((step or {}).get("target") or {}).get("entity_id") if isinstance(step, dict) else None
                if isinstance(entity, str):
                    out.add(entity)
    return out


def classify_existing(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Category / kind proposals for commands that have none (keyword rules)."""
    items: list[dict[str, Any]] = []
    for name, cfg in (config.get("intents") or {}).items():
        if not isinstance(cfg, dict):
            continue
        text = f"{name} {cfg.get('description', '')}".lower()
        if not cfg.get("category"):
            category = next((cat for rx, cat in CATEGORY_RULES if re.search(rx, text)), OTHER_CATEGORY)
            items.append({
                "op": "set_field", "payload": {"intent": name, "field": "category", "value": category},
                "title": f"{name}: Art „{category}“",
                "origin": {"source": "starter", "detail": "nach Name und Beschreibung des Befehls"},
                "check": {"status": "ok", "notes": []},
            })
        if not cfg.get("kind"):
            kind = "question" if QUESTION_RE.search(text) else "command"
            items.append({
                "op": "set_field", "payload": {"intent": name, "field": "kind", "value": kind},
                "title": f"{name}: {'Frage (antwortet immer gesprochen)' if kind == 'question' else 'Schaltbefehl (Ton möglich)'}",
                "origin": {"source": "starter", "detail": "nach Name und Beschreibung des Befehls"},
                "check": {"status": "warn", "notes": ["bitte prüfen"]},
            })
    return items


async def async_discover(
    hass: HomeAssistant,
    runtime: dict[str, Any],
    *,
    ai_task: str,
    languages: list[str],
    only_new: bool,
    context: Context | None,
) -> dict[str, Any]:
    """Generate proposals for uncovered devices (and, with only_new, just new ones)."""
    matcher = runtime["matcher"]
    config = matcher.config
    slot_lists = {**matcher.static_slot_lists(), **runtime["lists"].async_get()}
    entry_id = runtime["entry"].entry_id
    seen_store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.seen.{entry_id}")
    stored_seen = await seen_store.async_load()
    seen = set((stored_seen or {}).get("entities", []))

    registry = er.async_get(hass)
    exposed = [
        s for s in hass.states.async_all()
        if async_should_expose(hass, "conversation", s.entity_id)
    ]
    covered = covered_entities(config)
    if only_new and stored_seen is None:
        # first run: remember today's devices as the baseline, propose nothing
        await seen_store.async_save({"entities": sorted(s.entity_id for s in exposed)})
        return {"items": [], "errors": [], "baseline": len(exposed)}
    sensitive = sensitive_entities(config)
    extra = names_table(config)
    # protected devices are never proposed (locks, doors, garage, cars, …)
    exposed = [
        s for s in exposed
        if s.domain in ("script", "scene", "button", "input_button")
        or protected_reason(hass, s.entity_id, sensitive_entities=sensitive, extra_names=extra) is None
    ]
    fresh = [s for s in exposed if s.entity_id not in seen] if only_new else exposed
    processed: set[str] = set()
    origin_source = "new_devices" if only_new else "starter"
    items: list[dict[str, Any]] = []
    errors: list[str] = []

    # 1) commands for scripts / scenes / buttons
    for state in [s for s in fresh if s.domain in COMMAND_DOMAINS and s.entity_id not in covered][:MAX_COMMANDS_PER_RUN]:
        processed.add(state.entity_id)
        if action_target_reason(hass, state.entity_id, sensitive=sensitive, extra_names=extra):
            continue  # script/scene/button that touches something protected
        name = _camel(f"{state.domain} {state.name}")
        if name in (config.get("intents") or {}):
            continue
        action = [{"action": COMMAND_DOMAINS[state.domain], "target": {"entity_id": state.entity_id}}]
        what = f"{state.domain} named {state.name!r}"
        proposals = await async_propose_new_command(
            hass, ai_task_entity=ai_task, name=name, what=what, languages=languages,
            matcher=matcher, slot_lists=slot_lists, context=context,
        )
        sentences = {}
        notes: list[str] = []
        for lang, res in proposals.items():
            if isinstance(res, dict):
                errors.append(f"{state.entity_id}/{lang}: {res.get('error')}")
                continue
            good = [r["sentence"] for r in res if r["status"] != "error"]
            notes += [f"{lang}: {n}" for r in res if r["status"] == "error" for n in r["notes"][:1]]
            if good:
                sentences[lang] = good
        if not sentences:
            continue
        intent_cfg = {
            "description": f"{state.name} starten ({state.entity_id})",
            "category": OTHER_CATEGORY, "kind": "command", "action": action,
            "sentences": sentences,
        }
        item = {
            "op": "add_intent", "payload": {"name": name, "intent": intent_cfg},
            "title": f"Neuer Befehl „{state.name}“ in {', '.join(sentences)}",
            "origin": {"source": origin_source,
                       "detail": f"{state.entity_id} ist für Assist freigegeben, Polyglot kennt noch keinen Befehl dafür"},
            "check": {"status": "ok", "notes": notes[:5]},
        }
        reason = check_item(item)
        if reason:
            item["check"] = {"status": "error", "notes": [reason]}
        items.append(item)

    # 2) spoken names for rooms and devices in the other enabled languages
    table = names_table(config)
    primary = hass.config.language.split("-")[0]
    other_langs = [lang for lang in languages if lang != primary]
    targets: dict[str, tuple[str, str, str]] = {}
    areas = ar.async_get(hass)
    if not only_new:
        for area in areas.async_list_areas():
            key = f"area.{area.id}"
            if not all(lang in (config.get("names") or {}).get(key, {}) for lang in other_langs):
                targets.setdefault(key, (area.name, "room", ""))
    for state in fresh:
        if state.domain in NAME_DOMAINS and state.entity_id not in table:
            entry = registry.async_get(state.entity_id)
            area = areas.async_get_area(entry.area_id) if entry and entry.area_id else None
            targets.setdefault(state.entity_id, (state.name, f"{state.domain} device", area.name if area else ""))
    targets = dict(list(targets.items())[:MAX_NAME_TARGETS_PER_RUN])
    processed |= set(targets)
    if targets and other_langs:
        names = await async_propose_names(
            hass, ai_task_entity=ai_task, targets=targets, languages=other_langs,
            matcher=matcher, slot_lists=slot_lists, source_language=primary, context=context,
        )
        for target, per_lang in names.items():
            for lang, res in per_lang.items():
                if isinstance(res, dict):
                    errors.append(f"{target}/{lang}: {res.get('error')}")
                    continue
                good = [r["name"] for r in res if r["status"] == "ok"]
                if good:
                    items.append({
                        "op": "add_names", "payload": {"target": target, "lang": lang, "names": good},
                        "title": f"Namen für {targets[target][0]} ({lang}): {', '.join(good)}",
                        "origin": {"source": origin_source,
                                   "detail": f"für {targets[target][0]} gibt es noch keinen Namen auf {lang}"},
                        "check": {"status": "ok", "notes": []},
                    })

    if not only_new:
        items += classify_existing(config)
    # only what was really looked at counts as seen; the rest comes next time
    handled = processed | {s.entity_id for s in fresh if s.domain not in COMMAND_DOMAINS and s.domain not in NAME_DOMAINS}
    await seen_store.async_save({"entities": sorted(seen | handled)})
    return {"items": items, "errors": errors[:20]}
