"""Polyglot Assist — multi-language Tier-1 conversation agent for Home Assistant.

Architecture:
- ConversationEntity registered as a selectable agent on the Voice-PE pipeline
- Owns a per-language Hassil matcher (DE/EN/FR by default)
- On match: dispatch to existing intent_script.* entries via intent.async_handle
- On miss: proxy ConversationInput unchanged to a configured fallback agent
  (any HA conversation agent: skye-harris, anthropic, ollama, ...)

The integration is intentionally narrow — see ARCHITECTURE.md for the
scope-creep red lines.
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import service as service_helper

from .const import (
    CONF_FALLBACK_AGENT,
    CONF_INTENTS_FILE,
    DOMAIN,
    SERVICE_RELOAD,
    SERVICE_TEST_MATCH,
)
from .loader import MatcherLoader
from .matcher import MultilangMatcher

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.CONVERSATION]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Polyglot Assist domain (no YAML config, ConfigEntry only)."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Polyglot Assist from a config entry."""
    intents_file = entry.data[CONF_INTENTS_FILE]

    # Build matcher
    try:
        matcher = await MatcherLoader.load(hass, intents_file)
    except Exception:  # noqa: BLE001 — surface broadly to user
        _LOGGER.exception("Failed to load Polyglot intents from %s", intents_file)
        return False

    hass.data[DOMAIN][entry.entry_id] = {
        "matcher": matcher,
        "fallback_agent": entry.data[CONF_FALLBACK_AGENT],
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload service (no per-entry needed for v0.1 — one global service)
    if not hass.services.has_service(DOMAIN, SERVICE_RELOAD):
        async def _reload(call: ServiceCall) -> None:  # noqa: ARG001
            await _async_reload_all(hass)

        hass.services.async_register(DOMAIN, SERVICE_RELOAD, _reload)

    # Test-match service (debug)
    if not hass.services.has_service(DOMAIN, SERVICE_TEST_MATCH):
        async def _test_match(call: ServiceCall) -> dict:
            text = call.data["text"]
            language_hint = call.data.get("language")
            results = {}
            for entry_id, data in hass.data[DOMAIN].items():
                m: MultilangMatcher = data["matcher"]
                match = await hass.async_add_executor_job(m.match, text, language_hint)
                results[entry_id] = (
                    {"intent": match.intent_name, "lang": match.lang, "slots": match.slots}
                    if match else None
                )
            return results

        hass.services.async_register(
            DOMAIN, SERVICE_TEST_MATCH, _test_match, supports_response=True
        )

    return True


async def _async_reload_all(hass: HomeAssistant) -> None:
    """Reload all Polyglot Assist config entries (rebuild matchers)."""
    _LOGGER.info("Reloading Polyglot Assist matchers")
    for entry_id, data in list(hass.data[DOMAIN].items()):
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            continue
        try:
            new_matcher = await MatcherLoader.load(hass, entry.data[CONF_INTENTS_FILE])
            data["matcher"] = new_matcher
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Reload failed for %s — keeping previous matcher", entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        # Last entry → unregister services
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_RELOAD)
            hass.services.async_remove(DOMAIN, SERVICE_TEST_MATCH)
    return unload_ok
