"""Regression-guarded writes of the intents source (panel and options flow).

A write is accepted only if

1. the candidate builds and every sentence/rule parses (structural problems
   that did not exist before are always refused — ``force`` cannot override
   them: one unparsable template silently disables a whole language), and
2. no case of the regression corpus (``polyglot_assist/tests.yaml``) that
   passed before fails afterwards — this part can be overridden by ``force``.

Writes of one entry are serialised by a lock and always start from a fresh
read of the source, so concurrent edits or hand edits of the file are never
silently overwritten (an optional ``base_rev`` detects stale editors).
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import yaml

from homeassistant.core import HomeAssistant

from .const import DEFAULT_TESTS_FILE
from .corpus import parse_corpus, run_corpus, validate
from .loader import MatcherLoader


class GuardError(Exception):
    """Refused write (message is safe to show)."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


def revision(data: dict[str, Any]) -> str:
    """Stable short hash of an intents document (for optimistic locking)."""
    raw = json.dumps(data, ensure_ascii=False, sort_keys=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _lock(runtime: dict[str, Any]) -> asyncio.Lock:
    lock = runtime.get("write_lock")
    if lock is None:
        lock = runtime["write_lock"] = asyncio.Lock()
    return lock


async def _load_cases(hass: HomeAssistant) -> list:
    path = Path(hass.config.path(DEFAULT_TESTS_FILE))

    def _read() -> list | None:
        if not path.is_file():
            return None
        return parse_corpus(yaml.safe_load(path.read_text("utf-8")) or {})

    try:
        return await hass.async_add_executor_job(_read) or []
    except (ValueError, yaml.YAMLError, UnicodeDecodeError) as err:
        raise GuardError("corpus_unreadable", f"{DEFAULT_TESTS_FILE} is not readable: {err}") from None


async def _report(hass: HomeAssistant, runtime: dict[str, Any], data: dict, cases: list) -> dict:
    matcher = await MatcherLoader.load_data(hass, data, "candidate")
    slot_lists = runtime["lists"].async_get()

    def _match(text: str, hint: str | None):
        res = matcher.match(text, hint, slot_lists)
        if res is None:
            return None
        return res.intent_name, res.lang, {k: v.get("value") for k, v in res.slots.items()}

    return await hass.async_add_executor_job(run_corpus, _match, cases, hass.config.language)


async def async_guarded_update(
    hass: HomeAssistant,
    runtime: dict[str, Any],
    mutate: Callable[[dict[str, Any]], None],
    *,
    force: bool = False,
    base_rev: str | None = None,
) -> dict[str, Any]:
    """Apply ``mutate`` to a fresh copy of the source and save it if safe.

    Returns {"saved", "regressions", "problems", "corpus_cases", "rev",
    "reloaded"}; raises GuardError when refused.
    """
    source = runtime["source"]
    async with _lock(runtime):
        current = await source.async_load()
        if base_rev is not None and base_rev != revision(current):
            raise GuardError("conflict", "the intents changed meanwhile — reload and try again")
        candidate = copy.deepcopy(current)
        mutate(candidate)

        names = runtime["lists"].names
        before = set(validate(current, extra_lists=names))
        problems = validate(candidate, extra_lists=names)
        new_problems = [p for p in problems if p not in before]
        if new_problems:
            raise GuardError("invalid", "the change has errors", {"problems": new_problems[:50]})
        try:
            await MatcherLoader.load_data(hass, candidate, "candidate")
        except Exception as err:  # noqa: BLE001
            raise GuardError("invalid", f"the change cannot be loaded: {err}") from None

        cases = await _load_cases(hass)
        regressions: list = []
        if cases:
            rep_before = await _report(hass, runtime, current, cases)
            rep_after = await _report(hass, runtime, candidate, cases)
            failed_before = {f["index"] for f in rep_before["failed"]}
            regressions = [f for f in rep_after["failed"] if f["index"] not in failed_before]
        if regressions and not force:
            raise GuardError(
                "regression",
                f"{len(regressions)} sentence(s) of the regression corpus would no longer work",
                {"regressions": regressions[:50]},
            )

        await source.async_save(candidate)
        old_matcher = runtime["matcher"]
        from . import async_reload_entry_matcher  # noqa: PLC0415 — avoid import cycle

        await async_reload_entry_matcher(hass, runtime)
        return {
            "saved": True,
            "regressions": regressions[:50],
            "problems": problems,
            "corpus_cases": len(cases),
            "rev": revision(candidate),
            "reloaded": runtime["matcher"] is not old_matcher,
        }
