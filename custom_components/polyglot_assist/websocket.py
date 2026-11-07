"""WebSocket API (admin only) — the backend of the Polyglot panel.

Writes go through guard.py (structure check, regression corpus, lock, fresh
base) and only target the catalog, never a hand-maintained intents.yaml.
"""
from __future__ import annotations

import asyncio
import copy
import re
import time
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent

from .const import (
    CONF_BUILTIN,
    CONF_CHAT_LOG_TOOLS,
    CONF_FALLBACK_AGENT,
    CONF_LANGUAGES_ENABLED,
    CONF_MONITOR,
    CONF_MONITOR_TIME,
    CONF_RESPONSE_MODE,
    CONF_TRANSLATION_AI_TASK,
    DOMAIN,
    SOURCE_CATALOG,
)
from .history import change_summary, get_history, loosened
from .fallback import fallback_error
from .settings import async_language_check, pipelines_overview
from .ui_texts import UiTextError, get_ui_texts, shipped_languages
from .corpus import count_summary, validate
from .guard import GuardError, async_guarded_update, revision, write_lock
from .source import SourceError
from .assistant import async_discover
from .proposal_queue import ProposalQueue
from .actions import CatalogIntentHandler
from .corpus import names_table
from .proposals import QueueError, apply_op, check_item, check_item_against
from .safety import action_target_reason, protected_reason, sensitive_entities
from .translate import (
    ProposalError,
    async_propose_languages,
    async_propose_names,
    async_propose_sentences,
    ai_task_has_tools,
    valid_languages,
)


@callback
def async_register(hass: HomeAssistant) -> None:
    for handler in (
        ws_stats, ws_get, ws_save_intent, ws_delete_intent, ws_test,
        ws_translate, ws_suggest_names,
        ws_proposals_list, ws_proposals_accept, ws_proposals_reject,
        ws_proposals_generate, ws_suggest_languages,
        ws_monitor_status, ws_monitor_run,
        ws_overview, ws_categories, ws_names_set, ws_options, ws_entries,
        ws_history_list, ws_history_diff, ws_history_restore,
        ws_fallback_set, ws_pipelines, ws_languages_check,
        ws_ui_texts_get, ws_ui_texts_languages, ws_ui_texts_set, ws_ui_texts_translate, ws_ui_texts_export,
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


def _actor(connection, what: str) -> str:
    user = getattr(connection, "user", None)
    name = getattr(user, "name", None) if user is not None else None
    return f"{what}:{name}" if name else what


async def _guarded(hass, connection, msg, runtime, mutate, actor: str = "panel") -> None:
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
            hass, runtime, mutate, force=msg.get("force", False), base_rev=msg.get("base_rev"),
            actor=_actor(connection, actor),
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


TOTAL_BUDGET = 180
CALL_BUDGET = 70
LANG_CHECK_BUDGET = 420  # up to 12 languages, 2 at a time, ≤ 60 s each


def _enabled_or_none(hass: HomeAssistant, entry_id: str) -> list[str] | None:
    entry = hass.config_entries.async_get_entry(entry_id)
    return (entry.options.get(CONF_LANGUAGES_ENABLED) if entry else None) or None


def enabled_languages(hass: HomeAssistant, entry_id: str, available: list[str]) -> list[str]:
    entry = hass.config_entries.async_get_entry(entry_id)
    chosen = (entry.options.get(CONF_LANGUAGES_ENABLED) if entry else None) or []
    return [lang for lang in available if not chosen or lang in chosen]


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "polyglot_assist/translate",
        vol.Optional("entry_id"): str,
        vol.Required("name"): str,
        vol.Required("languages"): [str],
        vol.Optional("to_queue", default=False): bool,
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
        connection.send_error(msg["id"], "not_configured", "choose an AI task for proposals in the options")
        return
    matcher = runtime["matcher"]
    config = matcher.config
    intent_cfg = (config.get("intents") or {}).get(msg["name"])
    if not isinstance(intent_cfg, dict):
        connection.send_error(msg["id"], "not_found", "unknown intent")
        return
    languages = valid_languages(msg["languages"], _enabled_or_none(hass, entry_id))
    if not languages:
        connection.send_error(msg["id"], "invalid_language", "no valid or enabled language requested")
        return
    slot_lists = {**matcher.static_slot_lists(), **runtime["lists"].async_get()}
    try:
        # per-call deadlines keep the results that are finished
        proposals = await async_propose_sentences(
            hass,
            ai_task_entity=ai_task,
            intent_name=msg["name"],
            intent_cfg=intent_cfg,
            config=config,
            languages=languages,
            matcher=matcher,
            slot_lists=slot_lists,
            context=connection.context(msg),
            deadline=time.monotonic() + TOTAL_BUDGET,
        )
    except ProposalError as err:
        connection.send_error(msg["id"], "refused", str(err))
        return
    queued = 0
    if msg.get("to_queue"):
        items = [
            {
                "op": "add_sentences",
                "payload": {"intent": msg["name"], "lang": lang, "sentences": [r["sentence"]]},
                "title": f"{msg['name']} ({lang}): {r['sentence']}",
                "origin": {"source": "translation", "detail": f"Übersetzung von {msg['name']} nach {lang}"},
                "check": {"status": r["status"], "notes": r["notes"]},
            }
            for lang, results in proposals.items()
            if isinstance(results, list)
            for r in results
        ]
        queued = len(await _queue(hass, runtime).async_add(items))
    connection.send_result(
        msg["id"],
        {"name": msg["name"], "safety": intent_cfg.get("safety", "normal"), "proposals": proposals,
         "queued": queued},
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
        connection.send_error(msg["id"], "not_configured", "choose an AI task for proposals in the options")
        return
    matcher = runtime["matcher"]
    languages = valid_languages(msg["languages"], _enabled_or_none(hass, entry_id))
    ids = list(dict.fromkeys(msg["ids"]))
    if not languages or not ids:
        connection.send_error(msg["id"], "invalid", "no valid ids or languages")
        return
    if len(ids) * len(languages) > 20:
        connection.send_error(msg["id"], "too_many", "at most 20 id × language combinations per request")
        return
    areas = ar.async_get(hass)
    targets: dict[str, tuple[str, str, str]] = {}
    for target in ids:
        if target.startswith("area."):
            if (area := areas.async_get_area(target[5:])) is not None:
                targets[target] = (area.name, "room", "")
        elif (state := hass.states.get(target)) is not None:
            entry = er.async_get(hass).async_get(target)
            area_id = entry.area_id if entry else None
            area = areas.async_get_area(area_id) if area_id else None
            targets[target] = (state.name, f"{state.domain} device", area.name if area else "")
    slot_lists = {**matcher.static_slot_lists(), **runtime["lists"].async_get()}
    try:
        # per-call deadlines keep the results that are finished
        names = await async_propose_names(
            hass,
            ai_task_entity=ai_task,
            targets=targets,
            languages=languages,
            matcher=matcher,
            slot_lists=slot_lists,
            source_language=hass.config.language,
            context=connection.context(msg),
            deadline=time.monotonic() + TOTAL_BUDGET,
        )
    except ProposalError as err:
        connection.send_error(msg["id"], "refused", str(err))
        return
    connection.send_result(msg["id"], {"proposals": names, "unknown_ids": sorted(set(ids) - set(targets))})


# ── proposal queue ──────────────────────────────────────────────────────────


def _queue(hass: HomeAssistant, runtime: dict) -> ProposalQueue:
    queue = runtime.get("queue")
    if queue is None:
        queue = runtime["queue"] = ProposalQueue(hass, runtime["entry"].entry_id)
    return queue


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): "polyglot_assist/proposals/list", vol.Optional("entry_id"): str}
)
@websocket_api.async_response
async def ws_proposals_list(hass: HomeAssistant, connection, msg) -> None:
    _, runtime = _runtime(hass, connection, msg)
    if runtime is None:
        return
    connection.send_result(msg["id"], {"items": list(await _queue(hass, runtime).async_items())})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "polyglot_assist/proposals/accept",
        vol.Optional("entry_id"): str,
        vol.Required("ids"): [str],
        vol.Optional("force", default=False): bool,
    }
)
@websocket_api.async_response
async def ws_proposals_accept(hass: HomeAssistant, connection, msg) -> None:
    """Apply the selected proposals in ONE guarded write."""
    _, runtime = _runtime(hass, connection, msg)
    if runtime is None:
        return
    queue = _queue(hass, runtime)
    wanted = set(msg["ids"])
    items = [i for i in await queue.async_items() if i["id"] in wanted]
    config = runtime["matcher"].config
    sensitive = sensitive_entities(config)
    extra_names = names_table(config)
    registered = {h.intent_type for h in intent.async_get(hass) if not isinstance(h, CatalogIntentHandler)}
    refused: dict[str, str] = {}
    trial = copy.deepcopy(config)
    usable = []
    for item in items:
        reason = check_item(item) or check_item_against(item, trial) or _dynamic_reason(
            hass, item, sensitive, extra_names, registered
        )
        if not reason and item.get("check", {}).get("status") == "error":
            reason = "; ".join(item.get("check", {}).get("notes", [])[:1]) or "has errors"
        if not reason:
            try:
                apply_op(trial, item)  # stale items are refused, the rest goes on
            except QueueError as err:
                reason = str(err)
        if reason:
            refused[item["id"]] = reason
        else:
            usable.append(item)
    if not usable:
        connection.send_result(msg["id"], {"saved": False, "code": "nothing_usable", "refused": refused})
        return

    def _mutate(data: dict) -> None:
        for item in usable:
            # repeat document checks on the FRESH document inside the write lock
            reason = check_item_against(item, data)
            if reason:
                raise GuardError("stale", f"{item.get('title', item['id'])}: {reason}")
            try:
                apply_op(data, item)
            except QueueError as err:
                raise GuardError("stale", f"{item.get('title', item['id'])}: {err}") from None

    msg = {**msg, "force": msg.get("force", False)}
    if runtime["source"].kind != SOURCE_CATALOG:
        connection.send_error(
            msg["id"], "yaml_source_read_only",
            "proposals are applied to the catalog — switch the intents source to the catalog first",
        )
        return
    try:
        result = await async_guarded_update(hass, runtime, _mutate, force=msg["force"], actor=_actor(connection, "proposals"))
    except GuardError as err:
        connection.send_result(msg["id"], {"saved": False, "code": err.code, "message": str(err), "refused": refused, **err.details})
        return
    await queue.async_remove([i["id"] for i in usable])
    connection.send_result(msg["id"], {**result, "applied": [i["id"] for i in usable], "refused": refused})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "polyglot_assist/proposals/reject",
        vol.Optional("entry_id"): str,
        vol.Required("ids"): [str],
    }
)
@websocket_api.async_response
async def ws_proposals_reject(hass: HomeAssistant, connection, msg) -> None:
    _, runtime = _runtime(hass, connection, msg)
    if runtime is None:
        return
    removed = await _queue(hass, runtime).async_reject(msg["ids"])
    connection.send_result(msg["id"], {"removed": removed})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "polyglot_assist/proposals/generate",
        vol.Optional("entry_id"): str,
        vol.Required("what"): vol.In(["starter", "new_devices"]),
    }
)
@websocket_api.async_response
async def ws_proposals_generate(hass: HomeAssistant, connection, msg) -> None:
    """Starter set or new devices → proposals in the queue (nothing applied)."""
    entry_id, runtime = _runtime(hass, connection, msg)
    if runtime is None:
        return
    ai_task = _ai_task_entity(hass, entry_id)
    if not ai_task:
        connection.send_error(msg["id"], "not_configured", "choose an AI task for proposals in the options")
        return
    languages = enabled_languages(hass, entry_id, runtime["matcher"].languages)
    try:
        found = await async_discover(
            hass, runtime, ai_task=ai_task, languages=languages,
            only_new=msg["what"] == "new_devices", context=connection.context(msg),
            deadline=time.monotonic() + TOTAL_BUDGET * 2,
        )
    except ProposalError as err:
        connection.send_error(msg["id"], "refused", str(err))
        return
    added = await _queue(hass, runtime).async_add(found["items"])
    connection.send_result(msg["id"], {"added": len(added), "errors": found["errors"]})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): "polyglot_assist/suggest_languages", vol.Optional("entry_id"): str}
)
@websocket_api.async_response
async def ws_suggest_languages(hass: HomeAssistant, connection, msg) -> None:
    entry_id, runtime = _runtime(hass, connection, msg)
    if runtime is None:
        return
    ai_task = _ai_task_entity(hass, entry_id)
    if not ai_task:
        connection.send_error(msg["id"], "not_configured", "choose an AI task for proposals in the options")
        return
    try:
        async with asyncio.timeout(CALL_BUDGET):
            langs = await async_propose_languages(hass, ai_task, connection.context(msg))
    except (ProposalError, TimeoutError) as err:
        connection.send_error(msg["id"], "failed", str(err) or "timeout")
        return
    connection.send_result(msg["id"], {"languages": langs})



@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "polyglot_assist/monitor/status",
        vol.Optional("entry_id"): str,
        vol.Optional("limit", default=50): vol.All(int, vol.Range(min=1, max=500)),
    }
)
@callback
def ws_monitor_status(hass: HomeAssistant, connection, msg) -> None:
    _, runtime = _runtime(hass, connection, msg)
    if runtime is None:
        return
    monitor = runtime.get("monitor")
    if monitor is None:
        connection.send_result(msg["id"], {"enabled": False})
        return
    connection.send_result(
        msg["id"],
        {"enabled": True, "summary": monitor.summary, "today": monitor.stats(monitor.window(24)),
         "log": list(reversed(monitor.log))[: msg["limit"]]},
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): "polyglot_assist/monitor/run", vol.Optional("entry_id"): str}
)
@websocket_api.async_response
async def ws_monitor_run(hass: HomeAssistant, connection, msg) -> None:
    _, runtime = _runtime(hass, connection, msg)
    if runtime is None:
        return
    if runtime.get("monitor") is None:
        connection.send_error(msg["id"], "disabled", "monitoring is switched off")
        return
    from . import async_run_monitor  # noqa: PLC0415

    try:
        async with asyncio.timeout(TOTAL_BUDGET):
            summary = await async_run_monitor(hass, runtime)
    except (ProposalError, TimeoutError) as err:
        connection.send_error(msg["id"], "failed", str(err) or "timeout")
        return
    connection.send_result(msg["id"], {"summary": summary})


# ── panel helpers: per-command meta, categories, names, options ──────────────


def intent_meta(hass: HomeAssistant, config: dict, hits: dict[str, int]) -> dict[str, Any]:
    """Category, kind, safety, rooms and devices per command (for grouping)."""
    ents = er.async_get(hass)
    devs = dr.async_get(hass)
    areas = {a.id: a.name for a in ar.async_get(hass).async_list_areas()}
    static = {k: v for k, v in (config.get("lists") or {}).items() if isinstance(v, dict) and isinstance(v.get("values"), list)}
    out: dict[str, Any] = {}
    for name, cfg in (config.get("intents") or {}).items():
        if not isinstance(cfg, dict):
            continue
        sentences = cfg.get("sentences") or {}
        texts = [s for lst in sentences.values() if isinstance(lst, list) for s in lst if isinstance(s, str)]
        refs = {m for t in texts for m in re.findall(r"(?<!\{)\{([^{}:]+)", t)}
        targets: set[str] = set()
        for ref in refs:
            for value in static.get(ref.strip(), {}).get("values", []):
                out_value = value.get("out") if isinstance(value, dict) else None
                if isinstance(out_value, str) and "." in out_value:
                    targets.add(out_value)
        for step in cfg.get("action") or []:
            entity = ((step or {}).get("target") or {}).get("entity_id") if isinstance(step, dict) else None
            if isinstance(entity, str):
                targets.add(entity)
        room_names, device_names = set(), set()
        for entity_id in targets:
            entry = ents.async_get(entity_id)
            area_id = entry.area_id if entry else None
            if area_id is None and entry is not None and entry.device_id:
                device = devs.async_get(entry.device_id)
                area_id = device.area_id if device else None
            if area_id in areas:
                room_names.add(areas[area_id])
            state = hass.states.get(entity_id)
            device_names.add(state.name if state else entity_id)
        out[name] = {
            "description": cfg.get("description", ""),
            "category": cfg.get("category"),
            "kind": cfg.get("kind"),
            "safety": cfg.get("safety", "normal"),
            "response_mode": cfg.get("response_mode"),
            "has_action": bool(cfg.get("action")),
            "sentences": {lang: len(lst) for lang, lst in sentences.items() if isinstance(lst, list)},
            "rooms": sorted(room_names),
            "devices": sorted(device_names)[:8],
            "hits": hits.get(name, 0),
        }
    return out


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): "polyglot_assist/overview", vol.Optional("entry_id"): str}
)
@callback
def ws_overview(hass: HomeAssistant, connection, msg) -> None:
    """Everything the panel's start page and command list need in one call."""
    entry_id, runtime = _runtime(hass, connection, msg)
    if runtime is None:
        return
    entry = runtime["entry"]
    config = runtime["matcher"].config
    hits = dict(runtime.get("hits") or {})
    connection.send_result(
        msg["id"],
        {
            "entry_id": entry_id,
            "title": entry.title,
            "rev": revision(config),
            "source": runtime["source"].kind,
            "languages": runtime["matcher"].languages,
            "builtin_languages": runtime["builtin"].loaded if runtime.get("builtin") else [],
            "summary": count_summary(config),
            "problems": validate(config, extra_lists=runtime["lists"].names)[:50],
            "categories": list(config.get("categories") or []),
            "intents": intent_meta(hass, config, hits),
            "options": {k: v for k, v in entry.options.items() if k in PANEL_OPTIONS},
            "fallback_agent": runtime.get("fallback_agent"),
            "monitor": runtime.get("monitor") is not None,
        },
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "polyglot_assist/categories",
        vol.Optional("entry_id"): str,
        vol.Required("categories"): [vol.All(str, vol.Length(min=1, max=40))],
        vol.Optional("renames", default={}): {str: vol.All(str, vol.Length(min=1, max=40))},
        vol.Optional("base_rev"): str,
    }
)
@websocket_api.async_response
async def ws_categories(hass: HomeAssistant, connection, msg) -> None:
    """Set the category list; renamed/deleted categories update the commands."""
    _, runtime = _runtime(hass, connection, msg)
    if runtime is None:
        return
    wanted = list(dict.fromkeys(c.strip() for c in msg["categories"] if c.strip()))
    renames = {k: v.strip() for k, v in msg["renames"].items()}

    def _mutate(data: dict) -> None:
        data["categories"] = wanted
        for cfg in (data.get("intents") or {}).values():
            if not isinstance(cfg, dict) or not cfg.get("category"):
                continue
            cat = renames.get(cfg["category"], cfg["category"])
            if cat in wanted:
                cfg["category"] = cat
            else:
                cfg.pop("category", None)  # deleted → "Sonstiges"

    await _guarded(hass, connection, msg, runtime, _mutate)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "polyglot_assist/names/set",
        vol.Optional("entry_id"): str,
        vol.Required("target"): vol.Match(r"^[a-z_]+\.[a-z0-9_]+$"),
        vol.Required("lang"): vol.Match(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$"),
        vol.Required("names"): [vol.All(str, vol.Length(min=1, max=40))],
        vol.Optional("base_rev"): str,
    }
)
@websocket_api.async_response
async def ws_names_set(hass: HomeAssistant, connection, msg) -> None:
    _, runtime = _runtime(hass, connection, msg)
    if runtime is None:
        return
    names = list(dict.fromkeys(n.strip() for n in msg["names"] if n.strip()))

    def _mutate(data: dict) -> None:
        per_lang = data.setdefault("names", {}).setdefault(msg["target"], {})
        if names:
            per_lang[msg["lang"]] = names
        else:
            per_lang.pop(msg["lang"], None)
            if not per_lang:
                data["names"].pop(msg["target"], None)

    await _guarded(hass, connection, msg, runtime, _mutate)


PANEL_OPTIONS = (
    CONF_LANGUAGES_ENABLED, CONF_RESPONSE_MODE, CONF_MONITOR, CONF_MONITOR_TIME,
    CONF_TRANSLATION_AI_TASK, CONF_CHAT_LOG_TOOLS, CONF_BUILTIN,
)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "polyglot_assist/options",
        vol.Optional("entry_id"): str,
        vol.Optional(CONF_LANGUAGES_ENABLED): [vol.Match(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")],
        vol.Optional(CONF_RESPONSE_MODE): vol.In(["text", "chime"]),
        vol.Optional(CONF_MONITOR): bool,
        vol.Optional(CONF_MONITOR_TIME): vol.Match(r"^\d{2}:\d{2}$"),
        vol.Optional(CONF_TRANSLATION_AI_TASK): vol.Any(None, vol.Match(r"^ai_task\.[a-z0-9_]+$")),
        vol.Optional(CONF_CHAT_LOG_TOOLS): bool,
        vol.Optional(CONF_BUILTIN): bool,
    }
)
@websocket_api.async_response
async def ws_options(hass: HomeAssistant, connection, msg) -> None:
    """Change panel settings (same options as the options flow)."""
    entry_id, runtime = _runtime(hass, connection, msg)
    if runtime is None:
        return
    entry = runtime["entry"]
    changes = {k: msg[k] for k in PANEL_OPTIONS if k in msg}
    ai_task = changes.get(CONF_TRANSLATION_AI_TASK)
    if ai_task and ai_task_has_tools(hass, ai_task):
        connection.send_error(msg["id"], "ai_task_has_tools", "this AI task has tools — choose one without tools")
        return
    new_options = {**entry.options, **changes}
    if CONF_TRANSLATION_AI_TASK in changes and not ai_task:
        new_options.pop(CONF_TRANSLATION_AI_TASK, None)
    hass.config_entries.async_update_entry(entry, options=new_options)
    if any(k in changes for k in (CONF_LANGUAGES_ENABLED, CONF_MONITOR, CONF_MONITOR_TIME, CONF_CHAT_LOG_TOOLS, CONF_BUILTIN)):
        hass.async_create_task(hass.config_entries.async_reload(entry_id))
    connection.send_result(msg["id"], {"options": {k: v for k, v in new_options.items() if k in PANEL_OPTIONS}})



def _dynamic_reason(hass: HomeAssistant, item: dict, sensitive: set[str], extra_names: dict, registered: set[str]) -> str | None:
    """Checks against the real installation before a proposal may be applied."""
    op, p = item.get("op"), item.get("payload") or {}
    if op == "add_intent":
        if p.get("name") in registered:
            return "a command with this name already exists (e.g. intent_script)"
        for step in (p.get("intent") or {}).get("action") or []:
            entity = ((step or {}).get("target") or {}).get("entity_id")
            if isinstance(entity, str):
                reason = action_target_reason(hass, entity, sensitive=sensitive, extra_names=extra_names)
                if reason:
                    return reason
    if op == "set_list_value" and isinstance(p.get("out"), str) and "." in p["out"]:
        return protected_reason(hass, p["out"], sensitive_entities=sensitive, extra_names=extra_names)
    if op in ("add_names", "remove_names") and not str(p.get("target", "")).startswith("area."):
        return protected_reason(hass, p.get("target", ""), sensitive_entities=sensitive, extra_names=extra_names)
    return None



@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "polyglot_assist/entries"})
@callback
def ws_entries(hass: HomeAssistant, connection, msg) -> None:
    """Loaded Polyglot entries (the panel lets the user pick one if several)."""
    connection.send_result(
        msg["id"],
        {"entries": [{"entry_id": eid, "title": data["entry"].title} for eid, data in hass.data.get(DOMAIN, {}).items()]},
    )


def _history(runtime: dict[str, Any]):
    return get_history(runtime["source"].hass, runtime["entry"].entry_id)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): "polyglot_assist/history/list", vol.Optional("entry_id"): str}
)
@websocket_api.async_response
async def ws_history_list(hass: HomeAssistant, connection, msg) -> None:
    """Earlier states of the catalog (newest first, without their content)."""
    _entry_id, runtime = _runtime(hass, connection, msg)
    if runtime is None:
        return
    if runtime["source"].kind != SOURCE_CATALOG:
        connection.send_error(msg["id"], "yaml_source_read_only", "the history belongs to the catalog — the active source is intents.yaml")
        return
    connection.send_result(msg["id"], {"items": await _history(runtime).async_list()})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): "polyglot_assist/history/diff", vol.Optional("entry_id"): str, vol.Required("rev"): str}
)
@websocket_api.async_response
async def ws_history_diff(hass: HomeAssistant, connection, msg) -> None:
    """What a restore of ``rev`` would change compared with now (preview)."""
    _entry_id, runtime = _runtime(hass, connection, msg)
    if runtime is None:
        return
    old = await _history(runtime).async_get(msg["rev"])
    if old is None:
        connection.send_error(msg["id"], "not_found", "this state is no longer in the history")
        return
    current = await runtime["source"].async_load()
    connection.send_result(msg["id"], {
        "summary": change_summary(current, old), "loosened": loosened(current, old, hass), "base_rev": revision(current),
    })


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "polyglot_assist/history/restore",
        vol.Optional("entry_id"): str,
        vol.Required("rev"): str,
        vol.Optional("base_rev"): str,
        vol.Optional("force", default=False): bool,
        vol.Optional("confirm_loosened", default=False): bool,
    }
)
@websocket_api.async_response
async def ws_history_restore(hass: HomeAssistant, connection, msg) -> None:
    """Go back to an earlier state — a normal guarded save (can be undone again)."""
    _entry_id, runtime = _runtime(hass, connection, msg)
    if runtime is None:
        return
    old = await _history(runtime).async_get(msg["rev"])
    if old is None:
        connection.send_error(msg["id"], "not_found", "this state is no longer in the history")
        return

    def _mutate(data: dict[str, Any]) -> None:
        # checked against the CURRENT state inside the write lock
        weaker = loosened(data, old, hass)
        if weaker and not msg["confirm_loosened"]:
            raise GuardError(
                "confirm_loosened",
                f"{len(weaker)} command(s) would be less protected than now — confirm explicitly",
                {"loosened": weaker},
            )
        data.clear()
        data.update(copy.deepcopy(old))

    await _guarded(hass, connection, msg, runtime, _mutate, actor=f"restore {msg['rev'][:8]}")


# ── settings: AI assistant, voice assistants, languages ─────────────────


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): "polyglot_assist/fallback/set", vol.Optional("entry_id"): str, vol.Required("agent_id"): str}
)
@websocket_api.async_response
async def ws_fallback_set(hass: HomeAssistant, connection, msg) -> None:
    """Same setting as the options flow (stored in the entry data), then reload."""
    from homeassistant.components import conversation  # noqa: PLC0415

    entry_id, runtime = _runtime(hass, connection, msg)
    if runtime is None:
        return
    agent_id = msg["agent_id"]
    if err := fallback_error(hass, agent_id):
        connection.send_error(msg["id"], err, "Polyglot cannot be its own fallback (endless loop)" if err == "fallback_is_polyglot" else "choose an agent")
        return
    if conversation.async_get_agent(hass, agent_id) is None:
        connection.send_error(msg["id"], "not_found", "this conversation agent does not exist")
        return
    entry = runtime["entry"]
    old_data = dict(entry.data)
    async with write_lock(runtime):  # no guarded save may run across the reload
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_FALLBACK_AGENT: agent_id})
        try:
            ok = await hass.config_entries.async_reload(entry_id)
        except Exception as err:  # noqa: BLE001
            ok, why = False, str(err)
        else:
            why = "the integration did not load again"
        if not ok:
            hass.config_entries.async_update_entry(entry, data=old_data)
            await hass.config_entries.async_reload(entry_id)
            connection.send_error(msg["id"], "reload_failed", f"not changed: {why}")
            return
    connection.send_result(msg["id"], {"fallback_agent": agent_id})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "polyglot_assist/pipelines"})
@callback
def ws_pipelines(hass: HomeAssistant, connection, msg) -> None:
    """Voice assistants (read only): is Polyglot active, prefer local, STT/TTS, devices."""
    connection.send_result(msg["id"], pipelines_overview(hass))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "polyglot_assist/languages/check",
        vol.Optional("entry_id"): str,
        vol.Optional("pipeline_id"): str,
        vol.Required("languages"): [vol.Match(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")],
    }
)
@websocket_api.async_response
async def ws_languages_check(hass: HomeAssistant, connection, msg) -> None:
    """Which of these languages work end to end (STT, AI, Polyglot, TTS)."""
    entry_id, runtime = _runtime(hass, connection, msg)
    if runtime is None:
        return
    config = runtime["matcher"].config if runtime.get("matcher") else {}
    polyglot = {
        "sentences": count_summary(config).get("sentences", {}),
        "builtin": runtime["builtin"].loaded if runtime.get("builtin") else [],
    }
    try:
        async with asyncio.timeout(LANG_CHECK_BUDGET):
            result = await async_language_check(
                hass, pipeline_id=msg.get("pipeline_id"), languages=msg["languages"], polyglot=polyglot,
                ai_task=_ai_task_entity(hass, entry_id), context=connection.context(msg),
            )
    except TimeoutError:
        connection.send_error(msg["id"], "timeout", "the check took too long")
        return
    except Exception as err:  # noqa: BLE001 — e.g. unknown pipeline
        connection.send_error(msg["id"], "failed", str(err)[:200])
        return
    connection.send_result(msg["id"], result)


# ── interface texts (panel translations) ────────────────────────────────

_LANG = vol.Match(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "polyglot_assist/ui_texts/get", vol.Required("lang"): _LANG})
@websocket_api.async_response
async def ws_ui_texts_get(hass: HomeAssistant, connection, msg) -> None:
    connection.send_result(msg["id"], await get_ui_texts(hass).async_get(msg["lang"]))


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "polyglot_assist/ui_texts/languages"})
@websocket_api.async_response
async def ws_ui_texts_languages(hass: HomeAssistant, connection, msg) -> None:
    shipped = await hass.async_add_executor_job(shipped_languages)
    connection.send_result(msg["id"], {"shipped": shipped, "languages": await get_ui_texts(hass).async_languages()})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): "polyglot_assist/ui_texts/set", vol.Required("lang"): _LANG,
     vol.Required("key"): vol.All(str, vol.Length(max=120)), vol.Required("text"): vol.Any(None, str)}
)
@websocket_api.async_response
async def ws_ui_texts_set(hass: HomeAssistant, connection, msg) -> None:
    try:
        await get_ui_texts(hass).async_set(msg["lang"], msg["key"], msg["text"])
    except UiTextError as err:
        connection.send_error(msg["id"], "invalid", str(err))
        return
    connection.send_result(msg["id"], {"saved": True})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): "polyglot_assist/ui_texts/translate", vol.Optional("entry_id"): str, vol.Required("lang"): _LANG}
)
@websocket_api.async_response
async def ws_ui_texts_translate(hass: HomeAssistant, connection, msg) -> None:
    entry_id, runtime = _runtime(hass, connection, msg)
    if runtime is None:
        return
    try:
        result = await get_ui_texts(hass).async_translate(msg["lang"], _ai_task_entity(hass, entry_id) or "", connection.context(msg))
    except UiTextError as err:
        connection.send_error(msg["id"], "invalid", str(err))
        return
    connection.send_result(msg["id"], result)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "polyglot_assist/ui_texts/export", vol.Required("lang"): _LANG})
@websocket_api.async_response
async def ws_ui_texts_export(hass: HomeAssistant, connection, msg) -> None:
    """A complete translation file (nested, like the shipped ones) for a pull request."""
    store = get_ui_texts(hass)
    english = await store.async_english()
    own = (await store.async_get(msg["lang"]))["texts"]
    nested: dict[str, Any] = {}
    missing = []
    for key in english:
        text = own.get(key)
        if text is None:
            missing.append(key)
            continue
        node = nested
        *path, leaf = key.split(".")
        for part in path:
            node = node.setdefault(part, {})
        node[leaf] = text
    connection.send_result(msg["id"], {"file": f"{msg['lang']}.json", "content": nested, "missing": missing})
