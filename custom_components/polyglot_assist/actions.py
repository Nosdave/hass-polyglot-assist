"""Commands whose action lives in the catalog (no intent_script needed).

A catalog intent may carry its own action and spoken answers::

    KitchenCoffeeOn:
      category: Kitchen
      action:                         # Home Assistant action syntax (script)
        - action: switch.turn_on
          target: {entity_id: switch.coffee}
      speech:                         # per language; templates see slots,
        de: "Kaffeemaschine ist an."  # lang, utterance, action_response
        en: "Coffee machine is on."
        fr: "La machine à café est allumée."

Polyglot registers a handler for it that only Polyglot can call (it is not
offered to LLMs). An intent that already has a handler (e.g. an existing
``intent_script``) keeps it — the catalog action is then ignored with a
warning, so nothing changes for existing setups.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import intent
from homeassistant.helpers.script import Script, async_validate_actions_config
from homeassistant.helpers.template import Template

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

DEFAULT_SPEECH = {"de": "Erledigt.", "en": "Done.", "fr": "C'est fait.", "nl": "Gedaan.",
                  "it": "Fatto.", "es": "Hecho."}


class CatalogIntentHandler(intent.IntentHandler):
    """Runs the catalog action of one intent and answers in the hit language."""

    def __init__(
        self, hass: HomeAssistant, intent_type: str, script: Script, speech: dict[str, str], owner: str
    ) -> None:
        self.intent_type = intent_type
        self.owner = owner  # config entry id
        self.platforms = {DOMAIN}
        self.description = f"Polyglot catalog command {intent_type}"
        self._script = script
        self._speech = {lang: Template(text, hass) for lang, text in speech.items() if isinstance(text, str)}

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        variables: dict[str, Any] = {name: slot.get("value") for name, slot in intent_obj.slots.items()}
        lang = variables.get("lang") or intent_obj.language
        result = await self._script.async_run(variables, intent_obj.context)
        if result is not None and result.service_response is not None:
            variables["action_response"] = result.service_response
        response = intent_obj.create_response()
        template = self._speech.get(lang)
        text = template.async_render(variables, parse_result=False) if template else DEFAULT_SPEECH.get(lang, "OK")
        response.async_set_speech(text)
        return response


async def async_prepare_catalog_actions(
    hass: HomeAssistant, config: dict[str, Any], owner: str
) -> list[CatalogIntentHandler]:
    """Validate every catalog action (async) without registering anything."""
    # names taken by anything that is not OUR catalog handler (intent_scripts,
    # built-ins, other Polyglot entries)
    taken = {
        h.intent_type for h in intent.async_get(hass)
        if not (isinstance(h, CatalogIntentHandler) and h.owner == owner)
    }
    handlers: list[CatalogIntentHandler] = []
    for name, cfg in (config.get("intents") or {}).items():
        if not isinstance(cfg, dict) or not cfg.get("action"):
            continue
        if name in taken:
            _LOGGER.warning(
                "Polyglot Assist: %s already has a handler (e.g. intent_script) — catalog action ignored",
                name,
            )
            continue
        try:
            actions = await async_validate_actions_config(hass, cv.SCRIPT_SCHEMA(cfg["action"]))
            script = Script(hass, actions, f"Polyglot {name}", DOMAIN, script_mode="parallel")
        except Exception as err:  # noqa: BLE001 — one broken action must not break the rest
            _LOGGER.error("Polyglot Assist: action of %s is invalid: %s", name, err)
            continue
        handlers.append(CatalogIntentHandler(hass, name, script, cfg.get("speech") or {}, owner))
    return handlers


def async_swap_catalog_actions(
    hass: HomeAssistant, old: list[str], handlers: list[CatalogIntentHandler], owner: str
) -> list[str]:
    """Replace the previous handlers in one synchronous step (no gap)."""
    new_names = {h.intent_type for h in handlers}
    async_unregister_catalog_actions(hass, [n for n in old if n not in new_names], owner)
    registered: list[str] = []
    for handler in handlers:
        current = next((h for h in intent.async_get(hass) if h.intent_type == handler.intent_type), None)
        if current is not None and not (isinstance(current, CatalogIntentHandler) and current.owner == owner):
            continue  # an intent_script or another entry registered meanwhile: it wins
        if current is not None:
            intent.async_remove(hass, handler.intent_type)  # avoid the overwrite warning
        intent.async_register(hass, handler)
        registered.append(handler.intent_type)
    return sorted(registered)


async def async_register_catalog_actions(
    hass: HomeAssistant, config: dict[str, Any], owner: str
) -> list[str]:
    """Register handlers for intents with an ``action``. Returns registered names."""
    return async_swap_catalog_actions(hass, [], await async_prepare_catalog_actions(hass, config, owner), owner)


def async_unregister_catalog_actions(hass: HomeAssistant, names: list[str], owner: str) -> None:
    """Remove only handlers this entry owns."""
    for name in names:
        handler = next((h for h in intent.async_get(hass) if h.intent_type == name), None)
        if isinstance(handler, CatalogIntentHandler) and handler.owner == owner:
            intent.async_remove(hass, name)
