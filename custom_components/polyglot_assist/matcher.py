"""Per-language Hassil sentence matcher.

Builds one ``hassil.Intents`` object per language and tries them in priority
order on each input. The first match wins. The matched language is propagated
back so the response can be rendered in that language and slot ``lang`` can be
injected into the intent dispatch.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# hassil API is sync; we always call match() inside hass.async_add_executor_job
# pylint: disable=import-error
from hassil.intents import Intents
from hassil.recognize import recognize_all


@dataclass(slots=True)
class MatchResult:
    """Outcome of a successful Hassil match."""

    lang: str
    intent_name: str
    slots: dict[str, dict[str, Any]] = field(default_factory=dict)
    matched_sentence: str = ""


class MultilangMatcher:
    """Holds N Hassil ``Intents`` objects, one per language."""

    def __init__(self, intents_by_lang: dict[str, Intents]) -> None:
        self._by_lang: dict[str, Intents] = intents_by_lang

    @property
    def languages(self) -> list[str]:
        return list(self._by_lang.keys())

    def _order(self, hint: str | None) -> list[str]:
        """Return the iteration order of languages, hint first if present."""
        if hint and hint in self._by_lang:
            rest = [lng for lng in self._by_lang if lng != hint]
            return [hint, *rest]
        return list(self._by_lang.keys())

    def match(self, text: str, hint_lang: str | None) -> MatchResult | None:
        """Match ``text`` against all loaded languages, hint-language first.

        Returns the first ``MatchResult`` or ``None`` on miss.
        """
        if not text or not text.strip():
            return None

        for lang in self._order(hint_lang):
            intents = self._by_lang.get(lang)
            if intents is None:
                continue

            try:
                for result in recognize_all(text, intents):
                    # Tag with the source language; convert slot entities into a
                    # simple {name: {"value": v, "text": t}} dict for downstream.
                    slots: dict[str, dict[str, Any]] = {}
                    for ent_name, ent in (result.entities or {}).items():
                        slots[ent_name] = {
                            "value": ent.value,
                            "text": ent.text,
                        }
                    matched_sentence = ""
                    if result.intent_sentence is not None:
                        matched_sentence = result.intent_sentence.text
                    return MatchResult(
                        lang=lang,
                        intent_name=result.intent.name,
                        slots=slots,
                        matched_sentence=matched_sentence,
                    )
            except Exception:  # noqa: BLE001 — Hassil edge cases shouldn't crash the agent
                continue

        return None
