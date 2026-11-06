"""Runtime slot lists: areas, exposed entity names and declarative lists.

Built-in lists (unless ``intents.yaml`` defines a list of the same name):

* ``area`` – every area name and alias, value = area name (so existing
  intent_scripts that compare ``area_name(...)`` keep working; note that the
  Home Assistant default agent returns the alias itself as value).
* ``name`` – every entity exposed to Assist with the names Home Assistant
  uses for Assist (aliases incl. the computed name), value = entity name,
  context ``domain`` and ``entity_id``.

Declarative lists in ``intents.yaml``::

    lists:
      lamps:
        entities: {domain: light, area: [kitchen], exposed: true, exclude: [...]}
      rooms:
        areas: {}                # value = area_id
      kids:
        template: "{{ ['Anna', 'Ben'] }}"   # strings or {in, out} mappings

Only lists that sentences reference are built. Every referenced list always
exists (empty if it cannot be built, the last good version if a rebuild
fails), because hassil raises on a missing list while matching and that
would silently disable the whole language.

Requests never wait for a rebuild except the very first one: changes
(registries, expose settings, added/removed entities) mark the cache dirty
and a debounced rebuild runs in the background; templates are rendered on a
timer.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from hassil.intents import SlotList, TextSlotList
from hassil.util import remove_punctuation

from homeassistant.components.homeassistant.exposed_entities import (
    async_listen_entity_updates,
    async_should_expose,
)
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.template import Template

from .corpus import (
    BUILTIN_LISTS,
    dynamic_lists,
    names_table,
    referenced_lists,
    static_lists,
)

_LOGGER = logging.getLogger(__name__)

ASSISTANT = "conversation"
TEMPLATE_INTERVAL = timedelta(seconds=60)
TEMPLATE_TIMEOUT = 3.0
REBUILD_DELAY = 2.0
_ENTITY_FIELDS = ("aliases", "area_id", "device_id", "entity_id", "name", "original_name")
_DEVICE_FIELDS = ("area_id", "name", "name_by_user")


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _text_in(text: str) -> str:
    return remove_punctuation(text).strip()


def _slot_list(name: str, tuples: list[tuple[Any, ...]]) -> TextSlotList:
    sl = TextSlotList.from_tuples(tuples, allow_template=False, name=name)
    # hassil builds its lookup index lazily on first use; do it here on the
    # event loop so concurrent matches in executor threads only read.
    try:
        sl.get_candidates("")
    except Exception:  # noqa: BLE001
        pass
    return sl


class DynamicLists:
    """Builds and caches the slot lists that change at runtime."""

    def __init__(self, hass: HomeAssistant, data: dict[str, Any]) -> None:
        self.hass = hass
        static = set(static_lists(data))
        used = referenced_lists(data)
        self._builtins = [b for b in BUILTIN_LISTS if b not in static and b in used]
        self._specs = {k: v for k, v in dynamic_lists(data).items() if k in used}
        self._declared = set(dynamic_lists(data)) | {b for b in BUILTIN_LISTS if b not in static}
        self._names = names_table(data)
        self._cache: dict[str, SlotList] | None = None
        self._templates: dict[str, SlotList] = {}
        self._dirty = False
        self._unsubs: list[CALLBACK_TYPE] = []
        self._pending: CALLBACK_TYPE | None = None

    @property
    def names(self) -> set[str]:
        """Every list name supplied at runtime (for validation)."""
        return set(self._declared)

    # ── lifecycle ───────────────────────────────────────────────────────

    @callback
    def async_start(self) -> None:
        """Listen for changes that invalidate the cache."""
        if not self._builtins and not self._specs:
            return

        @callback
        def _entity_filter(event_data: er.EventEntityRegistryUpdatedData) -> bool:
            return event_data["action"] != "update" or any(
                f in event_data.get("changes", {}) for f in _ENTITY_FIELDS
            )

        @callback
        def _device_filter(event_data: dr.EventDeviceRegistryUpdatedData) -> bool:
            return event_data["action"] != "update" or any(
                f in event_data.get("changes", {}) for f in _DEVICE_FIELDS
            )

        @callback
        def _state_filter(event_data: dict[str, Any]) -> bool:
            # added or removed entities only (names come from the registry)
            return not event_data.get("old_state") or not event_data.get("new_state")

        self._unsubs = [
            self.hass.bus.async_listen(ar.EVENT_AREA_REGISTRY_UPDATED, self._invalidate),
            self.hass.bus.async_listen(
                er.EVENT_ENTITY_REGISTRY_UPDATED, self._invalidate, event_filter=_entity_filter
            ),
            self.hass.bus.async_listen(
                dr.EVENT_DEVICE_REGISTRY_UPDATED, self._invalidate, event_filter=_device_filter
            ),
            self.hass.bus.async_listen(
                EVENT_STATE_CHANGED, self._invalidate, event_filter=_state_filter
            ),
            async_listen_entity_updates(self.hass, ASSISTANT, self._invalidate),
        ]
        if any("template" in spec for spec in self._specs.values()):
            self._unsubs.append(
                async_track_time_interval(self.hass, self._async_render_templates, TEMPLATE_INTERVAL)
            )
            self.hass.async_create_task(self._async_render_templates(), eager_start=True)

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []
        if self._pending is not None:
            self._pending()
            self._pending = None

    @callback
    def _invalidate(self, _event: Event | None = None) -> None:
        self._dirty = True
        if self._pending is None:
            self._pending = async_call_later(self.hass, REBUILD_DELAY, self._rebuild_later)

    @callback
    def _rebuild_later(self, _now: Any) -> None:
        self._pending = None
        if self._dirty:
            self._cache = self._build_static(self._cache or {})
            self._dirty = False

    @callback
    def async_get(self) -> dict[str, SlotList]:
        """Current slot lists (event loop). Builds synchronously only once."""
        if self._cache is None:
            self._cache = self._build_static({})
            self._dirty = False
        out = {**self._cache, **self._templates}
        # Every runtime list name must exist (also ones only referenced from
        # inside static list values): a missing list kills the language.
        for name in self._declared:
            if name not in out:  # not referenced, template not rendered yet / failed
                out[name] = _slot_list(name, [])
        return out

    # ── builders ────────────────────────────────────────────────────────

    @callback
    def _build_static(self, previous: dict[str, SlotList]) -> dict[str, SlotList]:
        out: dict[str, SlotList] = {}
        builders: dict[str, Any] = {}
        if "area" in self._builtins:
            builders["area"] = lambda: self._area_list("name", "area")
        if "name" in self._builtins:
            builders["name"] = self._name_list
        for list_name, spec in self._specs.items():
            if "entities" in spec:
                builders[list_name] = lambda n=list_name, s=spec: self._entity_list(n, s["entities"] or {})
            elif "areas" in spec:
                builders[list_name] = lambda n=list_name: self._area_list("id", n)
        for list_name, build in builders.items():
            try:
                out[list_name] = build()
            except Exception:  # noqa: BLE001 — keep matching alive
                _LOGGER.exception("Polyglot Assist: building list %s failed", list_name)
                out[list_name] = previous.get(list_name) or _slot_list(list_name, [])
        return out

    @callback
    def _area_list(self, value: str, name: str) -> TextSlotList:
        tuples: list[tuple[str, Any, dict[str, Any]]] = []
        for area in ar.async_get(self.hass).async_list_areas():
            out = area.name if value == "name" else area.id
            extra = self._names.get(f"area.{area.id}", [])
            for text in dict.fromkeys([area.name, *(area.aliases or []), *extra]):
                if isinstance(text, str) and _text_in(text):
                    tuples.append((_text_in(text), out, {"area_id": area.id}))
        return _slot_list(name, tuples)

    def _entity_names(self, state: Any, entry: er.RegistryEntry | None) -> list[str]:
        try:
            names = intent.async_get_entity_aliases(self.hass, entry, state=state)
        except Exception:  # noqa: BLE001 — older cores
            names = [state.name, *((entry.aliases or []) if entry else [])]
        names = [*names, *self._names.get(state.entity_id, [])]
        return [t for t in dict.fromkeys(_text_in(n) for n in names if isinstance(n, str)) if t]

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
            for text in self._entity_names(state, entry):
                tuples.append((text, primary, ctx))
        return _slot_list("name", tuples)

    @callback
    def _entity_list(self, list_name: str, spec: dict[str, Any]) -> TextSlotList:
        registry = er.async_get(self.hass)
        devices = dr.async_get(self.hass)
        domains = {str(d) for d in _as_list(spec.get("domain"))}
        areas = {str(a) for a in _as_list(spec.get("area"))}
        exclude = {str(e) for e in _as_list(spec.get("exclude"))}
        exposed_only = bool(spec.get("exposed", True))
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
                    device = devices.async_get(entry.device_id)
                    area_id = device.area_id if device else None
                if area_id not in areas:
                    continue
            for text in self._entity_names(state, entry):
                tuples.append((text, eid, {"domain": state.domain}))
        return _slot_list(list_name, tuples)

    async def _async_render_templates(self, _now: Any = None) -> None:
        for list_name, spec in self._specs.items():
            if "template" not in spec:
                continue
            try:
                tpl = Template(str(spec["template"]), self.hass)
                if await tpl.async_render_will_timeout(TEMPLATE_TIMEOUT):
                    _LOGGER.error(
                        "Polyglot Assist: template list %s takes longer than %ss — skipped",
                        list_name, TEMPLATE_TIMEOUT,
                    )
                    continue
                result = tpl.async_render(parse_result=True)
                tuples: list[tuple[str, Any]] = []
                for item in _as_list(result):
                    if isinstance(item, dict):
                        if "in" not in item or not _text_in(str(item["in"])):
                            continue
                        tuples.append((_text_in(str(item["in"])), item.get("out", str(item["in"]).strip())))
                    elif item is not None and _text_in(str(item)):
                        tuples.append((_text_in(str(item)), str(item).strip()))
                self._templates[list_name] = _slot_list(list_name, tuples)
            except Exception:  # noqa: BLE001 — keep the last good version
                _LOGGER.exception("Polyglot Assist: template list %s failed", list_name)
