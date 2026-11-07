"""Version history of the catalog: every guarded save keeps the state before it.

The last ``KEEP`` states are stored (in ``.storage``, so they are part of
Home Assistant backups). Restoring a state is an ordinary guarded save —
validation and the regression corpus apply, and the restore itself can be
undone again.
"""
from __future__ import annotations

import copy
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

KEEP = 20
STORAGE_VERSION = 1


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
    return out


class CatalogHistory:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.history.{entry_id}", atomic_writes=True
        )
        self._items: list[dict[str, Any]] | None = None

    async def _load(self) -> list[dict[str, Any]]:
        if self._items is None:
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
        items.insert(0, {
            "rev": rev, "at": dt_util.utcnow().isoformat(), "actor": actor,
            "summary": summary, "catalog": copy.deepcopy(before),
        })
        del items[KEEP:]
        await self._store.async_save({"items": items})

    async def async_list(self) -> list[dict[str, Any]]:
        return [{k: v for k, v in i.items() if k != "catalog"} for i in await self._load()]

    async def async_get(self, rev: str) -> dict[str, Any] | None:
        for item in await self._load():
            if item.get("rev") == rev:
                return copy.deepcopy(item["catalog"])
        return None
