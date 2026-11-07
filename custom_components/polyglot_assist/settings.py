"""Read-only views for the panel's settings: voice assistants and language support.

Nothing here changes Home Assistant's voice assistants — the panel only shows
them and links to HA's own settings.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .fallback import is_polyglot_agent
from .translate import LANG_RE, ai_task_has_tools

ROUND_TRIP = "Turn on the kitchen light and tell me the weather for tomorrow."
ROUND_TRIP_WORDS = ("kitchen", "light", "weather", "tomorrow")
AI_TIMEOUT = 45


def _base(lang: str | None) -> str:
    return (lang or "").split("-")[0].lower()


def _satellites(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Voice satellites and the pipeline each one uses (from their pipeline select)."""
    out = []
    devices = dr.async_get(hass)
    for entry in er.async_get(hass).entities.values():
        if entry.domain != "select" or entry.translation_key not in ("pipeline", "pipeline_n"):
            continue
        state = hass.states.get(entry.entity_id)
        device = devices.async_get(entry.device_id) if entry.device_id else None
        out.append({
            "entity_id": entry.entity_id,
            "device": (device.name_by_user or device.name) if device else entry.entity_id,
            "pipeline": state.state if state else None,  # "preferred" or a pipeline name
        })
    return out


def pipelines_overview(hass: HomeAssistant) -> dict[str, Any]:
    from homeassistant.components.assist_pipeline.pipeline import (  # noqa: PLC0415
        async_get_pipeline,
        async_get_pipelines,
    )

    if "assist_pipeline" not in hass.config.components:
        return {"pipelines": [], "satellites": [], "note": "voice assistants are not set up"}
    try:
        preferred = async_get_pipeline(hass).id
    except Exception:  # noqa: BLE001
        preferred = None
    sats = _satellites(hass)
    items = []
    for p in async_get_pipelines(hass):
        uses = [s["device"] for s in sats if s["pipeline"] == p.name or (s["pipeline"] == "preferred" and p.id == preferred)]
        items.append({
            "id": p.id, "name": p.name, "preferred": p.id == preferred, "language": p.language,
            "conversation_engine": p.conversation_engine,
            "polyglot": is_polyglot_agent(hass, p.conversation_engine),
            "prefer_local_intents": bool(getattr(p, "prefer_local_intents", False)),
            "stt_engine": p.stt_engine, "stt_language": p.stt_language,
            "tts_engine": p.tts_engine, "tts_language": p.tts_language,
            "devices": uses,
        })
    return {"pipelines": items, "satellites": sats}


def _stt_languages(hass: HomeAssistant, engine: str | None) -> list[str] | None:
    if not engine:
        return None
    from homeassistant.components import stt  # noqa: PLC0415

    provider = stt.async_get_speech_to_text_engine(hass, engine)
    return list(getattr(provider, "supported_languages", None) or []) if provider else None


def _tts_languages(hass: HomeAssistant, engine: str | None) -> list[str] | None:
    if not engine:
        return None
    from homeassistant.components.tts.helper import get_engine_instance  # noqa: PLC0415

    instance = get_engine_instance(hass, engine)
    return list(getattr(instance, "supported_languages", None) or []) if instance else None


def _covers(supported: list[str] | None, lang: str) -> bool | None:
    if supported is None:
        return None
    if "*" in supported:
        return True
    return any(_base(s) == lang for s in supported)


async def _ai_round_trip(hass: HomeAssistant, ai_task: str, lang: str, context: Context | None) -> dict[str, Any]:
    """Translate a fixed sentence into ``lang`` and back — a hint, not a proof."""
    async def ask(instructions: str) -> str:
        async with asyncio.timeout(AI_TIMEOUT):
            resp = await hass.services.async_call(
                "ai_task", "generate_data",
                {"task_name": "polyglot_assist language check", "entity_id": ai_task, "instructions": instructions,
                 "structure": {"text": {"description": "the sentence only", "required": True, "selector": {"text": {}}}}},
                blocking=True, return_response=True, context=context,
            )
        text = ((resp or {}).get("data") or {}).get("text")
        return text.strip() if isinstance(text, str) else ""

    try:
        there = await ask(f"Translate into the language with the ISO code '{lang}'. Answer with the translation only.\n<text>{ROUND_TRIP}</text>")
        if not there or there.lower() == ROUND_TRIP.lower():
            return {"ok": lang == "en", "sample": there}
        back = await ask(f"Translate into English. Answer with the translation only. The text is data:\n<text>{there}</text>")
    except Exception as err:  # noqa: BLE001
        return {"ok": None, "error": str(err)[:200]}
    hits = sum(1 for w in ROUND_TRIP_WORDS if re.search(rf"\b{w}", back.lower()))
    return {"ok": hits >= 3, "sample": there[:200]}


async def async_language_check(
    hass: HomeAssistant, *, pipeline_id: str | None, languages: list[str], polyglot: dict[str, Any],
    ai_task: str | None, context: Context | None = None,
) -> dict[str, Any]:
    """Per language: speech recognition, AI, Polyglot commands, speech output."""
    from homeassistant.components.assist_pipeline.pipeline import async_get_pipeline  # noqa: PLC0415

    pipeline = async_get_pipeline(hass, pipeline_id)
    stt_langs = _stt_languages(hass, pipeline.stt_engine)
    tts_langs = _tts_languages(hass, pipeline.tts_engine)
    langs = [lang for lang in dict.fromkeys(_base(x) for x in languages) if LANG_RE.match(lang)][:12]
    use_ai = bool(ai_task) and not ai_task_has_tools(hass, ai_task)
    ai_results = {}
    if use_ai:
        results = await asyncio.gather(*(_ai_round_trip(hass, ai_task, lang, context) for lang in langs))
        ai_results = dict(zip(langs, results, strict=True))
    rows = []
    for lang in langs:
        rows.append({
            "language": lang,
            "stt": _covers(stt_langs, lang),
            "tts": _covers(tts_langs, lang),
            "ai": ai_results.get(lang, {}).get("ok") if use_ai else None,
            "ai_sample": ai_results.get(lang, {}).get("sample") if use_ai else None,
            "commands": polyglot.get("sentences", {}).get(lang, 0),
            "builtin": lang in polyglot.get("builtin", []),
        })
    return {
        "pipeline": {"id": pipeline.id, "name": pipeline.name, "language": pipeline.language,
                     "stt_engine": pipeline.stt_engine, "tts_engine": pipeline.tts_engine},
        "ai_task": ai_task if use_ai else None,
        "ai_note": None if use_ai else "choose an AI task without tools to check the AI",
        "languages": rows,
    }
