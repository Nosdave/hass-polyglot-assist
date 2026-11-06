"""WebSocket API (admin only) — the backend of the Polyglot panel.

Writes go through guard.py (structure check, regression corpus, lock, fresh
base) and only target the catalog, never a hand-maintained intents.yaml.
"""
from __future__ import annotations

import copy
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar

from .const import CONF_TRANSLATION_AI_TASK, DOMAIN, SOURCE_CATALOG
from .corpus import count_summary, validate
from .guard import GuardError, async_guarded_update, revision
from .source import SourceError
from .translate import async_propose_names, async_propose_sentences


@callback
def async_register(hass: HomeAssistant) -> None:
    for handler in (
        ws_stats, ws_get, ws_save_intent, ws_delete_intent, ws_test,
        ws_translate, ws_suggest_names,
    ):
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


NAME_RE = r"^[A-Za-z][A-Za-z0-9_]{0,79}$"


async def _guarded(hass, connection, msg, runtime, mutate) -> None:
    if runtime["source"].kind != SOURCE_CATALOG:
        connection.send_error(
            msg["id"],
            "yaml_source_read_only",
            "the panel edits the catalog — switch the intents source to the catalog first "
            "(intents.yaml stays untouched as your backup)",
        )
        return
    try:
        result = await async_guarded_update(
            hass, runtime, mutate, force=msg.get("force", False), base_rev=msg.get("base_rev")
        )
    except GuardError as err:
        connection.send_result(
            msg["id"], {"saved": False, "code": err.code, "message": str(err), **err.details}
        )
        return
    except SourceError as err:
        connection.send_result(msg["id"], {"saved": False, "code": "write_failed", "message": str(err)})
        return
    connection.send_result(msg["id"], result)


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
    config = runtime["matcher"].config
    connection.send_result(msg["id"], {"rev": revision(config), "data": copy.deepcopy(config)})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "polyglot_assist/save_intent",
        vol.Optional("entry_id"): str,
        vol.Required("name"): vol.Match(NAME_RE),
        vol.Required("intent"): dict,
        vol.Optional("rename_from"): str,
        vol.Optional("base_rev"): str,
        vol.Optional("force", default=False): bool,
    }
)
@websocket_api.async_response
async def ws_save_intent(hass: HomeAssistant, connection, msg) -> None:
    _, runtime = _runtime(hass, connection, msg)
    if runtime is None:
        return
    name, old = msg["name"], msg.get("rename_from")

    def _mutate(data: dict) -> None:
        intents = data.setdefault("intents", {})
        if old and old != name:
            if name in intents:
                raise GuardError("exists", f"an intent named {name} already exists")
            # keep the position of the renamed intent (order = match priority)
            data["intents"] = {
                (name if k == old else k): (msg["intent"] if k == old else v)
                for k, v in intents.items()
            }
            if old not in intents:
                data["intents"][name] = msg["intent"]
        else:
            intents[name] = msg["intent"]

    await _guarded(hass, connection, msg, runtime, _mutate)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "polyglot_assist/delete_intent",
        vol.Optional("entry_id"): str,
        vol.Required("name"): str,
        vol.Optional("base_rev"): str,
        vol.Optional("force", default=False): bool,
    }
)
@websocket_api.async_response
async def ws_delete_intent(hass: HomeAssistant, connection, msg) -> None:
    _, runtime = _runtime(hass, connection, msg)
    if runtime is None:
        return

    def _mutate(data: dict) -> None:
        if msg["name"] not in (data.get("intents") or {}):
            raise GuardError("not_found", "unknown intent")
        del data["intents"][msg["name"]]

    await _guarded(hass, connection, msg, runtime, _mutate)


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


def _ai_task_entity(hass: HomeAssistant, entry_id: str) -> str | None:
    entry = hass.config_entries.async_get_entry(entry_id)
    return entry.options.get(CONF_TRANSLATION_AI_TASK) if entry else None


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "polyglot_assist/translate",
        vol.Optional("entry_id"): str,
        vol.Required("name"): str,
        vol.Required("languages"): [str],
    }
)
@websocket_api.async_response
async def ws_translate(hass: HomeAssistant, connection, msg) -> None:
    """Validated sentence PROPOSALS for other languages — nothing is saved."""
    entry_id, runtime = _runtime(hass, connection, msg)
    if runtime is None:
        return
    ai_task = _ai_task_entity(hass, entry_id)
    if not ai_task:
        connection.send_error(msg["id"], "not_configured", "set the translation AI task in the options")
        return
    config = runtime["matcher"].config
    intent_cfg = (config.get("intents") or {}).get(msg["name"])
    if not isinstance(intent_cfg, dict):
        connection.send_error(msg["id"], "not_found", "unknown intent")
        return
    matcher = runtime["matcher"]
    slot_lists = {**matcher.static_slot_lists(), **runtime["lists"].async_get()}

    def _match(text: str):
        res = matcher.match(text, None, slot_lists)
        return res and (res.intent_name, res.lang, {})

    try:
        proposals = await async_propose_sentences(
            hass,
            ai_task_entity=ai_task,
            intent_name=msg["name"],
            intent_cfg=intent_cfg,
            config=config,
            languages=msg["languages"],
            match_fn=_match,
            slot_lists=slot_lists,
        )
    except Exception as err:  # noqa: BLE001 — report, never break the panel
        connection.send_error(msg["id"], "translation_failed", str(err)[:300])
        return
    connection.send_result(
        msg["id"],
        {"name": msg["name"], "safety": intent_cfg.get("safety", "normal"), "proposals": proposals},
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "polyglot_assist/suggest_names",
        vol.Optional("entry_id"): str,
        vol.Required("ids"): [str],
        vol.Required("languages"): [str],
    }
)
@websocket_api.async_response
async def ws_suggest_names(hass: HomeAssistant, connection, msg) -> None:
    """Name-table PROPOSALS for entities / areas — nothing is saved."""
    entry_id, runtime = _runtime(hass, connection, msg)
    if runtime is None:
        return
    ai_task = _ai_task_entity(hass, entry_id)
    if not ai_task:
        connection.send_error(msg["id"], "not_configured", "set the translation AI task in the options")
        return
    targets: dict[str, str] = {}
    for target in msg["ids"][:20]:
        if target.startswith("area."):
            area = ar.async_get(hass).async_get_area(target[5:])
            if area:
                targets[target] = area.name
        elif (state := hass.states.get(target)) is not None:
            targets[target] = state.name
    try:
        names = await async_propose_names(
            hass, ai_task_entity=ai_task, targets=targets, languages=msg["languages"]
        )
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "translation_failed", str(err)[:300])
        return
    connection.send_result(msg["id"], {"proposals": names})
