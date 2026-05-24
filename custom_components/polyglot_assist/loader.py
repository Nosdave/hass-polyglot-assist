"""YAML loader for Polyglot Assist intents.

Reads a single user-facing YAML file with this shape:

    intents:
      WeatherForecast:
        description: "Multi-day weather forecast"
        sentences:
          de: ["wettervorhersage [heute]", "wie wird das wetter [heute]"]
          en: ["(what is|what's|how is) the weather", "weather forecast"]
          fr: ["(quel|quelle) (est|sera) la météo", "météo aujourd'hui"]
        slots: {}                # optional, see hassil docs for slot-lists
        response_intent: WeatherForecast   # name of an existing intent_script

…and splits it into N ``hassil.Intents`` objects (one per language). The
matched language is later passed to the intent handler via the ``lang`` slot,
so the existing user-side ``intent_script`` Jinja templates can branch on it
without changes.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from hassil.intents import Intents  # pylint: disable=import-error
from homeassistant.core import HomeAssistant

from .matcher import MultilangMatcher

_LOGGER = logging.getLogger(__name__)


class MatcherLoader:
    """Loads a YAML intents file and produces a ready-to-use MultilangMatcher."""

    @staticmethod
    async def load(hass: HomeAssistant, intents_file: str) -> MultilangMatcher:
        path = Path(intents_file)
        if not path.is_absolute():
            path = Path(hass.config.path(intents_file))

        if not path.is_file():
            raise FileNotFoundError(f"Polyglot intents file not found: {path}")

        raw = await hass.async_add_executor_job(path.read_text, "utf-8")
        data = yaml.safe_load(raw) or {}
        intents_section = data.get("intents", {})
        if not intents_section:
            raise ValueError(f"No intents in {path}")

        # Bucket sentences by language
        per_lang: dict[str, dict[str, Any]] = {}
        for intent_name, intent_cfg in intents_section.items():
            sentences = intent_cfg.get("sentences", {}) or {}
            if not isinstance(sentences, dict):
                _LOGGER.warning(
                    "Intent %s: 'sentences' must be a dict keyed by language",
                    intent_name,
                )
                continue
            for lang, sent_list in sentences.items():
                bucket = per_lang.setdefault(lang, {})
                # Hassil shape per intent: { data: [ { sentences: [...] } ] }
                bucket[intent_name] = {
                    "data": [{"sentences": list(sent_list)}],
                }

        # Build one Intents object per language by injecting language at the top
        result: dict[str, Intents] = {}
        for lang, intents_dict in per_lang.items():
            payload = {"language": lang, "intents": intents_dict}
            try:
                result[lang] = await hass.async_add_executor_job(
                    Intents.from_dict, payload
                )
                _LOGGER.info(
                    "Polyglot Assist: loaded %d intents for language '%s'",
                    len(intents_dict),
                    lang,
                )
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Failed to build Hassil Intents for lang=%s", lang)

        if not result:
            raise ValueError(f"No language groups successfully loaded from {path}")

        return MultilangMatcher(result)
