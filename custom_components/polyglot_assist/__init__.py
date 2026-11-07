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
from homeassistant.exceptions import ConfigEntryError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import service as service_helper
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    CONF_FALLBACK_AGENT,
    CONF_BUILTIN,
    CONF_LANGUAGES_ENABLED,
    CONF_MONITOR,
    CONF_MONITOR_TIME,
    CONF_TRANSLATION_AI_TASK,
    DEFAULT_MONITOR_TIME,
    DEFAULT_TESTS_FILE,
    DOMAIN,
    SERVICE_EXPORT_YAML,
    SERVICE_IMPORT_YAML,
    SIGNAL_LOADED,
    SOURCE_CATALOG,
    SERVICE_RELOAD,
    SERVICE_RUN_TESTS,
    SERVICE_TEST_MATCH,
)
from .corpus import Case, parse_corpus, run_corpus
from .actions import (
    async_prepare_catalog_actions,
    async_register_catalog_actions,
    async_swap_catalog_actions,
    async_unregister_catalog_actions,
)
from .chatlog import clear_tool_cache
from .builtin import BuiltinIntents
from .dynlists import DynamicLists
from .monitor import Monitor
from .proposal_queue import ProposalQueue
from .loader import MatcherLoader
from . import websocket
from .source import IntentSource, SourceError
from .matcher import MultilangMatcher

_LOGGER = logging.getLogger(__name__)

ATTR_FILE = "file"
ATTR_LANGUAGE = "language"


class _CorpusError(Exception):
    """Corpus file problem (message is safe to return to the caller)."""


PLATFORMS: list[Platform] = [Platform.CONVERSATION, Platform.SENSOR]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Polyglot Assist domain (no YAML config, ConfigEntry only)."""
    hass.data.setdefault(DOMAIN, {})
    websocket.async_register(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Polyglot Assist from a config entry."""
    source = IntentSource(hass, entry)

    # Build matcher from the active source (YAML file or catalog)
    try:
        matcher = await MatcherLoader.load_data(
            hass, await source.async_load(), f"{source.kind}:{entry.title}",
            entry.options.get(CONF_LANGUAGES_ENABLED) or None,
        )
    except Exception as err:  # noqa: BLE001 — surface to the user with a fix
        _LOGGER.exception("Failed to load Polyglot intents (%s)", source.kind)
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"load_failed_{entry.entry_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="load_failed",
            translation_placeholders={"source": source.kind, "error": str(err)[:200]},
        )
        # Never fall back silently to the other source: that could re-enable
        # intents that were disabled in the catalog.
        raise ConfigEntryError(f"Polyglot intents could not be loaded ({source.kind}): {err}") from err
    ir.async_delete_issue(hass, DOMAIN, f"load_failed_{entry.entry_id}")

    lists = DynamicLists(hass, matcher.config)
    lists.async_start()
    actions = await async_register_catalog_actions(hass, matcher.config)
    hass.data[DOMAIN][entry.entry_id] = {
        "actions": actions,
        "matcher": matcher,
        "lists": lists,
        "source": source,
        "entry": entry,
        "fallback_agent": entry.data[CONF_FALLBACK_AGENT],
    }

    clear_tool_cache()
    if entry.options.get(CONF_BUILTIN, True):
        await async_setup_builtin(hass, hass.data[DOMAIN][entry.entry_id], matcher)
    if entry.options.get(CONF_MONITOR, False):
        monitor = Monitor(hass, entry.entry_id)
        await monitor.async_load()
        hass.data[DOMAIN][entry.entry_id]["monitor"] = monitor

        async def _daily(_now=None) -> None:
            data = hass.data[DOMAIN].get(entry.entry_id)
            if data is not None:
                await async_run_monitor(hass, data)

        monitor.async_schedule(entry.options.get(CONF_MONITOR_TIME, DEFAULT_MONITOR_TIME), _daily)
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        lists.async_stop()
        async_unregister_catalog_actions(hass, actions)
        hass.data[DOMAIN].pop(entry.entry_id, None)
        raise

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
                slot_lists = data["lists"].async_get()
                match = await hass.async_add_executor_job(
                    m.match, text, language_hint, slot_lists
                )
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
                slot_lists = data["lists"].async_get()

                def _match(
                    text: str,
                    lang_hint: str | None,
                    m: MultilangMatcher = m,
                    slot_lists: dict = slot_lists,
                ):
                    res = m.match(text, lang_hint, slot_lists)
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

    # Catalog migration (admin): YAML → catalog (verified), catalog → YAML export
    if not hass.services.has_service(DOMAIN, SERVICE_IMPORT_YAML):
        async def _import_yaml(call: ServiceCall) -> dict:
            out = {}
            for entry_id, data in list(hass.data[DOMAIN].items()):
                try:
                    imported = await data["source"].async_import_yaml(
                        overwrite=call.data.get("overwrite", False)
                    )
                except SourceError as err:
                    out[entry_id] = {"error": str(err)}
                    continue
                if data["source"].kind == SOURCE_CATALOG:
                    await async_reload_entry_matcher(hass, data)
                out[entry_id] = {"intents": len(imported.get("intents") or {})}
            if out and all("error" in v for v in out.values()):
                raise ServiceValidationError(next(iter(out.values()))["error"])
            return out

        async def _export_yaml(call: ServiceCall) -> dict:  # noqa: ARG001
            out = {}
            for entry_id, data in list(hass.data[DOMAIN].items()):
                try:
                    path = await data["source"].async_export_yaml()
                except SourceError as err:
                    out[entry_id] = {"error": str(err)}
                    continue
                out[entry_id] = {"file": str(path)}
            if out and all("error" in v for v in out.values()):
                raise ServiceValidationError(next(iter(out.values()))["error"])
            return out

        service_helper.async_register_admin_service(
            hass, DOMAIN, SERVICE_IMPORT_YAML, _import_yaml,
            vol.Schema({vol.Optional("overwrite", default=False): cv.boolean}),
            supports_response=SupportsResponse.OPTIONAL,
        )
        service_helper.async_register_admin_service(
            hass, DOMAIN, SERVICE_EXPORT_YAML, _export_yaml,
            supports_response=SupportsResponse.OPTIONAL,
        )

    return True


async def async_setup_builtin(hass: HomeAssistant, data: dict, matcher: MultilangMatcher) -> None:
    """Home Assistant's built-in commands in every enabled language."""
    builtin = BuiltinIntents(matcher.languages)
    await hass.async_add_executor_job(builtin.load)
    if not builtin.loaded:
        return
    # area/name lists (incl. the name table) for the built-in sentences
    lists = DynamicLists(
        hass,
        {"names": matcher.config.get("names") or {},
         "intents": {"_builtin": {"sentences": {"xx": ["{area} {name}"]}}}},
    )
    lists.async_start()
    if data.get("builtin_lists") is not None:
        data["builtin_lists"].async_stop()
    data["builtin"] = builtin
    data["builtin_lists"] = lists


async def async_reload_entry_matcher(hass: HomeAssistant, data: dict) -> bool:
    """Rebuild matcher + lists of one entry from its source. True on success."""
    entry = data["entry"]
    source: IntentSource = data["source"]
    generation = data["generation"] = data.get("generation", 0) + 1
    try:
        new_matcher = await MatcherLoader.load_data(
            hass, await source.async_load(), f"{source.kind}:{entry.title}",
            entry.options.get(CONF_LANGUAGES_ENABLED) or None,
        )
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Reload failed for %s — keeping previous matcher", entry.entry_id)
        return False
    if data.get("generation") != generation:
        return False  # a newer reload started meanwhile; it wins
    handlers = await async_prepare_catalog_actions(hass, new_matcher.config, data.get("actions", []))
    if data.get("generation") != generation:
        return False
    new_lists = DynamicLists(hass, new_matcher.config)
    new_lists.async_start()
    data["lists"].async_stop()
    data["actions"] = async_swap_catalog_actions(hass, data.get("actions", []), handlers)
    data["matcher"] = new_matcher
    if data.get("builtin") is not None:
        await async_setup_builtin(hass, data, new_matcher)
    data["lists"] = new_lists
    clear_tool_cache()
    async_dispatcher_send(hass, f"{SIGNAL_LOADED}_{entry.entry_id}")
    return True


async def _async_reload_all(hass: HomeAssistant) -> None:
    """Reload all Polyglot Assist config entries (rebuild matchers)."""
    _LOGGER.info("Reloading Polyglot Assist matchers")
    for data in list(hass.data[DOMAIN].values()):
        await async_reload_entry_matcher(hass, data)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id, None)
        if data is not None:
            data["lists"].async_stop()
            if data.get("builtin_lists") is not None:
                data["builtin_lists"].async_stop()
            async_unregister_catalog_actions(hass, data.get("actions", []))
            if data.get("monitor") is not None:
                data["monitor"].async_stop()
        # Last entry → unregister services
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_RELOAD)
            hass.services.async_remove(DOMAIN, SERVICE_TEST_MATCH)
            hass.services.async_remove(DOMAIN, SERVICE_RUN_TESTS)
            hass.services.async_remove(DOMAIN, SERVICE_IMPORT_YAML)
            hass.services.async_remove(DOMAIN, SERVICE_EXPORT_YAML)
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove catalog, proposals and monitoring log of a deleted entry (intents.yaml is kept)."""
    await IntentSource(hass, entry).async_remove_catalog()
    await ProposalQueue(hass, entry.entry_id).async_clear()
    await Monitor(hass, entry.entry_id).async_clear()


async def async_run_monitor(hass: HomeAssistant, data: dict) -> dict:
    """Evaluate the last 24 h and put the proposals into the queue."""
    from .monitor import build_monitor_items  # noqa: PLC0415
    from .translate import async_propose_from_monitor  # noqa: PLC0415

    entry = data["entry"]
    monitor: Monitor = data["monitor"]
    ai_task = entry.options.get(CONF_TRANSLATION_AI_TASK)
    matcher = data["matcher"]

    async def _propose(repeated: list) -> list:
        if not ai_task:
            return []
        slot_lists = {**matcher.static_slot_lists(), **data["lists"].async_get()}
        lines = await async_propose_from_monitor(
            hass, ai_task_entity=ai_task, repeated=repeated, config=matcher.config
        )
        return await hass.async_add_executor_job(
            build_monitor_items, repeated, lines, matcher.config,
            lambda t, lang: matcher.match_all(t, lang, slot_lists), slot_lists,
        )

    result = await monitor.async_evaluate(data, _propose)
    queue = data.get("queue") or ProposalQueue(hass, entry.entry_id)
    data["queue"] = queue
    await queue.async_add(result["items"])
    return result["summary"]
