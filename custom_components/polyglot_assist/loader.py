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

from .corpus import bucket_by_language, static_lists, string_rules, validate
from .matcher import MultilangMatcher

_LOGGER = logging.getLogger(__name__)


class MatcherLoader:
    """Loads a YAML intents file and produces a ready-to-use MultilangMatcher."""

    @staticmethod
    async def load(hass: HomeAssistant, intents_file: str) -> MultilangMatcher:
        """Load from a YAML file (kept for callers outside a config entry)."""
        path = Path(intents_file)
        if not path.is_absolute():
            path = Path(hass.config.path(intents_file))
        if not path.is_file():
            raise FileNotFoundError(f"Polyglot intents file not found: {path}")
        raw = await hass.async_add_executor_job(path.read_text, "utf-8")
        return await MatcherLoader.load_data(hass, yaml.safe_load(raw) or {}, path.name)

    @staticmethod
    async def load_data(
        hass: HomeAssistant, data: dict, origin: str, languages: list[str] | None = None
    ) -> MultilangMatcher:
        """Build the matcher from parsed intents data (YAML file or catalog)."""
        if not data.get("intents"):
            raise ValueError(f"No intents in {origin}")

        # Problems hassil would only raise lazily while matching (e.g. a
        # sentence referencing an undefined {list}) silently disable matching
        # for that language. Surface them at load time.
        try:
            for problem in validate(data):
                _LOGGER.warning("Polyglot Assist: %s (%s)", problem, origin)
        except Exception:  # noqa: BLE001 — diagnostics must never block loading
            _LOGGER.exception("Polyglot Assist: validation of %s failed", origin)

        lists_section = static_lists(data)
        rules_section = string_rules(data)

        # One Intents object per language; a broken language is skipped.
        result: dict[str, Intents] = {}
        for lang, intents_dict in bucket_by_language(data).items():
            if languages and lang not in languages:
                continue  # language switched off in the options
            payload = {"language": lang, "intents": intents_dict}
            if lists_section:
                payload["lists"] = lists_section
            if rules_section:
                payload["expansion_rules"] = rules_section
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

        if not result and bucket_by_language(data):
            raise ValueError(f"No language groups successfully loaded from {origin}")
        # every command switched off on purpose → a matcher that always misses

        matcher = MultilangMatcher(result)
        matcher.config = data
        return matcher
