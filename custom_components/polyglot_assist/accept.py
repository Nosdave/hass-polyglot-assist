"""Accepting queued proposals — ONE path for the panel and for voice.

Every accepted item is checked statically, against the current document and
against the real installation, applied to a trial copy, and finally written
in a single guarded save (structure, regression corpus, write lock).
Voice adds its own, stricter allow-list on top (``extra_reason``).
"""
from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent

from .actions import CatalogIntentHandler
from .const import SOURCE_CATALOG
from .corpus import names_table
from .guard import GuardError, async_guarded_update
from .proposal_queue import ProposalQueue
from .proposals import QueueError, apply_op, check_item, check_item_against
from .safety import action_target_reason, protected_reason, sensitive_entities


class AcceptError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def queue_of(hass: HomeAssistant, runtime: dict) -> ProposalQueue:
    queue = runtime.get("queue")
    if queue is None:
        queue = runtime["queue"] = ProposalQueue(hass, runtime["entry"].entry_id)
    return queue


def dynamic_reason(hass: HomeAssistant, item: dict, sensitive: set[str], extra_names: dict, registered: set[str]) -> str | None:
    """Checks against the real installation before a proposal may be applied."""
    op, p = item.get("op"), item.get("payload") or {}
    if op == "add_intent":
        if p.get("name") in registered:
            return "a command with this name already exists (e.g. intent_script)"
        from homeassistant.components.homeassistant.exposed_entities import async_should_expose  # noqa: PLC0415

        from .proposals import action_entity_reason  # noqa: PLC0415

        # commands derived from a correction may only switch what Assist may switch
        needs_exposure = (item.get("origin") or {}).get("source") == "correction"

        def _reason(entity: str) -> str | None:
            reason = action_target_reason(hass, entity, sensitive=sensitive, extra_names=extra_names)
            if reason or not needs_exposure or async_should_expose(hass, "conversation", entity):
                return reason
            return f"{entity} is not exposed to Assist"

        reason = action_entity_reason((p.get("intent") or {}).get("action") or [], _reason)
        if reason:
            return reason
        # what an ANSWER reads must be readable by Assist too (and never a protected device)
        from .answer import AnswerError, blocks_entities  # noqa: PLC0415

        try:
            read = blocks_entities(p.get("intent") or {})
        except AnswerError as err:
            return f"answer: {err}"
        for entity in read:
            if not async_should_expose(hass, "conversation", entity):
                return f"the answer reads {entity}, which is not exposed to Assist"
            reason = protected_reason(hass, entity, sensitive_entities=sensitive, extra_names=extra_names)
            if reason:
                return f"the answer reads a protected device ({entity})"
    if op == "set_list_value" and isinstance(p.get("out"), str) and "." in p["out"]:
        return protected_reason(hass, p["out"], sensitive_entities=sensitive, extra_names=extra_names)
    if op in ("add_names", "remove_names") and not str(p.get("target", "")).startswith("area."):
        return protected_reason(hass, p.get("target", ""), sensitive_entities=sensitive, extra_names=extra_names)
    return None


def _with_answer(hass: HomeAssistant, item: dict) -> dict:
    """add_intent with answer blocks → a copy whose speech is compiled from them (else unchanged)."""
    p = item.get("payload") or {}
    cfg = p.get("intent") if item.get("op") == "add_intent" else None
    if not isinstance(cfg, dict) or not cfg.get("answer_blocks"):
        return item
    from .answer import AnswerError, blocks_entities, materialize  # noqa: PLC0415

    try:
        units = {e: (hass.states.get(e).attributes.get("unit_of_measurement") if hass.states.get(e) else None)
                 for e in blocks_entities(cfg)}
        compiled = materialize(cfg, {e: u for e, u in units.items() if isinstance(u, str)})
    except AnswerError:
        return item  # check_item reports it
    return {**item, "payload": {**p, "intent": compiled}}


async def async_accept(
    hass: HomeAssistant,
    runtime: dict,
    ids: list[str],
    *,
    actor: str,
    force: bool = False,
    extra_reason: Callable[[dict, dict], str | None] | None = None,
    base_rev: str | None = None,
) -> dict[str, Any]:
    """Apply the selected proposals in ONE guarded write.

    Returns the guard result plus ``applied`` / ``refused``; a refused save
    comes back as ``saved: False`` with ``code`` (callers must check
    ``saved``). Raises AcceptError only for the YAML source.
    """
    if runtime["source"].kind != SOURCE_CATALOG:
        raise AcceptError("yaml_source_read_only",
                          "proposals are applied to the catalog — switch the intents source to the catalog first")
    queue = queue_of(hass, runtime)
    wanted = set(ids)
    items = [i for i in await queue.async_items() if i["id"] in wanted]
    config = runtime["matcher"].config
    sensitive = sensitive_entities(config)
    extra_names = names_table(config)
    registered = {h.intent_type for h in intent.async_get(hass) if not isinstance(h, CatalogIntentHandler)}
    refused: dict[str, str] = {}
    trial = copy.deepcopy(config)
    usable = []
    for item in items:
        # the proposal as it came (plain text, blocks only) is checked BEFORE the server compiles
        # its blocks into the template — the compiled speech is server-made, not proposal text
        reason = check_item(item)
        if not reason:  # compile only what passed the static check (malformed blocks never get here)
            item = _with_answer(hass, item)  # blocks → the template, compiled HERE with the real units
        reason = reason or check_item_against(item, trial) or dynamic_reason(
            hass, item, sensitive, extra_names, registered
        )
        if not reason and item.get("check", {}).get("status") == "error":
            reason = "; ".join(item.get("check", {}).get("notes", [])[:1]) or "has errors"
        if not reason and extra_reason is not None:
            reason = extra_reason(item, trial)
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
        return {"saved": False, "code": "nothing_usable", "refused": refused, "applied": []}

    def _mutate(data: dict) -> None:
        for item in usable:
            # repeat document checks on the FRESH document inside the write lock
            reason = check_item_against(item, data) or (extra_reason(item, data) if extra_reason else None)
            if reason:
                raise GuardError("stale", f"{item.get('title', item['id'])}: {reason}")
            try:
                apply_op(data, item)
            except QueueError as err:
                raise GuardError("stale", f"{item.get('title', item['id'])}: {err}") from None

    try:
        result = await async_guarded_update(hass, runtime, _mutate, force=force, actor=actor, base_rev=base_rev)
    except GuardError as err:
        return {"saved": False, "code": err.code, "message": str(err), "refused": refused, "applied": [], **err.details}
    await queue.async_remove([i["id"] for i in usable])
    return {**result, "applied": [i["id"] for i in usable], "refused": refused}
