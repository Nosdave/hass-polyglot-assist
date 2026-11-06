"""Translation PROPOSALS for intent sentences and names (never auto-applied).

An AI Task entity (pick one without tools — the sentences are untrusted text
for it) turns the sentences of one intent into hassil templates for other
languages. Every proposal is validated (parsable, same slots as a source
sentence, sample expansions must not match another intent) and returned to
the caller; saving is a separate, regression-guarded step.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .corpus import capture_lists, check_proposal, string_rules

_LOGGER = logging.getLogger(__name__)

LANG_NAMES = {"de": "German", "en": "English", "fr": "French", "nl": "Dutch",
              "it": "Italian", "es": "Spanish"}

_SYNTAX = (
    "Home Assistant (hassil) sentence template syntax: [optional words], "
    "(alternative|alternative), {list_name} = a slot filled from a list, "
    "<rule_name> = an expansion rule. Never translate, rename, add or drop "
    "{list} or <rule> references; keep exactly the slot set of the source "
    "sentence you translate. Write lowercase, no punctuation. Cover natural "
    "spoken variants (polite and short forms) and common speech-to-text "
    "variants, but do not invent different meanings."
)


async def _generate(hass: HomeAssistant, entity_id: str, instructions: str) -> list[str]:
    response = await hass.services.async_call(
        "ai_task",
        "generate_data",
        {
            "task_name": "polyglot_assist translation",
            "entity_id": entity_id,
            "instructions": instructions,
            "structure": {
                "sentences": {
                    "description": "Proposed sentence templates",
                    "required": True,
                    "selector": {"text": {"multiple": True}},
                }
            },
        },
        blocking=True,
        return_response=True,
    )
    data = (response or {}).get("data") or {}
    sentences = data.get("sentences") if isinstance(data, dict) else None
    if not isinstance(sentences, list):
        raise HomeAssistantError("AI task returned no sentence list")
    cleaned = [" ".join(str(s).split()) for s in sentences]
    return list(dict.fromkeys(s for s in cleaned if s))[:30]


async def async_propose_sentences(
    hass: HomeAssistant,
    *,
    ai_task_entity: str,
    intent_name: str,
    intent_cfg: dict[str, Any],
    config: dict[str, Any],
    languages: list[str],
    match_fn,
    slot_lists: dict,
) -> dict[str, list[dict[str, Any]]]:
    """Return {lang: [validated proposals]} — nothing is saved."""
    sentences: dict[str, list[str]] = intent_cfg.get("sentences") or {}
    source = [s for lst in sentences.values() for s in (lst or []) if isinstance(s, str)]
    if not source:
        raise HomeAssistantError("intent has no sentences to translate from")
    slot_sets = sorted({", ".join(sorted(capture_lists(s))) or "(none)" for s in source})
    out: dict[str, list[dict[str, Any]]] = {}
    for lang in languages:
        existing = sentences.get(lang) or []
        instructions = (
            f"{_SYNTAX}\n\nIntent: {intent_name}"
            + (f" — {intent_cfg['description']}" if intent_cfg.get("description") else "")
            + "\nSource sentences (all languages):\n"
            + "\n".join(f"- {s}" for s in source)
            + f"\nAllowed slot sets: {'; '.join(slot_sets)}"
            + (
                "\nAlready present in the target language (do not repeat):\n"
                + "\n".join(f"- {s}" for s in existing)
                if existing
                else ""
            )
            + f"\n\nWrite up to 8 sentence templates in {LANG_NAMES.get(lang, lang)} "
            "with the same meaning."
        )
        proposals = await _generate(hass, ai_task_entity, instructions)
        out[lang] = [
            await hass.async_add_executor_job(
                lambda p=p: check_proposal(
                    intent_name,
                    p,
                    source,
                    match_fn,
                    slot_lists,
                    string_rules(config),
                    lang,
                )
            )
            for p in proposals
            if p not in existing
        ]
    return out


async def async_propose_names(
    hass: HomeAssistant,
    *,
    ai_task_entity: str,
    targets: dict[str, str],
    languages: list[str],
) -> dict[str, dict[str, list[str]]]:
    """Name proposals {target_id: {lang: [names]}} for the name table."""
    result: dict[str, dict[str, list[str]]] = {}
    for target_id, current in targets.items():
        result[target_id] = {}
        for lang in languages:
            names = await _generate(
                hass,
                ai_task_entity,
                (
                    f"How would people in a household naturally say the name of this "
                    f"smart-home {'room' if target_id.startswith('area.') else 'device'} "
                    f"in {LANG_NAMES.get(lang, lang)} when speaking to a voice assistant? "
                    f"Current name: {current!r}. Give 1 to 4 short spoken forms, "
                    "lowercase, without articles unless they are part of the name."
                ),
            )
            result[target_id][lang] = names[:4]
    return result
