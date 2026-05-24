"""Polyglot Assist conversation agent.

This is THE intercept point on the Voice-PE pipeline. When set as the pipeline's
conversation engine, Home Assistant routes every utterance here first. We:

1. Try to match against the per-language Hassil matcher.
2. On match: dispatch to the user's existing ``intent_script`` via
   ``intent.async_handle`` (with the matched language injected as the ``lang``
   slot so the user's Jinja templates can branch). Speech is built directly
   on the returned ``IntentResponse``.
3. On miss: proxy the **unmodified** ``user_input`` to the configured
   fallback agent (any HA conversation agent — skye-harris, ollama, anthropic,
   etc.).

We unconditionally declare ``ConversationEntityFeature.CONTROL`` so the
``_async_local_fallback_intent_filter`` in ``assist_pipeline/pipeline.py``
does not fire — we are the agent on the pipeline, not a local-fallback
companion to one.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.components import conversation
from homeassistant.components.conversation import (
    AbstractConversationAgent,
    ConversationEntity,
    ConversationEntityFeature,
    ConversationInput,
    ConversationResult,
)
from homeassistant.components.conversation.chat_log import ChatLog
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_FALLBACK_AGENT,
    DOMAIN,
    EVENT_MATCH,
    EVENT_MISS_PROXIED,
    SLOT_LANG,
)
from .matcher import MatchResult, MultilangMatcher

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Polyglot Assist conversation entity from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    matcher: MultilangMatcher = data["matcher"]
    fallback_agent_id: str = data[CONF_FALLBACK_AGENT]

    agent = PolyglotAssistAgent(
        hass=hass,
        entry=entry,
        matcher=matcher,
        fallback_agent_id=fallback_agent_id,
    )
    async_add_entities([agent])


class PolyglotAssistAgent(ConversationEntity, AbstractConversationAgent):
    """Multilingual Tier-1 conversation agent with LLM fallback proxy."""

    _attr_has_entity_name = True
    # CONTROL declared unconditionally — bypasses _async_local_fallback_intent_filter.
    # The Tier-1 + fallback chain handles control between them.
    _attr_supported_features = ConversationEntityFeature.CONTROL
    _attr_supports_streaming = False

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        matcher: MultilangMatcher,
        fallback_agent_id: str,
    ) -> None:
        self.hass = hass
        self._entry = entry
        self._matcher = matcher
        self._fallback_agent_id = fallback_agent_id

        self._attr_name = entry.title or "Polyglot Assist"
        self._attr_unique_id = entry.entry_id

    @property
    def supported_languages(self) -> list[str]:
        return self._matcher.languages

    # NOTE: we override _async_handle_message, NOT async_process. The base
    # ConversationEntity.async_process sets up the chat session + ChatLog
    # and delegates here. This is the contract every modern HA agent
    # (anthropic, openai, ollama, google, skye-harris, michelle-avery) uses.
    async def _async_handle_message(
        self,
        user_input: ConversationInput,
        chat_log: ChatLog,
    ) -> ConversationResult:
        t0 = time.perf_counter()

        # Hassil is sync-CPU — run in executor.
        match = await self.hass.async_add_executor_job(
            self._matcher.match,
            user_input.text,
            user_input.language,
        )

        if match is not None:
            t_ms = (time.perf_counter() - t0) * 1000.0
            _LOGGER.debug(
                "Polyglot match: %s (lang=%s) in %.1f ms — text=%r",
                match.intent_name, match.lang, t_ms, user_input.text,
            )
            self.hass.bus.async_fire(
                EVENT_MATCH,
                {
                    "intent": match.intent_name,
                    "lang": match.lang,
                    "input": user_input.text,
                    "took_ms": round(t_ms, 1),
                    "matched_sentence": match.matched_sentence,
                },
            )
            return await self._handle_match(match, user_input)

        # Miss → proxy to fallback agent.
        t_miss_ms = (time.perf_counter() - t0) * 1000.0
        return await self._proxy_to_fallback(user_input, t_miss_ms)

    async def _handle_match(
        self,
        match: MatchResult,
        user_input: ConversationInput,
    ) -> ConversationResult:
        """Dispatch the matched intent to HA's intent system."""
        # Inject the matched language as a slot so existing user-side
        # intent_script Jinja templates can branch on `lang`.
        slots: dict[str, dict[str, Any]] = dict(match.slots)
        slots[SLOT_LANG] = {"value": match.lang}

        try:
            response = await intent.async_handle(
                self.hass,
                DOMAIN,
                intent_type=match.intent_name,
                slots=slots,
                text_input=user_input.text,
                context=user_input.context,
                language=match.lang,
                conversation_agent_id=user_input.agent_id,
                device_id=user_input.device_id,
            )
        except intent.IntentHandleError as err:
            _LOGGER.error(
                "Polyglot match %s found but intent handler failed: %s",
                match.intent_name, err,
            )
            err_response = intent.IntentResponse(language=match.lang)
            err_response.async_set_speech(
                f"Intent {match.intent_name} fehlgeschlagen: {err}"
            )
            return ConversationResult(
                response=err_response,
                conversation_id=user_input.conversation_id,
            )
        except intent.UnknownIntent:
            _LOGGER.error(
                "Polyglot matched intent %s but no handler is registered "
                "(missing intent_script:?)",
                match.intent_name,
            )
            err_response = intent.IntentResponse(language=match.lang)
            err_response.async_set_speech(
                f"Kein Handler für Intent {match.intent_name} registriert."
            )
            return ConversationResult(
                response=err_response,
                conversation_id=user_input.conversation_id,
            )

        # IntentResponse is already populated by the handler (e.g. intent_script
        # set speech via its `speech.text` template). We just need to return it.
        # If the handler didn't set speech (rare), fall back to a generic OK.
        if not response.speech:
            response.async_set_speech("OK")

        return ConversationResult(
            response=response,
            conversation_id=user_input.conversation_id,
        )

    async def _proxy_to_fallback(
        self,
        user_input: ConversationInput,
        miss_took_ms: float,
    ) -> ConversationResult:
        """Forward the unmodified ConversationInput to the configured fallback."""
        fallback_agent = conversation.async_get_agent(
            self.hass, self._fallback_agent_id
        )

        self.hass.bus.async_fire(
            EVENT_MISS_PROXIED,
            {
                "input": user_input.text,
                "language": user_input.language,
                "fallback_agent": self._fallback_agent_id,
                "fallback_available": fallback_agent is not None,
                "match_took_ms": round(miss_took_ms, 1),
            },
        )

        if fallback_agent is None:
            _LOGGER.error(
                "Polyglot miss but fallback agent %r not available",
                self._fallback_agent_id,
            )
            err_response = intent.IntentResponse(language=user_input.language)
            err_response.async_set_speech(
                "Fallback-Agent ist nicht verfügbar."
            )
            return ConversationResult(
                response=err_response,
                conversation_id=user_input.conversation_id,
            )

        # IMPORTANT: call async_process, NOT _async_handle_message.
        # The base method sets up a fresh ChatLog session for the fallback.
        # Pass user_input UNCHANGED — preserves satellite_id, extra_system_prompt,
        # and any future ConversationInput fields HA adds.
        return await fallback_agent.async_process(user_input)
