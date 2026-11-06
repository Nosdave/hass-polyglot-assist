"""Where intents come from: the YAML file or the catalog in ``.storage``.

Both hold the very same schema (``intents`` / ``lists`` / ``expansion_rules``
plus optional per-intent metadata), so matcher, runtime lists and corpus
behave identically whichever source is active. ``source: yaml`` stays the
default; ``import_yaml`` copies the file 1:1 into the catalog (verified
round-trip) before an installation switches to ``source: catalog``.
"""
from __future__ import annotations

import copy
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    CONF_INTENTS_FILE,
    CONF_SOURCE,
    DEFAULT_INTENTS_FILE,
    DOMAIN,
    SOURCE_CATALOG,
    SOURCE_YAML,
)

STORAGE_VERSION = 1


class SourceError(Exception):
    """Source cannot be read or written (message safe for users)."""


def _dump(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, indent=2, width=200)


class IntentSource:
    """Reads and writes the active intents source of one config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.catalog.{entry.entry_id}", atomic_writes=True
        )

    # ── locations ───────────────────────────────────────────────────────

    @property
    def kind(self) -> str:
        return self.entry.options.get(CONF_SOURCE, SOURCE_YAML)

    @property
    def yaml_path(self) -> Path:
        name = self.entry.data.get(CONF_INTENTS_FILE, DEFAULT_INTENTS_FILE)
        path = Path(name)
        return path if path.is_absolute() else Path(self.hass.config.path(name))

    # ── YAML file ───────────────────────────────────────────────────────

    async def async_load_yaml(self) -> dict[str, Any]:
        path = self.yaml_path
        if not await self.hass.async_add_executor_job(path.is_file):
            raise SourceError(f"intents file not found: {path}")
        raw = await self.hass.async_add_executor_job(path.read_text, "utf-8")
        data = yaml.safe_load(raw) or {}
        if not isinstance(data, dict):
            raise SourceError(f"{path.name}: top level must be a mapping")
        return data

    # ── catalog ─────────────────────────────────────────────────────────

    async def async_load_catalog(self) -> dict[str, Any] | None:
        stored = await self._store.async_load()
        if not stored or not isinstance(stored.get("catalog"), dict):
            return None
        return stored["catalog"]

    async def async_save_catalog(self, data: dict[str, Any]) -> None:
        await self._store.async_save(
            {"catalog": copy.deepcopy(data), "saved": datetime.now().isoformat()}
        )

    # ── active source ───────────────────────────────────────────────────

    async def async_load(self) -> dict[str, Any]:
        """Data of the active source."""
        if self.kind == SOURCE_CATALOG:
            data = await self.async_load_catalog()
            if data is None:
                raise SourceError("source is 'catalog' but the catalog is empty — run import_yaml")
            return copy.deepcopy(data)
        return await self.async_load_yaml()

    async def async_save(self, data: dict[str, Any]) -> None:
        """Write to the active source (YAML: a .bak of the previous file is kept)."""
        if self.kind == SOURCE_CATALOG:
            await self.async_save_catalog(data)
            return
        text = _dump(data)
        await self.async_save_yaml_text(text)

    async def async_save_yaml_text(self, text: str) -> None:
        path = self.yaml_path

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_file():
                path.with_name(path.name + ".bak").write_text(path.read_text("utf-8"), "utf-8")
            path.write_text(text, "utf-8")

        await self.hass.async_add_executor_job(_write)

    async def async_load_text(self) -> str:
        """YAML text of the active source (for the raw editor)."""
        if self.kind == SOURCE_CATALOG:
            return _dump(await self.async_load())
        return await self.hass.async_add_executor_job(self.yaml_path.read_text, "utf-8")

    async def async_save_text(self, text: str) -> None:
        """Save raw YAML text to the active source (parsed for the catalog)."""
        if self.kind == SOURCE_CATALOG:
            data = yaml.safe_load(text) or {}
            if not isinstance(data, dict):
                raise SourceError("top level must be a mapping")
            await self.async_save_catalog(data)
            return
        await self.async_save_yaml_text(text)

    # ── migration ───────────────────────────────────────────────────────

    async def async_import_yaml(self) -> dict[str, Any]:
        """Copy intents.yaml 1:1 into the catalog and verify the round-trip."""
        data = await self.async_load_yaml()
        await self.async_save_catalog(data)
        stored = await self.async_load_catalog()
        if stored != data:
            raise SourceError("catalog round-trip differs from intents.yaml — not switched")
        return data

    async def async_export_yaml(self) -> Path:
        """Write the catalog to polyglot_assist/export/intents-<time>.yaml."""
        data = await self.async_load_catalog()
        if data is None:
            raise SourceError("catalog is empty")
        target = Path(self.hass.config.path(DOMAIN, "export")) / (
            f"intents-{datetime.now().strftime('%Y%m%d-%H%M%S')}.yaml"
        )

        def _write() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_dump(data), "utf-8")

        await self.hass.async_add_executor_job(_write)
        return target
