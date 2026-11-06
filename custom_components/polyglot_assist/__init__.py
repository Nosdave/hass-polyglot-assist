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
from pathlib import Path

import voluptuous as vol
import yaml

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import service as service_helper

from .const import (
    CONF_FALLBACK_AGENT,
    CONF_INTENTS_FILE,
    DEFAULT_TESTS_FILE,
    DOMAIN,
    SERVICE_RELOAD,
    SERVICE_RUN_TESTS,
    SERVICE_TEST_MATCH,
)
from .corpus import Case, parse_corpus, run_corpus
from .loader import MatcherLoader
from .matcher import MultilangMatcher

_LOGGER = logging.getLogger(__name__)

ATTR_FILE = "file"
ATTR_LANGUAGE = "language"


class _CorpusError(Exception):
    """Corpus file problem (message is safe to return to the caller)."""


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
            for entry_id, data in list(hass.data[DOMAIN].items()):
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

    # Regression corpus (no intent is dispatched — matching only).
    # Admin-only, and only YAML files below <config>/polyglot_assist/ — error
    # messages never echo file content.
    if not hass.services.has_service(DOMAIN, SERVICE_RUN_TESTS):
        async def _run_tests(call: ServiceCall) -> dict:
            base = Path(hass.config.path(DOMAIN))
            rel = call.data.get(ATTR_FILE) or DEFAULT_TESTS_FILE

            def _load() -> list[Case]:
                path = Path(rel)
                if not path.is_absolute():
                    path = Path(hass.config.path(rel))
                resolved = path.resolve()
                if base.resolve() not in resolved.parents or resolved.suffix not in (
                    ".yaml",
                    ".yml",
                ):
                    raise _CorpusError(
                        f"corpus must be a .yaml file inside {base}: {rel}"
                    )
                if not resolved.is_file():
                    raise _CorpusError(f"corpus file not found: {rel}")
                try:
                    raw = yaml.safe_load(resolved.read_text("utf-8"))
                except UnicodeDecodeError:
                    raise _CorpusError("corpus is not UTF-8") from None
                except yaml.YAMLError as err:
                    mark = getattr(err, "problem_mark", None)
                    where = f" (line {mark.line + 1})" if mark else ""
                    raise _CorpusError(f"corpus is not valid YAML{where}") from None
                try:
                    return parse_corpus(raw or {})
                except ValueError as err:  # messages carry indexes only
                    raise _CorpusError(f"invalid corpus: {err}") from None

            try:
                cases = await hass.async_add_executor_job(_load)
            except _CorpusError as err:
                raise ServiceValidationError(str(err)) from None

            hint = call.data.get(ATTR_LANGUAGE)
            results = {}
            for entry_id, data in list(hass.data[DOMAIN].items()):
                m: MultilangMatcher = data["matcher"]

                def _match(text: str, lang_hint: str | None, m: MultilangMatcher = m):
                    res = m.match(text, lang_hint)
                    if res is None:
                        return None
                    return (
                        res.intent_name,
                        res.lang,
                        {k: v.get("value") for k, v in res.slots.items()},
                    )

                results[entry_id] = await hass.async_add_executor_job(
                    run_corpus, _match, cases, hint
                )
            return results

        service_helper.async_register_admin_service(
            hass,
            DOMAIN,
            SERVICE_RUN_TESTS,
            _run_tests,
            vol.Schema(
                {
                    vol.Optional(ATTR_FILE): cv.string,
                    vol.Optional(ATTR_LANGUAGE): cv.string,
                }
            ),
            supports_response=SupportsResponse.ONLY,
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
            hass.services.async_remove(DOMAIN, SERVICE_RUN_TESTS)
    return unload_ok
