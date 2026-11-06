"""Runtime slot lists: areas, exposed entity names and declarative lists.

Built-in lists (unless ``intents.yaml`` defines a list of the same name):

* ``area`` – every area name and alias, value = area name (same value the
  Home Assistant default agent uses, so existing intent_scripts keep working).
* ``name`` – every entity exposed to Assist, name and aliases,
  value = name, context ``domain`` and ``entity_id``.

Declarative lists in ``intents.yaml``::

    lists:
      lamps:
        entities: {domain: light, area: [kitchen], exposed: true, exclude: [...]}
      rooms:
        areas: {}                # value = area_id
      kids:
        template: "{{ ['Anna', 'Ben'] }}"   # strings or {in, out} mappings

Lists are built on the event loop (registry access) and cached. The cache is
invalidated by entity/area registry updates and expose changes and expires
after ``STATIC_TTL``; template lists expire after ``TEMPLATE_TTL`` seconds.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from hassil.intents import SlotList, TextSlotList

from homeassistant.components.homeassistant.exposed_entities import (
    async_listen_entity_updates,
    async_should_expose,
)
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.template import Template

from .corpus import BUILTIN_LISTS, DYNAMIC_KEYS, dynamic_lists, static_lists

_LOGGER = logging.getLogger(__name__)

ASSISTANT = "conversation"
TEMPLATE_TTL = 60.0
# names can also change via state attributes (no registry event) → refresh anyway
STATIC_TTL = 300.0


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple, set)) else [value]


class DynamicLists:
    """Builds and caches the slot lists that change at runtime."""

    def __init__(self, hass: HomeAssistant, data: dict[str, Any]) -> None:
        self.hass = hass
        static = set(static_lists(data))
        self._builtins = [b for b in BUILTIN_LISTS if b not in static]
        self._specs = dynamic_lists(data)
        self._cache: dict[str, SlotList] | None = None
        self._cache_built = 0.0
        self._template_cache: dict[str, SlotList] = {}
        self._template_built = 0.0
        self._unsubs: list[CALLBACK_TYPE] = []

    @property
    def names(self) -> set[str]:
        """Every list name supplied at runtime (for validation)."""
        return set(self._builtins) | set(self._specs)

    @callback
    def async_start(self) -> None:
        """Listen for changes that invalidate the cache."""

        @callback
        def _invalidate(_event: Event | None = None) -> None:
            self._cache = None

        self._unsubs = [
            self.hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _invalidate),
            self.hass.bus.async_listen(ar.EVENT_AREA_REGISTRY_UPDATED, _invalidate),
            async_listen_entity_updates(self.hass, ASSISTANT, _invalidate),
        ]

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []

    @callback
    def async_get(self) -> dict[str, SlotList]:
        """Return the current slot lists (must run on the event loop)."""
        if self._cache is None or time.monotonic() - self._cache_built > STATIC_TTL:
            self._cache = self._build_static()
            self._cache_built = time.monotonic()
        if self._has_templates() and time.monotonic() - self._template_built > TEMPLATE_TTL:
            self._template_cache = self._build_templates()
            self._template_built = time.monotonic()
        return {**self._cache, **self._template_cache}

    # ── builders ────────────────────────────────────────────────────────────

    def _has_templates(self) -> bool:
        return any("template" in spec for spec in self._specs.values())

    @callback
    def _build_static(self) -> dict[str, SlotList]:
        out: dict[str, SlotList] = {}
        try:
            if "area" in self._builtins:
                out["area"] = self._area_list(value="name", name="area")
            if "name" in self._builtins:
                out["name"] = self._name_list()
            for list_name, spec in self._specs.items():
                if "entities" in spec:
                    out[list_name] = self._entity_list(list_name, spec["entities"] or {})
                elif "areas" in spec:
                    out[list_name] = self._area_list(value="id", name=list_name)
        except Exception:  # noqa: BLE001 — never break matching
            _LOGGER.exception("Polyglot Assist: building runtime lists failed")
        return out

    @callback
    def _area_list(self, value: str, name: str) -> TextSlotList:
        tuples: list[tuple[str, Any, dict[str, Any]]] = []
        for area in ar.async_get(self.hass).async_list_areas():
            out = area.name if value == "name" else area.id
            for text in [area.name, *(area.aliases or [])]:
                if text and text.strip():
                    tuples.append((text.strip(), out, {"area_id": area.id}))
        return TextSlotList.from_tuples(tuples, allow_template=False, name=name)

    def _entity_names(self, entity_id: str, entry: er.RegistryEntry | None) -> list[str]:
        names: list[str] = []
        state = self.hass.states.get(entity_id)
        if state is not None and state.name:
            names.append(state.name)
        if entry is not None:
            names.extend(a for a in (entry.aliases or []) if isinstance(a, str))
        return [n.strip() for n in dict.fromkeys(names) if n and n.strip()]

    @callback
    def _name_list(self) -> TextSlotList:
        registry = er.async_get(self.hass)
        tuples: list[tuple[str, Any, dict[str, Any]]] = []
        for state in self.hass.states.async_all():
            if not async_should_expose(self.hass, ASSISTANT, state.entity_id):
                continue
            entry = registry.async_get(state.entity_id)
            primary = state.name or state.entity_id
            ctx = {"domain": state.domain, "entity_id": state.entity_id}
            for text in self._entity_names(state.entity_id, entry):
                tuples.append((text, primary, ctx))
        return TextSlotList.from_tuples(tuples, allow_template=False, name="name")

    @callback
    def _entity_list(self, list_name: str, spec: dict[str, Any]) -> TextSlotList:
        registry = er.async_get(self.hass)
        domains = set(_as_list(spec.get("domain")))
        areas = set(_as_list(spec.get("area")))
        exclude = set(_as_list(spec.get("exclude")))
        exposed_only = spec.get("exposed", True)
        tuples: list[tuple[str, Any, dict[str, Any]]] = []
        for state in self.hass.states.async_all():
            eid = state.entity_id
            if eid in exclude or (domains and state.domain not in domains):
                continue
            if exposed_only and not async_should_expose(self.hass, ASSISTANT, eid):
                continue
            entry = registry.async_get(eid)
            if areas:
                area_id = entry.area_id if entry else None
                if area_id is None and entry is not None and entry.device_id:
                    device = dr.async_get(self.hass).async_get(entry.device_id)
                    area_id = device.area_id if device else None
                if area_id not in areas:
                    continue
            for text in self._entity_names(eid, entry):
                tuples.append((text, eid, {"domain": state.domain}))
        return TextSlotList.from_tuples(tuples, allow_template=False, name=list_name)

    @callback
    def _build_templates(self) -> dict[str, SlotList]:
        out: dict[str, SlotList] = {}
        for list_name, spec in self._specs.items():
            if "template" not in spec:
                continue
            try:
                result = Template(str(spec["template"]), self.hass).async_render(
                    parse_result=True
                )
                tuples: list[tuple[str, Any]] = []
                for item in _as_list(result):
                    if isinstance(item, dict) and "in" in item:
                        tuples.append((str(item["in"]), item.get("out", item["in"])))
                    elif item is not None and str(item).strip():
                        tuples.append((str(item).strip(), str(item).strip()))
                out[list_name] = TextSlotList.from_tuples(
                    tuples, allow_template=False, name=list_name
                )
            except Exception:  # noqa: BLE001 — a broken template must not break matching
                _LOGGER.exception("Polyglot Assist: template list %s failed", list_name)
        return out


__all__ = ["DYNAMIC_KEYS", "DynamicLists"]
