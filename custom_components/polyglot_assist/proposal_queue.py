"""Proposal queue: new, changed and removed commands, sentences and names.

Nothing in the queue is active. Accepting applies the selected proposals in
ONE regression-guarded write (guard.py); rejected or applied proposals leave
the queue. Every proposal says where it comes from (``origin``) and how it was
checked (``check``).

Operations (``op``)::

    add_intent        {name, intent}
    add_sentences     {intent, lang, sentences}
    remove_sentences  {intent, lang, sentences}
    set_field         {intent, field, value}        # category, kind, safety, …
    add_names         {target, lang, names}
    remove_names      {target, lang, names}
    set_list_value    {list, in, out}               # e.g. "gang" → light.gang_ost
    remove_list_value {list, in}
"""
from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .proposals import OPS, ORIGINS, proposal_id

STORAGE_VERSION = 1
MAX_ITEMS = 500


class ProposalQueue:
    """Persistent queue per config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.proposals.{entry_id}", atomic_writes=True
        )
        self._items: list[dict[str, Any]] | None = None
        self._rejected: list[str] = []
        self._load_lock = asyncio.Lock()

    async def async_items(self) -> list[dict[str, Any]]:
        if self._items is None:
            async with self._load_lock:  # one load; a late load never overwrites live changes
                if self._items is None:
                    stored = await self._store.async_load() or {}
                    self._items = list(stored.get("items") or [])
                    self._rejected = list(stored.get("rejected") or [])
        return self._items

    async def _save(self) -> None:
        await self._store.async_save({"items": self._items or [], "rejected": self._rejected[-2000:]})

    async def async_add(self, items: list[dict[str, Any]]) -> list[str]:
        """Add proposals (deduplicated by content). Returns the new ids."""
        current = await self.async_items()
        known = {i["id"] for i in current} | set(self._rejected)
        added: list[str] = []
        for item in items:
            if item.get("op") not in OPS:
                continue
            item = {**item, "id": proposal_id(item)}
            item.setdefault("created", dt_util.utcnow().isoformat())
            origin = item.setdefault("origin", {})
            if origin.get("source") not in ORIGINS:
                origin["source"] = "manual"
            if item["id"] in known:
                continue
            current.append(item)
            known.add(item["id"])
            added.append(item["id"])
        del current[:-MAX_ITEMS]
        await self._save()
        return added

    async def async_reject(self, ids: list[str]) -> int:
        """Remove and remember: a rejected proposal is never proposed again."""
        await self.async_items()
        self._rejected = [i for i in self._rejected if i not in set(ids)] + list(dict.fromkeys(ids))
        return await self.async_remove(ids)

    async def async_remove(self, ids: list[str]) -> int:
        current = await self.async_items()
        keep = [i for i in current if i["id"] not in set(ids)]
        removed = len(current) - len(keep)
        self._items = keep
        await self._save()
        return removed

    async def async_clear(self) -> None:
        self._items = []
        await self._store.async_remove()
