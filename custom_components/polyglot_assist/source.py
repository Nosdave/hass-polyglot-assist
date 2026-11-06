"""Where intents come from: the YAML file or the catalog in ``.storage``.

Both hold the very same schema (``intents`` / ``lists`` / ``expansion_rules``
plus optional per-intent metadata), so matcher, runtime lists and corpus
behave identically whichever source is active. ``source: yaml`` stays the
default; ``import_yaml`` copies the file 1:1 into the catalog (verified
round-trip) before an installation switches to ``source: catalog``.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import yaml

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.file import write_utf8_file_atomic

from .const import (
    CONF_INTENTS_FILE,
    CONF_SOURCE,
    DEFAULT_INTENTS_FILE,
    DOMAIN,
    SOURCE_CATALOG,
    SOURCE_YAML,
)

STORAGE_VERSION = 1
KEEP_BACKUPS = 10


class SourceError(Exception):
    """Source cannot be read or written (message safe for users)."""


def json_roundtrip_equal(data: dict[str, Any]) -> bool:
    """True if ``data`` survives JSON storage unchanged, key order included.

    YAML can carry dates, sets, NaN or integer keys that the store would
    silently convert — such a catalog would differ after the next restart.
    """
    try:
        raw = json.dumps(data, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return False
    back = json.loads(raw)
    return back == data and json.dumps(back, ensure_ascii=False) == raw


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
        """Save and verify; raises SourceError if the store would alter data."""
        if not json_roundtrip_equal(data):
            raise SourceError(
                "the intents contain values the catalog cannot store unchanged "
                "(dates, sets, NaN or non-text keys)"
            )
        await self._store.async_save(
            {"catalog": copy.deepcopy(data), "saved": dt_util.utcnow().isoformat()}
        )
        if await self.async_load_catalog() != data:
            raise SourceError("catalog write could not be verified (see log)")

    async def async_remove_catalog(self) -> None:
        await self._store.async_remove()

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
        """Atomic write; the previous file is kept as a timestamped backup."""
        path = self.yaml_path
        stamp = dt_util.now().strftime("%Y%m%d-%H%M%S")

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_file():
                old = path.read_text("utf-8")
                if old == text:
                    return
                backup = path.with_name(f"{path.name}.bak-{stamp}-polyglot")
                n = 1
                while backup.exists():
                    n += 1
                    backup = path.with_name(f"{path.name}.bak-{stamp}-{n}-polyglot")
                write_utf8_file_atomic(str(backup), old)
                backups = sorted(path.parent.glob(f"{path.name}.bak-*-polyglot"))
                for stale in backups[:-KEEP_BACKUPS]:
                    stale.unlink(missing_ok=True)
            write_utf8_file_atomic(str(path), text)

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

    async def async_import_yaml(self, *, overwrite: bool = False) -> dict[str, Any]:
        """Copy intents.yaml 1:1 into the catalog (checked BEFORE writing).

        While the catalog is the active source it is only overwritten with
        ``overwrite`` — and then exported first, so no catalog edit is lost.
        """
        data = await self.async_load_yaml()
        if not json_roundtrip_equal(data):
            raise SourceError(
                "intents.yaml contains values the catalog cannot store unchanged "
                "(dates, sets, NaN or non-text keys) — nothing was changed"
            )
        previous = await self.async_load_catalog()
        if previous is not None and self.kind == SOURCE_CATALOG:
            if not overwrite:
                raise SourceError(
                    "the catalog is the active source — importing would replace its "
                    "edits; use overwrite (the catalog is exported first)"
                )
            await self.async_export_yaml()
        try:
            await self.async_save_catalog(data)
        except SourceError:
            if previous is not None:
                await self._store.async_save({"catalog": previous, "saved": dt_util.utcnow().isoformat()})
            raise
        return data

    async def async_export_yaml(self) -> Path:
        """Write the catalog to polyglot_assist/export/intents-<time>.yaml."""
        data = await self.async_load_catalog()
        if data is None:
            raise SourceError("catalog is empty")
        folder = Path(self.hass.config.path(DOMAIN, "export"))
        stamp = dt_util.now().strftime("%Y%m%d-%H%M%S")

        def _write() -> Path:
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / f"intents-{stamp}.yaml"
            n = 1
            while target.exists():
                n += 1
                target = folder / f"intents-{stamp}-{n}.yaml"
            write_utf8_file_atomic(str(target), _dump(data))
            return target

        return await self.hass.async_add_executor_job(_write)
