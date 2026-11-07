"""What may be accepted BY VOICE (allow-list) and how often.

Voice has no user account: whoever is in earshot — a visitor, the TV — can
say "yes". So only harmless additions pass, each checked afresh at the
moment of acceptance:

* only new sentences (``add_sentences``) and new names (``add_names``);
* the command is "normal", its action touches only unprotected devices, its
  name/sentences do not look security-relevant;
* proposals from monitoring additionally need provenance: said at least
  twice, from approved satellites only, in ≥ 2 conversations ≥ 1 h apart,
  and the stored check must be "ok" (warnings stay in the panel);
* no free-text placeholder (also hidden in rules or list values);
* a new name must not already name another device, area or command;
* at most ``MAX_PER_HOUR`` voice acceptances per entry and hour (stored,
  survives restarts).
"""
from __future__ import annotations

import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .corpus import names_table, string_rules
from .proposals import looks_protected, norm, parse_rules
from .safety import action_target_reason, protected_reason, sensitive_entities

import re

_ENTITY = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
MAX_PER_HOUR = 5
MIN_SPAN_S = 3600
MIN_CONVERSATIONS = 2
VOICE_OPS = ("add_sentences", "add_names")


def _known_names(hass: HomeAssistant, config: dict[str, Any]) -> dict[str, str]:
    """Every name a sentence could already mean → what it names."""
    out: dict[str, str] = {}
    reg = er.async_get(hass)
    for st in hass.states.async_all():
        out.setdefault(norm(st.name or ""), st.entity_id)
        entry = reg.async_get(st.entity_id)
        for alias in (entry.aliases if entry else None) or []:
            if isinstance(alias, str):
                out.setdefault(norm(alias), st.entity_id)
    from homeassistant.helpers import device_registry as dr  # noqa: PLC0415
    from homeassistant.helpers import floor_registry as fr  # noqa: PLC0415

    for device in dr.async_get(hass).devices:  # iterating yields the device entries
        for n in (device.name_by_user, device.name):
            if n:
                out.setdefault(norm(n), f"device {device.id}")
    for floor in fr.async_get(hass).async_list_floors():
        out.setdefault(norm(floor.name), f"floor.{floor.floor_id}")
    for name, spec in (config.get("lists") or {}).items():
        for value in (spec or {}).get("values", []) if isinstance(spec, dict) else []:
            if isinstance(value, dict) and isinstance(value.get("in"), str):
                out.setdefault(norm(value["in"]), f"list {name}")
            elif isinstance(value, str):
                out.setdefault(norm(value), f"list {name}")
    for area in ar.async_get(hass).async_list_areas():
        out.setdefault(norm(area.name), f"area.{area.id}")
        for alias in area.aliases or []:
            out.setdefault(norm(alias), f"area.{area.id}")
    for target, names in names_table(config).items():
        for n in names:
            out.setdefault(norm(n), target)
    for name in (config.get("intents") or {}):
        out.setdefault(norm(name), f"command {name}")
    out.pop("", None)
    return out


def check_voice_item(
    hass: HomeAssistant, item: dict[str, Any], config: dict[str, Any], *, approved_satellites: set[str],
    entry_id: str | None = None, runtime_lists: dict | None = None,
) -> str | None:
    """Why this proposal must NOT be accepted by voice (None = allowed)."""
    from hassil.intents import WildcardSlotList  # noqa: PLC0415
    from hassil.parse_expression import parse_sentence  # noqa: PLC0415

    from .monitor import _all_list_refs  # noqa: PLC0415

    op, p = item.get("op"), item.get("payload") or {}
    if op not in VOICE_OPS:
        return "only new sentences and names can be accepted by voice — use the panel"
    if (item.get("check") or {}).get("status") != "ok":
        return "has notes — please review it in the panel"
    sensitive = sensitive_entities(config)
    extra = names_table(config)
    origin = (item.get("origin") or {}).get("source")
    if origin == "monitor":
        ev = (item.get("origin") or {}).get("evidence") or []
        if not ev:
            return "no evidence recorded — use the panel"
        for e in ev:
            sats = set(e.get("satellites") or [])
            if e.get("from_other") or not sats or not sats <= approved_satellites:
                return "said outside the approved satellites — use the panel"
            if e.get("count", 0) < 2 or e.get("conversations", 0) < MIN_CONVERSATIONS or e.get("span_s", 0) < MIN_SPAN_S:
                return "not repeated often enough yet — use the panel"
    if op == "add_sentences":
        name = p.get("intent")
        cfg = (config.get("intents") or {}).get(name)
        if not isinstance(cfg, dict):
            return "unknown command"
        if cfg.get("safety") not in (None, "normal"):
            return "security-relevant or switched-off command — use the panel"
        texts = [name or "", cfg.get("description") or "", *(p.get("sentences") or [])]
        if any(looks_protected(t) for t in texts):
            return "looks security-relevant — use the panel"
        # only commands whose action Polyglot itself runs and that is FULLY inspectable:
        # plain service steps, literal entity ids, no templates, no script/scene/area targets
        from .monitor import _action_entities  # noqa: PLC0415
        from .safety import _has_template  # noqa: PLC0415

        action = cfg.get("action")
        if not action:
            return "what this command does is not known to Polyglot (intent_script) — use the panel"
        # what really runs is the REGISTERED handler — it must be this entry's catalog action
        from homeassistant.helpers import intent as ha_intent  # noqa: PLC0415

        from .actions import CatalogIntentHandler  # noqa: PLC0415

        handler = next((h for h in ha_intent.async_get(hass) if h.intent_type == name), None)
        if not isinstance(handler, CatalogIntentHandler) or (entry_id is not None and handler.owner != entry_id):
            return "another handler (e.g. intent_script) runs this command — use the panel"
        entities = _action_entities(action)
        if not entities or _has_template(action):
            return "this command's action cannot be fully checked — use the panel"
        for e in entities:
            if action_target_reason(hass, e, sensitive=sensitive, extra_names=extra):
                return "the command switches a protected device — use the panel"
        from .matcher import MultilangMatcher  # noqa: PLC0415
        from .corpus import build_intents  # noqa: PLC0415

        lists = {**MultilangMatcher(build_intents(config)).static_slot_lists(), **(runtime_lists or {})}
        rules = parse_rules(string_rules(config))
        for sentence in p.get("sentences") or []:
            try:
                refs = _all_list_refs(parse_sentence(sentence), rules, lists)
            except Exception:  # noqa: BLE001
                return "not parsable"
            if any(isinstance(lists.get(r), WildcardSlotList) for r in refs):
                return "free-text placeholders cannot be learned by voice"
            # checked afresh against the CURRENT catalog (the stored result may be old)
            from .monitor import _other_samples  # noqa: PLC0415
            from .proposals import check_proposal  # noqa: PLC0415

            matcher = MultilangMatcher(build_intents(config))
            lang = p.get("lang") or ""
            source = [x for lst in (cfg.get("sentences") or {}).values() for x in (lst or []) if isinstance(x, str)]
            fresh = check_proposal(name, sentence, source, lambda t, lang=lang: matcher.match_all(t, lang, lists), lists,
                                   string_rules(config), lang, _other_samples(config, lists, rules, lang, name))
            if fresh.get("status") != "ok":
                return "no longer fits the current commands — use the panel"
            # every value a placeholder can produce must be harmless (runtime lists too: {name} …)
            from hassil.intents import TextSlotList  # noqa: PLC0415

            for ref in refs:
                lst = lists.get(ref)
                if isinstance(lst, TextSlotList):
                    for v in lst.values:
                        # the value itself or its context (e.g. {name}: friendly name + context entity_id)
                        ctx = getattr(v, "context", None) or {}
                        for out in (getattr(v, "value_out", None), ctx.get("entity_id") if isinstance(ctx, dict) else None):
                            if isinstance(out, str) and _ENTITY.match(out) and protected_reason(
                                    hass, out, sensitive_entities=sensitive, extra_names=extra):
                                return f"a placeholder can reach a protected device ({out}) — use the panel"
                elif ref in ("name", "area", "floor") or lst is None:
                    return "a placeholder whose values are not known — use the panel"
            for ref in refs:
                for value in ((config.get("lists") or {}).get(ref) or {}).get("values", []) if isinstance((config.get("lists") or {}).get(ref), dict) else []:
                    out = value.get("out") if isinstance(value, dict) else value
                    if isinstance(out, str) and "." in out and out.split(".", 1)[0].isidentifier():
                        if protected_reason(hass, out, sensitive_entities=sensitive, extra_names=extra):
                            return f"a placeholder can reach a protected device ({out}) — use the panel"
    if op == "add_names":
        target = str(p.get("target", ""))
        if not target or target.startswith(("area.", "floor.")):
            return "names for areas are only changed in the panel"
        if protected_reason(hass, target, sensitive_entities=sensitive, extra_names=extra):
            return "protected device — use the panel"
        known = _known_names(hass, config)
        for n in p.get("names") or []:
            other = known.get(norm(n))
            if other and other != target:
                return f"“{n}” already names something else ({other})"
            if looks_protected(n):
                return "looks security-relevant — use the panel"
    return None


class VoiceRate:
    """At most MAX_PER_HOUR voice acceptances per entry and hour (persisted).

    Use ``async with rate.slot() as ok:`` around the acceptance and call
    ``rate.charge()`` only after it was saved — the own lock serialises
    concurrent "yes" from two satellites (never take it inside the write lock).
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        import asyncio  # noqa: PLC0415

        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.voice.{entry_id}", atomic_writes=True)
        self._times: list[float] | None = None
        self.lock = asyncio.Lock()

    async def _load(self) -> list[float]:
        if self._times is None:
            data = await self._store.async_load() or {}
            if self._times is None:
                self._times = [t for t in data.get("accepted", []) if isinstance(t, (int, float))]
        return self._times

    async def async_left(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        return max(0, MAX_PER_HOUR - sum(1 for t in await self._load() if 0 <= now - t < 3600))

    async def async_charge(self, now: float | None = None) -> None:
        """Count one acceptance that WAS saved (call while holding ``lock``)."""
        now = time.time() if now is None else now
        times = [t for t in await self._load() if 0 <= now - t < 3600] + [now]
        self._times = times
        await self._store.async_save({"accepted": times})

    async def async_take(self, now: float | None = None) -> bool:
        """Check and count in one step (tests / simple callers)."""
        async with self.lock:
            if await self.async_left(now) <= 0:
                return False
            await self.async_charge(now)
            return True

    async def async_clear(self) -> None:
        self._times = []
        await self._store.async_remove()


def get_rate(hass: HomeAssistant, entry_id: str) -> VoiceRate:
    store = hass.data.setdefault(f"{DOMAIN}_voice_rate", {})
    if entry_id not in store:
        store[entry_id] = VoiceRate(hass, entry_id)
    return store[entry_id]
