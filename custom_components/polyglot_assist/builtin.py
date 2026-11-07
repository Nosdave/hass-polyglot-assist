"""Home Assistant's built-in commands in every enabled language.

Home Assistant pipelines run in ONE language, so "allume la lumière du salon"
never reaches the built-in French sentences. Polyglot matches the sentences
Home Assistant ships (``home-assistant-intents``) in all enabled languages —
with Polyglot's runtime lists, so the per-language name table works too
("salon" → area "Wohnzimmer") — and runs the built-in intent handler.

Safety: only an allowlist of everyday intents is executed, entities in
security-relevant domains (locks, alarms, valves, garage/door/gate covers)
are never targeted this way (the sentence goes on to the assistant, which has
its own rules), and Home Assistant's handlers only see entities exposed to
Assist.
"""
from __future__ import annotations

import logging
from typing import Any

from hassil.intents import Intents, SlotList, TextSlotList
from hassil.recognize import RecognizeResult, recognize_best

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent
from homeassistant.helpers.template import Template

_LOGGER = logging.getLogger(__name__)

ALLOWED_INTENTS = {
    "HassTurnOn", "HassTurnOff", "HassToggle", "HassLightSet", "HassSetPosition",
    "HassStartTimer", "HassCancelTimer", "HassCancelAllTimers", "HassIncreaseTimer",
    "HassDecreaseTimer", "HassPauseTimer", "HassUnpauseTimer", "HassTimerStatus",
    "HassGetCurrentTime", "HassGetCurrentDate", "HassGetWeather",
    "HassShoppingListAddItem", "HassListAddItem",
    "HassMediaPause", "HassMediaUnpause", "HassMediaNext", "HassMediaPrevious",
    "HassSetVolume", "HassSetVolumeRelative", "HassClimateGetTemperature",
    "HassClimateSetTemperature", "HassFanSetSpeed",
}
WILDCARD_OK = {"HassShoppingListAddItem", "HassListAddItem"}
BLOCKED_DOMAINS = {"lock", "alarm_control_panel", "valve", "siren", "camera"}
BLOCKED_COVER_CLASSES = {"garage", "door", "gate"}


def _empty(name: str) -> TextSlotList:
    return TextSlotList.from_tuples([], allow_template=False, name=name)


class BuiltinIntents:
    """Per-language built-in sentence data, loaded once (executor)."""

    def __init__(self, languages: list[str]) -> None:
        self.languages = languages
        self._by_lang: dict[str, tuple[Intents, dict[str, Any], list[str]]] = {}

    def load(self) -> None:
        """Blocking: read the sentence packages (call in the executor)."""
        try:
            from home_assistant_intents import get_intents  # noqa: PLC0415
        except ImportError:
            _LOGGER.warning("Polyglot Assist: home-assistant-intents not available")
            return
        for lang in self.languages:
            try:
                data = get_intents(lang)
            except Exception:  # noqa: BLE001
                data = None
            if not data:
                continue
            payload = {
                "language": lang,
                "intents": {k: v for k, v in data["intents"].items() if k in ALLOWED_INTENTS},
                "lists": data.get("lists") or {},
                "expansion_rules": data.get("expansion_rules") or {},
                "skip_words": data.get("skip_words") or [],
            }
            try:
                self._by_lang[lang] = (
                    Intents.from_dict(payload),
                    (data.get("responses") or {}).get("intents") or {},
                    list(data.get("skip_words") or []),
                )
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Polyglot Assist: built-in sentences for %s failed", lang)

    @property
    def loaded(self) -> list[str]:
        return list(self._by_lang)

    def recognize(
        self, text: str, lang: str, slot_lists: dict[str, SlotList]
    ) -> tuple[RecognizeResult, dict[str, Any]] | None:
        """Blocking: best built-in match in ``lang`` (call in the executor)."""
        entry = self._by_lang.get(lang)
        if entry is None:
            return None
        intents, responses, skip_words = entry
        lists = dict(slot_lists)
        for ref in ("name", "area", "floor"):
            lists.setdefault(ref, _empty(ref))
        try:
            result = recognize_best(text, intents, slot_lists=lists, skip_words=skip_words, language=lang)
        except Exception:  # noqa: BLE001 — e.g. a list Polyglot does not provide
            return None
        return (result, responses) if result is not None else None


def is_blocked(hass: HomeAssistant, result: RecognizeResult) -> str | None:
    """Reason to refuse a built-in match, or None."""
    if result.intent.name not in ALLOWED_INTENTS:
        return "intent not allowed"
    entities = result.entities or {}
    # Free-text placeholders (timer names …) swallow unrelated questions
    # ("how long does it take to get to work?") — only lists may use them.
    if result.intent.name not in WILDCARD_OK and any(
        getattr(ent, "is_wildcard", False) for ent in entities.values()
    ):
        return "free-text placeholder"
    if result.intent.name in ("HassTurnOn", "HassTurnOff", "HassToggle") and "name" not in entities and "domain" not in entities:
        return "area-only command without a device type"
    domain = entities.get("domain")
    domains = {domain.value} if domain and isinstance(domain.value, str) else set()
    if domain and isinstance(domain.value, list):
        domains |= set(domain.value)
    if domains & BLOCKED_DOMAINS:
        return "security-relevant domain"
    ctx = result.context or {}
    if isinstance(ctx.get("domain"), str) and ctx["domain"] in BLOCKED_DOMAINS:
        return "security-relevant domain"
    entity_id = ctx.get("entity_id") if "name" in entities else None
    if entity_id:
        if entity_id.split(".", 1)[0] in BLOCKED_DOMAINS:
            return "security-relevant device"
        state = hass.states.get(entity_id)
        if state and state.domain == "cover" and state.attributes.get("device_class") in BLOCKED_COVER_CLASSES:
            return "garage/door/gate"
    if "cover" in domains or result.intent.name in ("HassSetPosition", "HassTurnOn", "HassTurnOff"):
        dc = entities.get("device_class")
        values = dc.value if dc else None
        values = set(values) if isinstance(values, list) else {values} if values else set()
        if values & BLOCKED_COVER_CLASSES:
            return "garage/door/gate"
    return None


def preferred_area(hass: HomeAssistant, satellite_id: str | None, device_id: str | None) -> str | None:
    if satellite_id and (entry := er.async_get(hass).async_get(satellite_id)) and entry.area_id:
        return entry.area_id
    if device_id:
        from homeassistant.helpers import device_registry as dr  # noqa: PLC0415

        device = dr.async_get(hass).async_get(device_id)
        if device and device.area_id:
            return device.area_id
    return None


async def async_handle_builtin(
    hass: HomeAssistant,
    result: RecognizeResult,
    responses: dict[str, Any],
    *,
    text: str,
    lang: str,
    context,
    satellite_id: str | None,
    device_id: str | None,
    agent_id: str | None,
) -> intent.IntentResponse:
    """Run the built-in handler and render Home Assistant's own answer."""
    slots: dict[str, Any] = {
        name: {"value": ent.value, "text": ent.text or ent.value}
        for name, ent in (result.entities or {}).items()
    }
    if (area_id := preferred_area(hass, satellite_id, device_id)) is not None:
        slots["preferred_area_id"] = {"value": area_id}
    response = await intent.async_handle(
        hass,
        "conversation",
        result.intent.name,
        slots,
        text,
        context,
        lang,
        assistant="conversation",
        device_id=device_id,
        satellite_id=satellite_id,
        conversation_agent_id=agent_id,
    )
    if not response.speech:
        key = result.response or "default"
        template_text = (responses.get(result.intent.name) or {}).get(key)
        if template_text:
            speech_slots = {name: ent.text or ent.value for name, ent in (result.entities or {}).items()}
            speech_slots |= response.speech_slots
            state1 = (response.matched_states or response.unmatched_states or [None])[0]
            from homeassistant.helpers.template import TemplateState  # noqa: PLC0415

            rendered = Template(template_text, hass).async_render(
                {
                    "slots": speech_slots,
                    "state": TemplateState(hass, state1) if state1 is not None else None,
                    "query": {
                        "matched": [TemplateState(hass, s) for s in response.matched_states],
                        "unmatched": [TemplateState(hass, s) for s in response.unmatched_states],
                    },
                },
                parse_result=False,
            )
            response.async_set_speech(" ".join(str(rendered).split()))
    return response


def area_names(hass: HomeAssistant) -> dict[str, str]:
    return {a.id: a.name for a in ar.async_get(hass).async_list_areas()}
