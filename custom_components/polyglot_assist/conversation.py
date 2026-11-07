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

import dataclasses
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

from .chatlog import async_record_hit
from .const import (
    CHIME_MARKER,
    CONF_RESPONSE_MODE,
    RESPONSE_CHIME,
    RESPONSE_TEXT,
    DEFAULT_LANGUAGES,
    CONF_CHAT_LOG_TOOLS,
    CONF_FALLBACK_AGENT,
    CONF_LANGUAGE_HINT,
    DOMAIN,
    EVENT_MATCH,
    EVENT_MISS_PROXIED,
    SLOT_LANG,
    SLOT_UTTERANCE,
)
from .builtin import async_handle_builtin, is_blocked
from .langguess import HINT_MARKER, build_extra_prompt, guess_language
from .matcher import MatchResult, MultilangMatcher

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Polyglot Assist conversation entity from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    fallback_agent_id: str = data[CONF_FALLBACK_AGENT]

    agent = PolyglotAssistAgent(
        hass=hass,
        entry=entry,
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
        fallback_agent_id: str,
    ) -> None:
        self.hass = hass
        self._entry = entry
        self._fallback_agent_id = fallback_agent_id

        self._attr_name = entry.title or "Polyglot Assist"
        self._attr_unique_id = entry.entry_id

    def _runtime(self) -> dict[str, Any] | None:
        # Read once per request: the reload service swaps matcher and lists,
        # and an options change unloads the entry while requests may run.
        return self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)

    @property
    def supported_languages(self) -> list[str]:
        runtime = self._runtime()
        return runtime["matcher"].languages if runtime else list(DEFAULT_LANGUAGES)

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
        runtime = self._runtime()
        if runtime is None:  # entry is (re)loading — never drop the request
            return await self._proxy_to_fallback(user_input, 0.0, chat_log, [])
        matcher: MultilangMatcher = runtime["matcher"]
        slot_lists = runtime["lists"].async_get()
        match = await self.hass.async_add_executor_job(
            matcher.match,
            user_input.text,
            user_input.language,
            slot_lists,
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
                    "entry_id": self._entry.entry_id,
                    "intent": match.intent_name,
                    "lang": match.lang,
                    "input": user_input.text,
                    "took_ms": round(t_ms, 1),
                    "matched_sentence": match.matched_sentence,
                },
            )
            result = await self._handle_match(match, user_input, chat_log, matcher.config)
            hits = runtime.setdefault("hits", {})
            hits[match.intent_name] = hits.get(match.intent_name, 0) + 1
            if (monitor := runtime.get("monitor")) is not None:
                monitor.async_record(
                    text=user_input.text, lang=match.lang, result="hit", intent=match.intent_name,
                    ms=round((time.perf_counter() - t0) * 1000.0, 1),
                    error=result.response.response_type == intent.IntentResponseType.ERROR,
                    device=user_input.device_id,
                )
            return result

        # Miss → proxy to fallback agent.
        t_miss_ms = (time.perf_counter() - t0) * 1000.0
        _LOGGER.debug(
            "Polyglot miss after %.1f ms — text=%r (lang_hint=%s) → proxy to %s",
            t_miss_ms, user_input.text, user_input.language, self._fallback_agent_id,
        )
        builtin = await self._try_builtin(runtime, user_input, chat_log, matcher, t0)
        if builtin is not None:
            return builtin

        monitor = runtime.get("monitor")
        before = len(chat_log.content)
        result = await self._proxy_to_fallback(user_input, t_miss_ms, chat_log, matcher.languages)
        if monitor is not None:
            calls = [
                {"tool": tc.tool_name, "args": tc.tool_args}
                for content in chat_log.content[before:]
                for tc in (getattr(content, "tool_calls", None) or [])
                if not tc.external
            ]
            speech = result.response.speech.get("plain", {}).get("speech", "")
            monitor.async_record(
                text=user_input.text, lang=user_input.language, result="assistant",
                targets=calls[:5], speech=speech[:200],
                ms=round((time.perf_counter() - t0) * 1000.0, 1),
                error=result.response.response_type == intent.IntentResponseType.ERROR,
                device=user_input.device_id,
            )
        return result

    async def _handle_match(
        self,
        match: MatchResult,
        user_input: ConversationInput,
        chat_log: ChatLog,
        config: dict[str, Any],
    ) -> ConversationResult:
        """Dispatch the matched intent to HA's intent system."""
        # Inject the matched language as a slot so existing user-side
        # intent_script Jinja templates can branch on `lang`.
        slots: dict[str, dict[str, Any]] = dict(match.slots)
        slots[SLOT_LANG] = {"value": match.lang}
        slots[SLOT_UTTERANCE] = {"value": user_input.text}

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
                conversation_id=chat_log.conversation_id,
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
                conversation_id=chat_log.conversation_id,
            )

        # IntentResponse is already populated by the handler (e.g. intent_script
        # set speech via its `speech.text` template). We just need to return it.
        # If the handler didn't set speech (rare), fall back to a generic OK.
        if not response.speech:
            response.async_set_speech("OK")

        intent_cfg = (config.get("intents") or {}).get(match.intent_name)
        intent_cfg = intent_cfg if isinstance(intent_cfg, dict) else {}
        spoken = response.speech.get("plain", {}).get("speech", "")
        if self._wants_chime(intent_cfg, response):
            # Polyglot TTS turns the marker into a short tone; the real answer
            # stays in the response data (and in the ChatLog record).
            response.async_set_speech(CHIME_MARKER)

        if self._entry.options.get(CONF_CHAT_LOG_TOOLS, False):
            if intent_cfg:
                await async_record_hit(
                    self.hass,
                    user_input=user_input,
                    chat_log=chat_log,
                    intent_cfg=intent_cfg,
                    slots=slots,
                    response=response,
                    fallback_agent_id=self._fallback_agent_id,
                    spoken=spoken,
                )

        return ConversationResult(
            response=response,
            conversation_id=chat_log.conversation_id,
        )

    async def _try_builtin(
        self, runtime: dict[str, Any], user_input: ConversationInput, chat_log: ChatLog,
        matcher: MultilangMatcher, t0: float,
    ) -> ConversationResult | None:
        """Home Assistant's built-in commands in the detected language."""
        builtin = runtime.get("builtin")
        if builtin is None:
            return None
        langs = builtin.languages
        detected = guess_language(user_input.text, langs)
        candidates = [lang for lang in dict.fromkeys([detected, user_input.language]) if lang in langs]
        if not candidates:
            return None
        slot_lists = runtime["builtin_lists"].async_get()
        for lang in candidates:
            rec = await self.hass.async_add_executor_job(builtin.recognize, user_input.text, lang, slot_lists)
            if rec is None:
                continue
            result, responses = rec
            reason = is_blocked(self.hass, result)
            if reason:
                _LOGGER.debug("Polyglot built-in %s refused: %s", result.intent.name, reason)
                return None
            try:
                response = await async_handle_builtin(
                    self.hass, result, responses, text=user_input.text, lang=lang,
                    context=user_input.context, satellite_id=user_input.satellite_id,
                    device_id=user_input.device_id, agent_id=user_input.agent_id,
                )
            except (intent.MatchFailedError, intent.IntentHandleError) as err:
                _LOGGER.debug("Polyglot built-in %s did not match targets: %s", result.intent.name, err)
                return None
            t_ms = (time.perf_counter() - t0) * 1000.0
            self.hass.bus.async_fire(
                EVENT_MATCH,
                {"entry_id": self._entry.entry_id, "intent": result.intent.name, "lang": lang,
                 "input": user_input.text, "took_ms": round(t_ms, 1), "builtin": True,
                 "matched_sentence": result.intent_sentence.text if result.intent_sentence else ""},
            )
            if self._entry.options.get(CONF_CHAT_LOG_TOOLS, False):
                await async_record_hit(
                    self.hass, user_input=user_input, chat_log=chat_log,
                    intent_cfg={"llm_equivalent": {"tool": result.intent.name, "args": {
                        k: str(v.text or v.value) for k, v in (result.entities or {}).items()
                        if k in ("name", "area", "domain", "floor")}}},
                    slots={}, response=response, fallback_agent_id=self._fallback_agent_id,
                )
            if (monitor := runtime.get("monitor")) is not None:
                monitor.async_record(
                    text=user_input.text, lang=lang, result="hit", intent=result.intent.name,
                    builtin=True, ms=round(t_ms, 1), device=user_input.device_id,
                    error=response.response_type == intent.IntentResponseType.ERROR,
                )
            return ConversationResult(response=response, conversation_id=chat_log.conversation_id)
        return None

    def _wants_chime(self, intent_cfg: dict[str, Any], response: intent.IntentResponse) -> bool:
        """Tone only for successful commands — questions are always spoken."""
        mode = intent_cfg.get("response_mode") or self._entry.options.get(
            CONF_RESPONSE_MODE, RESPONSE_TEXT
        )
        return (
            mode == RESPONSE_CHIME
            and intent_cfg.get("kind") == "command"
            and response.response_type == intent.IntentResponseType.ACTION_DONE
        )

    async def _proxy_to_fallback(
        self,
        user_input: ConversationInput,
        miss_took_ms: float,
        chat_log: ChatLog,
        languages: list[str],
    ) -> ConversationResult:
        """Forward the ConversationInput to the configured fallback.

        Unmodified, unless the opt-in language hint is enabled: then a short
        "reply in <detected language>" instruction is appended to
        ``extra_system_prompt`` (pipelines run with one fixed language, so the
        fallback LLM otherwise tends to answer in the language of German tool
        results instead of the user's).
        """
        if self._entry.options.get(CONF_LANGUAGE_HINT, False):
            extra = build_extra_prompt(
                user_input.text,
                user_input.language,
                languages,
                user_input.extra_system_prompt,
                chat_log.extra_system_prompt,
            )
            if extra is not None:
                user_input = dataclasses.replace(user_input, extra_system_prompt=extra)
        fallback_agent = conversation.async_get_agent(
            self.hass, self._fallback_agent_id
        )

        self.hass.bus.async_fire(
            EVENT_MISS_PROXIED,
            {
                "entry_id": self._entry.entry_id,
                "input": user_input.text,
                "language": user_input.language,
                "language_hint": user_input.extra_system_prompt is not None
                and HINT_MARKER in user_input.extra_system_prompt,
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
        # Pass user_input otherwise UNCHANGED — preserves satellite_id,
        # extra_system_prompt and any future ConversationInput fields HA adds.
        return await fallback_agent.async_process(user_input)
