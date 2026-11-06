"""Diagnostic sensor: how many intents exist, per language, and hit counters."""
from __future__ import annotations

from collections import Counter
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, EVENT_MATCH, EVENT_MISS_PROXIED, SIGNAL_LOADED
from .corpus import count_summary, validate


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([PolyglotIntentsSensor(hass, entry)])


class PolyglotIntentsSensor(SensorEntity):
    """State = active intents; attributes = counts, problems, hits since start."""

    _attr_has_entity_name = True
    _attr_translation_key = "intents"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:translate"
    # Hit counters change often and are only interesting live
    _unrecorded_attributes = frozenset({"hits", "hits_by_intent", "misses"})

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_intents"
        self._attr_name = "Intents"
        self._hits: Counter[str] = Counter()
        self._misses = 0
        self._summary: dict[str, Any] = {}

    @callback
    def _refresh(self) -> None:
        data = self.hass.data[DOMAIN].get(self._entry.entry_id)
        if data is None:
            return
        config = data["matcher"].config
        summary = count_summary(config)
        try:
            problems = validate(config, extra_lists=data["lists"].names)
        except Exception:  # noqa: BLE001
            problems = ["validation failed"]
        summary["problems"] = problems[:20]
        summary["problem_count"] = len(problems)
        summary["source"] = data["source"].kind
        self._summary = summary
        self._attr_native_value = summary["active"]

    async def async_added_to_hass(self) -> None:
        self._refresh()

        @callback
        def _loaded() -> None:
            self._refresh()
            self.async_write_ha_state()

        @callback
        def _hit(event: Event) -> None:
            self._hits[str(event.data.get("intent"))] += 1
            self.async_write_ha_state()

        @callback
        def _miss(event: Event) -> None:  # noqa: ARG001
            self._misses += 1
            self.async_write_ha_state()

        self.async_on_remove(
            async_dispatcher_connect(self.hass, f"{SIGNAL_LOADED}_{self._entry.entry_id}", _loaded)
        )
        self.async_on_remove(self.hass.bus.async_listen(EVENT_MATCH, _hit))
        self.async_on_remove(self.hass.bus.async_listen(EVENT_MISS_PROXIED, _miss))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            **self._summary,
            "hits": sum(self._hits.values()),
            "misses": self._misses,
            "hits_by_intent": dict(self._hits.most_common(15)),
        }
