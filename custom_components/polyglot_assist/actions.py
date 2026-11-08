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

from .speech import say
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)



class CatalogIntentHandler(intent.IntentHandler):
    """Runs the catalog action of one intent and answers in the hit language."""

    def __init__(
        self, hass: HomeAssistant, intent_type: str, script: Script | None, speech: dict[str, str], owner: str,
        kind: str | None = None, action_cfg: Any = None,
    ) -> None:
        self.intent_type = intent_type
        self.owner = owner  # config entry id
        self.platforms = {DOMAIN}
        self.description = f"Polyglot catalog command {intent_type}"
        self._script = script
        self._kind = kind
        self._switched = _plain_targets(action_cfg) if action_cfg is not None else []
        # "*" = one template for every language (taken over from YAML: it branches on `lang` itself)
        self._speech = {lang: Template(text, hass) for lang, text in speech.items() if isinstance(text, str)}

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        from homeassistant.exceptions import TemplateError  # noqa: PLC0415

        variables: dict[str, Any] = {name: slot.get("value") for name, slot in intent_obj.slots.items()}
        lang = variables.get("lang") or intent_obj.language
        response = intent_obj.create_response()
        if self._script is not None:
            result = await self._script.async_run(variables, intent_obj.context)
            if result is not None and result.service_response is not None:
                variables["action_response"] = result.service_response
            # what was switched (monitor, ChatLog): only literal targets of plain service steps —
            # conditions, branches and templates are not reported (they may not have run)
            if self._switched:
                response.async_set_results(success_results=[
                    intent.IntentResponseTarget(type=intent.IntentResponseTargetType.ENTITY, name=e, id=e)
                    for e in self._switched])
        if self._kind == "question":
            response.response_type = intent.IntentResponseType.QUERY_ANSWER  # spoken, never a tone
        template = self._speech.get(lang) or self._speech.get((lang or "").split("-")[0]) or self._speech.get("*")
        try:
            text = template.async_render(variables, parse_result=False) if template else say("done", lang)
        except TemplateError as err:
            # the action (if any) HAS run — say so instead of "failed"
            _LOGGER.warning("Polyglot Assist: answer of %s could not be rendered: %s", self.intent_type, err)
            text = say("done", lang)
        response.async_set_speech(" ".join(str(text).split()))
        return response


def _plain_targets(action: Any) -> list[str]:
    """Entity ids of an action made ONLY of plain service steps with literal targets, else []."""
    from .proposals import iter_step_entities  # noqa: PLC0415

    steps = action if isinstance(action, list) else [action]
    out: list[str] = []
    for step in steps:
        if not isinstance(step, dict) or set(step) - {"action", "service", "target", "data", "metadata"}:
            return []
        entities = iter_step_entities({"target": step.get("target")}) if step.get("target") else None
        if not entities or any("{" in e for e in entities):
            return []
        out += [e for e in entities if e not in out]
    return out[:10]


async def async_prepare_owned(hass: HomeAssistant, config: dict[str, Any], owner: str) -> dict[str, CatalogIntentHandler]:
    """Handlers for commands taken over from YAML (``owned``): called by Polyglot DIRECTLY.

    They are not registered (the intent_script keeps its name in HA's registry
    and stays the fallback for the LLM tool); Polyglot's matcher calls them.
    """
    out: dict[str, CatalogIntentHandler] = {}
    for name, cfg in (config.get("intents") or {}).items():
        if not isinstance(cfg, dict) or not cfg.get("owned"):
            continue
        script = None
        if cfg.get("action"):
            try:
                actions = await async_validate_actions_config(hass, cv.SCRIPT_SCHEMA(cfg["action"]))
                # the mode of the YAML script it came from (single, queued, restart …) is kept
                script = Script(hass, actions, f"Polyglot {name}", DOMAIN,
                                script_mode=cfg.get("mode") or "parallel")
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Polyglot Assist: action of %s (taken over) is invalid: %s", name, err)
                continue  # never run half a command — the YAML handler stays in charge
        out[name] = CatalogIntentHandler(hass, name, script, cfg.get("speech") or {}, owner, cfg.get("kind"), cfg.get("action"))
    return out


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
        if not isinstance(cfg, dict) or cfg.get("owned") or not (cfg.get("action") or cfg.get("speech")):
            continue  # owned commands are called directly (async_prepare_owned); nothing to run → none
        if name in taken:
            _LOGGER.warning(
                "Polyglot Assist: %s already has a handler (e.g. intent_script) — catalog action ignored",
                name,
            )
            continue
        script = None
        if cfg.get("action"):  # a command may also only answer (speech, no action)
            try:
                actions = await async_validate_actions_config(hass, cv.SCRIPT_SCHEMA(cfg["action"]))
                script = Script(hass, actions, f"Polyglot {name}", DOMAIN, script_mode="parallel")
            except Exception as err:  # noqa: BLE001 — one broken action must not break the rest
                _LOGGER.error("Polyglot Assist: action of %s is invalid: %s", name, err)
                continue
        handlers.append(CatalogIntentHandler(hass, name, script, cfg.get("speech") or {}, owner, cfg.get("kind"), cfg.get("action")))
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
