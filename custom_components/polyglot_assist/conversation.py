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

from dataclasses import replace

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
from homeassistant.exceptions import TemplateError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .chatlog import async_record_hit
from .const import (
    CHIME_MARKER,
    CONF_RESPONSE_MODE,
    CONF_VOICE_LEARN,
    CONF_VOICE_SATELLITES,
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
from .speech import say
from .fallback import IN_FALLBACK, is_polyglot_agent
from .learn import get_learn
from .builtin import (
    async_handle_builtin,
    build_slots,
    check_targets,
    is_blocked,
    preferred_area,
)
from .corpus import names_table
from .safety import sensitive_entities
from .langguess import HINT_MARKER, build_extra_prompt, guess_language, transliterate_cyrillic
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
        langs = runtime["matcher"].languages if runtime else []
        return langs or list(DEFAULT_LANGUAGES)

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
        # learning by voice — before the catalog, but only when switched on and on approved satellites
        if self._entry.options.get(CONF_VOICE_LEARN) and user_input.satellite_id and not IN_FALLBACK.get():
            learned = await get_learn(self.hass, self._entry.entry_id).async_handle(
                runtime, text=user_input.text, conversation_id=chat_log.conversation_id,
                satellite_id=user_input.satellite_id,
                approved=set(self._entry.options.get(CONF_VOICE_SATELLITES) or []),
                languages=matcher.languages,
            )
            if learned is not None:
                speech, lang, ask_again = learned
                response = intent.IntentResponse(language=lang)
                response.async_set_speech(speech)
                return ConversationResult(response=response, conversation_id=chat_log.conversation_id,
                                          continue_conversation=ask_again)
        original_input = user_input
        latin = transliterate_cyrillic(user_input.text, matcher.languages)
        if latin is not None:
            # only for matching; the assistant gets what was really recognised
            _LOGGER.debug("Polyglot: Cyrillic speech-to-text %r → %r", user_input.text, latin)
            user_input = dataclasses.replace(user_input, text=latin)
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
                    device=user_input.device_id, satellite=user_input.satellite_id,
                    conversation=chat_log.conversation_id,
                    speech=_speech(result.response),
                    switched=_switched(result.response) or _catalog_entities(
                        (matcher.config.get("intents") or {}).get(match.intent_name)),
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
        # same conversation as ours, so the fallback continues THIS ChatLog
        user_input = dataclasses.replace(original_input, conversation_id=chat_log.conversation_id)
        result = await self._proxy_to_fallback(user_input, t_miss_ms, chat_log, matcher.languages)
        if monitor is not None:
            calls = [
                {"tool": tc.tool_name, "args": tc.tool_args}
                for content in chat_log.content[before:]
                for tc in (getattr(content, "tool_calls", None) or [])
                if not tc.external
            ]
            speech = result.response.speech.get("plain", {}).get("speech", "")
            switched = _tool_result_entities(chat_log.content[before:])
            read = _read_candidates(self.hass, calls)
            monitor.async_record(
                text=user_input.text,
                lang=guess_language(user_input.text, matcher.languages) or (user_input.language or "").split("-")[0],
                result="assistant",
                targets=[{"tool": c["tool"], "args": str(c["args"])[:200],
                          # target fields kept whole (the text above may be cut) — for the target check
                          "target": {k: c["args"][k] for k in ("name", "entity_id", "domain", "area", "floor")
                                     if isinstance(c["args"], dict) and isinstance(c["args"].get(k), (str, list))}}
                         for c in calls[:5]],
                speech=speech[:600], speech_cut=len(speech) > 600, switched=switched, read=read,
                # from ALL calls (targets keeps only 5): did the AI only read state in this turn?
                reads_only=bool(calls) and all(str(c["tool"]).endswith(("GetLiveContext", "HassGetState")) for c in calls),
                ms=round((time.perf_counter() - t0) * 1000.0, 1),
                error=result.response.response_type == intent.IntentResponseType.ERROR,
                device=user_input.device_id, satellite=user_input.satellite_id,
                conversation=chat_log.conversation_id,
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

        owned = ((self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id) or {}).get("owned") or {}).get(match.intent_name)
        try:
            if owned is not None:
                # taken over from YAML: Polyglot's own handler — the intent_script keeps its
                # registry entry (fallback, LLM tool), so it is called directly, not via the registry
                try:
                    response = await owned.async_handle(intent.Intent(
                        self.hass, DOMAIN, match.intent_name, slots, user_input.text, user_input.context,
                        match.lang, device_id=user_input.device_id, satellite_id=user_input.satellite_id,
                        conversation_agent_id=user_input.agent_id))
                except intent.IntentError:
                    raise
                except Exception as err:  # noqa: BLE001 — same as the registry path does for handlers
                    raise intent.IntentUnexpectedError(str(err)) from err
            else:
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
        except (intent.IntentHandleError, intent.IntentUnexpectedError) as err:
            _LOGGER.error(
                "Polyglot match %s found but intent handler failed: %s",
                match.intent_name, err,
            )
            err_response = intent.IntentResponse(language=match.lang)
            err_response.async_set_speech(say("handler_failed", match.lang, name=match.intent_name))
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
            err_response.async_set_speech(say("no_handler", match.lang, name=match.intent_name))
            return ConversationResult(
                response=err_response,
                conversation_id=chat_log.conversation_id,
            )

        # IntentResponse is already populated by the handler (e.g. intent_script
        # set speech via its `speech.text` template). We just need to return it.
        # If the handler didn't set speech (rare), fall back to a generic OK.
        if not response.speech:
            response.async_set_speech(say("done", match.lang))

        intent_cfg = (config.get("intents") or {}).get(match.intent_name)
        intent_cfg = intent_cfg if isinstance(intent_cfg, dict) else {}
        spoken = response.speech.get("plain", {}).get("speech", "")
        # tone answers only make sense on voice devices, never in text chats
        # only a satellite plays audio; an app (device_id only) would show the marker as text
        if self._wants_chime(intent_cfg, response, bool(user_input.satellite_id)):
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
        pipeline_lang = (user_input.language or "").split("-")[0]
        detected = guess_language(user_input.text, langs)
        # detected and pipeline language first, then every other enabled language
        candidates = [lang for lang in dict.fromkeys([detected, pipeline_lang, *langs]) if lang in langs]
        if not candidates:
            return None
        slot_lists = runtime["builtin_lists"].async_get()
        area_id = preferred_area(self.hass, user_input.satellite_id, user_input.device_id)
        area = ar.async_get(self.hass).async_get_area(area_id) if area_id else None
        intent_context = {"area": {"value": area.name, "text": area.name}} if area else None
        config = matcher.config
        for lang in candidates:
            rec = await self.hass.async_add_executor_job(
                builtin.recognize, user_input.text, lang, slot_lists, intent_context
            )
            if rec is None:
                continue
            result, responses = rec
            reason = is_blocked(self.hass, result) or check_targets(
                self.hass, result, build_slots(result), area_id,
                sensitive=sensitive_entities(config), extra_names=names_table(config),
            )
            if reason:
                _LOGGER.debug("Polyglot built-in %s refused: %s", result.intent.name, reason)
                return None
            try:
                response = await async_handle_builtin(
                    self.hass, result, responses, text=user_input.text, lang=lang,
                    context=user_input.context, satellite_id=user_input.satellite_id,
                    device_id=user_input.device_id, agent_id=user_input.agent_id,
                )
            except (intent.UnknownIntent, intent.InvalidSlotInfo, intent.MatchFailedError) as err:
                # nothing ran yet → let the assistant handle it
                _LOGGER.debug("Polyglot built-in %s not usable: %s", result.intent.name, err)
                return None
            except (intent.IntentHandleError, intent.IntentUnexpectedError, TemplateError) as err:
                # the action may have run → answer with an error, never run it twice
                _LOGGER.warning("Polyglot built-in %s failed: %s", result.intent.name, err)
                response = intent.IntentResponse(language=lang)
                response.async_set_error(intent.IntentResponseErrorCode.FAILED_TO_HANDLE, say("error", lang))
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
                    satellite=user_input.satellite_id, conversation=chat_log.conversation_id,
                    error=response.response_type == intent.IntentResponseType.ERROR,
                    speech=_speech(response), switched=_switched(response),
                )
            return ConversationResult(response=response, conversation_id=chat_log.conversation_id)
        return None

    def _wants_chime(
        self, intent_cfg: dict[str, Any], response: intent.IntentResponse, voice: bool
    ) -> bool:
        """Tone only for successful commands — questions are always spoken."""
        mode = intent_cfg.get("response_mode") or self._entry.options.get(
            CONF_RESPONSE_MODE, RESPONSE_TEXT
        )
        return (
            mode == RESPONSE_CHIME
            and voice
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

        loop = fallback_agent is not None and (IN_FALLBACK.get() or is_polyglot_agent(self.hass, self._fallback_agent_id))
        if loop:
            # Polyglot → … → Polyglot → fallback again would never end
            _LOGGER.error(
                "Polyglot Assist: fallback %r leads back to Polyglot — refusing (loop)",
                self._fallback_agent_id,
            )
            fallback_agent = None
        if fallback_agent is None:
            _LOGGER.error(
                "Polyglot miss but fallback agent %r not available",
                self._fallback_agent_id,
            )
            lang = guess_language(user_input.text, languages) or user_input.language
            err_response = intent.IntentResponse(language=user_input.language)
            err_response.async_set_speech(say("fallback_loop" if loop else "fallback_unavailable", lang))
            return ConversationResult(
                response=err_response,
                conversation_id=user_input.conversation_id,
            )

        # IMPORTANT: call async_process, NOT _async_handle_message.
        # The base method sets up a fresh ChatLog session for the fallback.
        # Pass user_input otherwise UNCHANGED — preserves satellite_id,
        # extra_system_prompt and any future ConversationInput fields HA adds.
        token = IN_FALLBACK.set(True)
        try:
            result = await fallback_agent.async_process(user_input)
            if (user_input.conversation_id and result.response.response_type == intent.IntentResponseType.ERROR
                    and result.response.error_code == intent.IntentResponseErrorCode.UNKNOWN):
                # The fallback failed inside a running conversation. The usual cause is a history that no longer
                # fits the model's context (HA keeps the whole chat for 5 min; a few web searches are enough) ->
                # "Error talking to API" for every following sentence. Retry ONCE in a fresh conversation.
                _LOGGER.warning(
                    "Polyglot fallback %s failed in conversation %s (%s) — retrying once without history",
                    self._fallback_agent_id, user_input.conversation_id, _speech(result.response)[:120])
                result = await fallback_agent.async_process(replace(user_input, conversation_id=None))
            return result
        finally:
            IN_FALLBACK.reset(token)


# ── what the monitor keeps about a request (for "what went wrong" reviews) ──
MAX_SWITCHED = 10


def _speech(response: intent.IntentResponse) -> str:
    return (response.speech.get("plain", {}).get("speech", "") or "")[:200]


def _switched(response: intent.IntentResponse) -> list[str]:
    """Entity ids a HA intent reports as switched."""
    out = []
    for target in getattr(response, "success_results", None) or []:
        if getattr(target, "type", None) == intent.IntentResponseTargetType.ENTITY and isinstance(target.id, str):
            out.append(target.id)
    return out[:MAX_SWITCHED]


def _tool_result_entities(contents) -> list[str]:
    """Entity ids in the tool RESULTS of an AI turn (what was actually switched)."""
    found: list[str] = []

    def walk(value, depth=0, in_success=False):
        """Entities only from "success" lists (HA intent results) — not the requested targets or failures."""
        if depth > 6 or len(found) >= MAX_SWITCHED:
            return
        if isinstance(value, dict):
            if in_success and value.get("type") == "entity" and isinstance(value.get("id"), str) and "." in value["id"]:
                if value["id"] not in found:
                    found.append(value["id"])
            for key, inner in value.items():
                walk(inner, depth + 1, in_success or key == "success")
        elif isinstance(value, list):
            for inner in value:
                walk(inner, depth + 1, in_success)

    for content in contents:
        if getattr(content, "role", None) == "tool_result":
            result = getattr(content, "result", None)
            # HA ≥ 2026.10: ToolResult.data (content.tool_result is deprecated, breaks in 2027.11)
            walk(result.data if hasattr(result, "data") else getattr(content, "tool_result", None))
    return found[:MAX_SWITCHED]


def _catalog_entities(intent_cfg) -> list[str]:
    """Literal entity ids a catalog command's action targets (its handler reports no results)."""
    from .proposals import iter_step_entities  # noqa: PLC0415

    action = (intent_cfg or {}).get("action") if isinstance(intent_cfg, dict) else None
    out: list[str] = []
    for step in action if isinstance(action, list) else ([action] if action else []):
        for entity in iter_step_entities(step) or []:
            if "{" not in entity and entity not in out:
                out.append(entity)
    return out[:MAX_SWITCHED]


def _read_candidates(hass, calls: list[dict]) -> list[dict]:
    """Which exposed entities a state-reading tool call looked at (with their value right now).

    GetLiveContext returns names, not entity ids — so its filter (name / area /
    domain) is replayed with HA's own matcher on the exposed states. An
    unfiltered call (everything) or more than 5 matches count as "unknown".
    """
    from homeassistant.components.homeassistant.exposed_entities import async_should_expose  # noqa: PLC0415

    out: list[dict] = []
    for call in calls:
        tool = str(call.get("tool") or "")
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        if not (tool.endswith("GetLiveContext") or tool.endswith("HassGetState")):
            continue
        name, area = args.get("name"), args.get("area")
        domain = args.get("domain")
        domains = [domain] if isinstance(domain, str) else [d for d in (domain or []) if isinstance(d, str)]
        if not (name or area or domains):
            return []  # everything was read — no way to tell which value answered
        states = [s for s in hass.states.async_all() if async_should_expose(hass, "conversation", s.entity_id)]
        try:
            match = intent.async_match_targets(
                hass, intent.MatchTargetsConstraints(
                    name=name if isinstance(name, str) else None, area_name=area if isinstance(area, str) else None,
                    domains=[d.strip().lower() for d in domains] or None, allow_duplicate_names=True),
                states=states)
        except Exception:  # noqa: BLE001 — recording must never break the answer
            return []
        if not match.is_match or len(match.states) > 5:
            return []
        for st in match.states:
            if not any(r["e"] == st.entity_id for r in out):
                out.append({"e": st.entity_id, "s": st.state[:40]})
    return out[:5]
