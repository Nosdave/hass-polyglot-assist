"""WebSocket API (admin only) — the backend of the Polyglot panel.

Every write goes through a regression guard: the candidate intents are built
in memory and the regression corpus (``polyglot_assist/tests.yaml`` if present)
is run against them; a save that makes a previously passing case fail is
refused unless ``force`` is set.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import voluptuous as vol
import yaml

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DEFAULT_TESTS_FILE, DOMAIN
from .corpus import count_summary, parse_corpus, run_corpus, validate
from .loader import MatcherLoader


@callback
def async_register(hass: HomeAssistant) -> None:
    for handler in (ws_stats, ws_get, ws_save_intent, ws_delete_intent, ws_test):
        websocket_api.async_register_command(hass, handler)


def _runtime(
    hass: HomeAssistant, connection, msg
) -> tuple[str, dict[str, Any]] | tuple[None, None]:
    entries = hass.data.get(DOMAIN, {})
    entry_id = msg.get("entry_id")
    if entry_id is None and len(entries) == 1:
        entry_id = next(iter(entries))
    if entry_id not in entries:
        connection.send_error(msg["id"], "not_found", "unknown or ambiguous entry_id")
        return None, None
    return entry_id, entries[entry_id]


async def _load_cases(hass: HomeAssistant) -> list:
    path = Path(hass.config.path(DEFAULT_TESTS_FILE))

    def _read() -> list:
        if not path.is_file():
            return []
        return parse_corpus(yaml.safe_load(path.read_text("utf-8")) or {})

    try:
        return await hass.async_add_executor_job(_read)
    except (ValueError, yaml.YAMLError, UnicodeDecodeError):
        return []


async def _corpus_report(hass: HomeAssistant, runtime: dict[str, Any], data: dict, cases: list):
    matcher = await MatcherLoader.load_data(hass, data, "candidate")
    slot_lists = runtime["lists"].async_get()

    def _match(text: str, hint: str | None):
        res = matcher.match(text, hint, slot_lists)
        if res is None:
            return None
        return res.intent_name, res.lang, {k: v.get("value") for k, v in res.slots.items()}

    # same hint as a pipeline in the installation's language
    return await hass.async_add_executor_job(
        run_corpus, _match, cases, hass.config.language
    )


async def _guarded_save(hass, connection, msg, runtime, candidate: dict) -> None:
    problems = validate(candidate, extra_lists=runtime["lists"].names)
    cases = await _load_cases(hass)
    regressions: list = []
    if cases:
        before = await _corpus_report(hass, runtime, runtime["matcher"].config, cases)
        after = await _corpus_report(hass, runtime, candidate, cases)
        failed_before = {f["text"] for f in before["failed"]}
        regressions = [f for f in after["failed"] if f["text"] not in failed_before]
    if regressions and not msg.get("force"):
        connection.send_result(
            msg["id"],
            {"saved": False, "regressions": regressions[:50], "problems": problems},
        )
        return
    await runtime["source"].async_save(candidate)
    await hass.services.async_call(DOMAIN, "reload", {}, blocking=True)
    connection.send_result(
        msg["id"],
        {"saved": True, "regressions": regressions[:50], "problems": problems,
         "corpus_cases": len(cases)},
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): "polyglot_assist/stats", vol.Optional("entry_id"): str}
)
@callback
def ws_stats(hass: HomeAssistant, connection, msg) -> None:
    entry_id, runtime = _runtime(hass, connection, msg)
    if runtime is None:
        return
    config = runtime["matcher"].config
    connection.send_result(
        msg["id"],
        {
            "entry_id": entry_id,
            "source": runtime["source"].kind,
            "summary": count_summary(config),
            "problems": validate(config, extra_lists=runtime["lists"].names),
            "runtime_lists": sorted(runtime["lists"].names),
        },
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): "polyglot_assist/get", vol.Optional("entry_id"): str}
)
@callback
def ws_get(hass: HomeAssistant, connection, msg) -> None:
    _, runtime = _runtime(hass, connection, msg)
    if runtime is None:
        return
    connection.send_result(msg["id"], copy.deepcopy(runtime["matcher"].config))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "polyglot_assist/save_intent",
        vol.Optional("entry_id"): str,
        vol.Required("name"): vol.All(str, vol.Length(min=1, max=80)),
        vol.Required("intent"): dict,
        vol.Optional("rename_from"): str,
        vol.Optional("force", default=False): bool,
    }
)
@websocket_api.async_response
async def ws_save_intent(hass: HomeAssistant, connection, msg) -> None:
    _, runtime = _runtime(hass, connection, msg)
    if runtime is None:
        return
    candidate = copy.deepcopy(runtime["matcher"].config)
    intents = candidate.setdefault("intents", {})
    if msg.get("rename_from") and msg["rename_from"] != msg["name"]:
        intents.pop(msg["rename_from"], None)
    intents[msg["name"]] = msg["intent"]
    await _guarded_save(hass, connection, msg, runtime, candidate)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "polyglot_assist/delete_intent",
        vol.Optional("entry_id"): str,
        vol.Required("name"): str,
        vol.Optional("force", default=False): bool,
    }
)
@websocket_api.async_response
async def ws_delete_intent(hass: HomeAssistant, connection, msg) -> None:
    _, runtime = _runtime(hass, connection, msg)
    if runtime is None:
        return
    candidate = copy.deepcopy(runtime["matcher"].config)
    if msg["name"] not in (candidate.get("intents") or {}):
        connection.send_error(msg["id"], "not_found", "unknown intent")
        return
    del candidate["intents"][msg["name"]]
    await _guarded_save(hass, connection, msg, runtime, candidate)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "polyglot_assist/test",
        vol.Optional("entry_id"): str,
        vol.Required("text"): str,
        vol.Optional("language"): str,
    }
)
@websocket_api.async_response
async def ws_test(hass: HomeAssistant, connection, msg) -> None:
    _, runtime = _runtime(hass, connection, msg)
    if runtime is None:
        return
    slot_lists = runtime["lists"].async_get()
    res = await hass.async_add_executor_job(
        runtime["matcher"].match, msg["text"], msg.get("language"), slot_lists
    )
    connection.send_result(
        msg["id"],
        None
        if res is None
        else {
            "intent": res.intent_name,
            "lang": res.lang,
            "slots": res.slots,
            "sentence": res.matched_sentence,
        },
    )
