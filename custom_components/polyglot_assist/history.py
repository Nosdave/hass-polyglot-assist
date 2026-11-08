"""Version history of the catalog: every guarded save keeps the state before it.

The last ``KEEP`` states are stored (in ``.storage``, so they are part of
Home Assistant backups). Restoring a state is an ordinary guarded save —
validation and the regression corpus apply, and the restore itself can be
undone again.
"""
from __future__ import annotations

import asyncio
import copy
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

KEEP = 20
STORAGE_VERSION = 1
COALESCE_SECONDS = 60  # several saves by the same actor within this time are one entry
SAFETY_ORDER = {"normal": 0, None: 0, "sensitive": 1, "disabled": 2}


def get_history(hass: HomeAssistant, entry_id: str) -> "CatalogHistory":
    """The one history object of an entry (survives entry reloads)."""
    store = hass.data.setdefault(f"{DOMAIN}_history", {})
    if entry_id not in store:
        store[entry_id] = CatalogHistory(hass, entry_id)
    return store[entry_id]


def loosened(current: dict[str, Any], old: dict[str, Any], hass: HomeAssistant | None = None) -> list[dict[str, Any]]:
    """Commands a restore of ``old`` would make less protected than they are now.

    Lower safety level, or a command that is gone now but would come back
    (with an action or without protection).
    """
    out: list[dict[str, Any]] = []
    now_i = current.get("intents") or {}
    old_i = old.get("intents") or {}
    for name, cfg in old_i.items():
        if not isinstance(cfg, dict):
            continue
        then = cfg.get("safety")
        if name in now_i and isinstance(now_i[name], dict):
            now = now_i[name].get("safety")
            if SAFETY_ORDER.get(then, 2) < SAFETY_ORDER.get(now, 2):
                out.append({"intent": name, "now": now or "normal", "then": then or "normal"})
            elif cfg.get("action") and cfg.get("action") != now_i[name].get("action"):
                out.append({"intent": name, "now": "action", "then": "other action"})
            elif cfg.get("owned") and not now_i[name].get("owned"):
                # restoring a takeover: Polyglot (not the YAML file) would run this command again
                out.append({"intent": name, "now": "YAML", "then": "Polyglot runs it"})
        elif name not in now_i and then != "disabled":  # a switched-off command coming back stays off
            out.append({"intent": name, "now": "removed", "then": then or "normal"})
    if hass is not None:
        out += _effective_loosened(hass, current, old)
    return out


def change_summary(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[str]]:
    """Which commands, lists and name entries a save added, removed or changed."""
    out: dict[str, list[str]] = {}
    for part in ("intents", "lists", "names"):
        a = before.get(part) or {}
        b = after.get(part) or {}
        if not isinstance(a, dict) or not isinstance(b, dict):
            continue
        added = sorted(set(b) - set(a))
        removed = sorted(set(a) - set(b))
        changed = sorted(k for k in set(a) & set(b) if a[k] != b[k])
        for kind, keys in (("added", added), ("removed", removed), ("changed", changed)):
            if keys:
                out[f"{part}_{kind}"] = keys[:50]
    other = sorted(k for k in set(before) | set(after) if k not in ("intents", "lists", "names") and before.get(k) != after.get(k))
    if other:
        out["other_changed"] = other
    if not out and not _same_order(before, after):
        # the order of commands decides which one wins — a reorder is a real change
        out["order_changed"] = [p for p in ("intents", "lists", "names") if list(before.get(p) or {}) != list(after.get(p) or {})] or ["document"]
    return out


def _same_order(a: Any, b: Any) -> bool:
    from .corpus import same_document  # noqa: PLC0415

    return same_document(a, b)


async def async_record(hass: HomeAssistant, entry_id: str, before: dict[str, Any] | None, after: dict[str, Any], actor: str) -> None:
    """Record a write that does not go through the guard (import, source switch, repair)."""
    if before is None:
        return
    from .guard import revision  # noqa: PLC0415 — guard imports this module

    try:
        await get_history(hass, entry_id).async_add(before, after, rev=revision(before), actor=actor)
    except Exception:  # noqa: BLE001 — the write itself succeeded; history is a convenience
        _LOGGER.exception("Polyglot Assist: could not record the catalog history (%s)", actor)


def _effective_loosened(hass: HomeAssistant, current: dict[str, Any], old: dict[str, Any]) -> list[dict[str, Any]]:
    """Entities protected under ``current`` but not under ``old`` — using the real check.

    Runs the same protection rule as the built-in commands (safety.protected_reason)
    with each catalog's protected commands and name table, so every rule counts:
    security words in names, window words that whitelist covers, commands marked
    sensitive/disabled.
    """
    from .corpus import names_table  # noqa: PLC0415
    from .safety import protected_reason, sensitive_entities  # noqa: PLC0415

    sens_now, sens_old = sensitive_entities(current), sensitive_entities(old)
    names_now, names_old = names_table(current), names_table(old)
    candidates = {e for e in (*sens_now, *sens_old, *names_now, *names_old) if "." in e and not e.startswith("area.")}
    out = []
    for entity in sorted(candidates):
        now = protected_reason(hass, entity, sensitive_entities=sens_now, extra_names=names_now)
        then = protected_reason(hass, entity, sensitive_entities=sens_old, extra_names=names_old)
        if now and not then:
            out.append({"entity": entity, "now": f"protected: {now}", "then": "unprotected"})
    return out


class CatalogHistory:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.history.{entry_id}", atomic_writes=True
        )
        self._items: list[dict[str, Any]] | None = None
        self._lock = asyncio.Lock()

    async def _load(self) -> list[dict[str, Any]]:
        if self._items is None:
            async with self._lock:
                if self._items is None:  # a second caller must not replace live items
                    stored = await self._store.async_load() or {}
                    items = stored.get("items")
                    self._items = items if isinstance(items, list) else []
        return self._items

    async def async_add(self, before: dict[str, Any], after: dict[str, Any], *, rev: str, actor: str) -> None:
        """Remember ``before`` (revision ``rev``) as the state preceding this save."""
        summary = change_summary(before, after)
        if not summary:
            return
        items = await self._load()
        now = dt_util.utcnow()
        head = items[0] if items else None
        head_at = dt_util.parse_datetime(head["at"]) if head and isinstance(head.get("at"), str) else None
        if head and head.get("actor") == actor and head_at and (now - head_at).total_seconds() < COALESCE_SECONDS:
            # same person, a moment later: keep the state before the first save,
            # merge what changed
            merged = dict(head.get("summary") or {})
            for key, names in summary.items():
                merged[key] = sorted(set(merged.get(key, [])) | set(names))[:50]
            head.update(at=now.isoformat(), summary=merged, count=head.get("count", 1) + 1)
        else:
            items.insert(0, {
                "rev": rev, "at": now.isoformat(), "actor": actor,
                "summary": summary, "catalog": copy.deepcopy(before),
            })
            del items[KEEP:]
        await self._store.async_save({"items": items})

    async def async_clear(self) -> None:
        self._items = []
        await self._store.async_remove()

    async def async_list(self) -> list[dict[str, Any]]:
        return [{k: v for k, v in i.items() if k != "catalog"} for i in await self._load()]

    async def async_get(self, rev: str) -> dict[str, Any] | None:
        for item in await self._load():
            if item.get("rev") == rev:
                return copy.deepcopy(item["catalog"])
        return None
