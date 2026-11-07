"""Talking about suggestions by voice: "What have you learned?" → "Accept it" → "Yes".

Off by default; only on approved satellites (never the app, never text
chats). It runs BEFORE the catalog, so a catalog sentence such as "yes"
cannot take it over — but only while switched on, and "yes"/"no" only count
as the answer to an open question.

Every acceptance goes through accept.async_accept with the voice allow-list
(voice_rules.check_voice_item), at most 5 per hour, and can be undone by
voice (the counter-operation of exactly that acceptance, never a whole
older catalog state).
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

from hassil.intents import Intents
from hassil.recognize import recognize_best

from homeassistant.core import HomeAssistant

from .proposals import norm
from .speech import say

CONFIRM_SECONDS = 30
LISTING_SECONDS = 300
UNDO_SECONDS = 24 * 3600
MAX_LISTED = 5

_NTH = {
    "de": [("erste", 1), ("ersten", 1), ("erster", 1), ("zweite", 2), ("zweiten", 2), ("zweiter", 2),
           ("dritte", 3), ("dritten", 3), ("dritter", 3), ("vierte", 4), ("vierten", 4), ("fünfte", 5), ("fünften", 5),
           ("1", 1), ("2", 2), ("3", 3), ("4", 4), ("5", 5)],
    "en": [("first", 1), ("second", 2), ("third", 3), ("fourth", 4), ("fifth", 5), ("1", 1), ("2", 2), ("3", 3), ("4", 4), ("5", 5)],
    "fr": [("premier", 1), ("première", 1), ("deuxième", 2), ("second", 2), ("seconde", 2), ("troisième", 3), ("quatrième", 4),
           ("cinquième", 5), ("1", 1), ("2", 2), ("3", 3), ("4", 4), ("5", 5)],
}
_SENTENCES = {
    "de": {
        "list": ["was hast du gelernt", "(welche|gibt es) [neue] vorschläge", "was hast du [neu] gelernt"],
        "detail": ["(erzähl|sag) mir mehr (zum|zu dem|über den|zur|zu der|über die) {pg_nth} [vorschlag]", "was ist (der|die) {pg_nth} [vorschlag]"],
        "accept": ["übernimm (ihn|den|das|es|sie)", "übernimm (den|die|das) {pg_nth} [vorschlag]"],
        "reject": ["verwirf (ihn|den|das|es|sie)", "verwirf (den|die|das) {pg_nth} [vorschlag]"],
        "undo": ["mach das rückgängig", "nimm das zurück"],
        "later": ["später", "nicht jetzt"],
        "yes": ["ja", "ja bitte", "ja genau"],
        "no": ["nein", "nein danke", "lieber nicht"],
    },
    "en": {
        "list": ["what have you learned", "[are there] any [new] suggestions", "what did you learn"],
        "detail": ["tell me more about the {pg_nth} [one|suggestion]", "what is the {pg_nth} [one|suggestion]"],
        "accept": ["(accept|apply) (it|that)", "(accept|apply) the {pg_nth} [one|suggestion]"],
        "reject": ["(reject|discard) (it|that)", "(reject|discard) the {pg_nth} [one|suggestion]"],
        "undo": ["undo that", "take that back"],
        "later": ["later", "not now"],
        "yes": ["yes", "yes please", "yeah"],
        "no": ["no", "no thanks"],
    },
    "fr": {
        "list": ["qu'as-tu appris", "qu'est-ce que tu as appris", "[y a-t-il] des [nouvelles] suggestions"],
        "detail": ["(dis-m'en|dis moi) plus sur (la|le) {pg_nth} [suggestion]", "quelle est la {pg_nth} [suggestion]"],
        "accept": ["(accepte|applique)(-la|-le| la| le)", "(accepte|applique) (la|le) {pg_nth} [suggestion]"],
        "reject": ["(rejette|refuse)(-la|-le| la| le)", "(rejette|refuse) (la|le) {pg_nth} [suggestion]"],
        "undo": ["annule ça", "annule cela"],
        "later": ["plus tard", "pas maintenant"],
        "yes": ["oui", "oui s'il te plaît", "oui merci"],
        "no": ["non", "non merci"],
    },
}


def _build() -> dict[str, Intents]:
    out = {}
    for lang, kinds in _SENTENCES.items():
        out[lang] = Intents.from_dict({
            "language": lang,
            "intents": {f"PolyglotLearn_{k}": {"data": [{"sentences": v}]} for k, v in kinds.items()},
            "lists": {"pg_nth": {"values": [{"in": w, "out": n} for w, n in _NTH[lang]]}},
        })
    return out


def item_hash(item: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps({k: item.get(k) for k in ("op", "payload")}, sort_keys=True, default=str).encode()).hexdigest()[:16]


@dataclass
class _Pending:
    action: str  # accept | reject | undo
    item_id: str | None
    item_hash: str | None
    rev: str | None
    until: float
    undo: dict[str, Any] | None = None


@dataclass
class VoiceLearn:
    hass: HomeAssistant
    entry_id: str
    intents: dict[str, Intents] = field(default_factory=_build)
    listing: dict[tuple, tuple[float, list[str]]] = field(default_factory=dict)
    pending: dict[tuple, _Pending] = field(default_factory=dict)
    last: dict[str, dict[str, Any]] = field(default_factory=dict)  # satellite → last voice acceptance (also stored)
    focus: dict[tuple, str] = field(default_factory=dict)
    _rt_lists: dict = field(default_factory=dict)  # item last talked about → "accept it" means this one

    def recognize(self, text: str, languages: list[str]) -> tuple[str, str, int | None] | None:
        """(kind, lang, nth) of a learn command, or None (blocking — call in the executor)."""
        for lang in [x for x in languages if x in self.intents] or list(self.intents):
            try:
                result = recognize_best(text, self.intents[lang], language=lang)
            except Exception:  # noqa: BLE001
                result = None
            if result is not None:
                nth = result.entities.get("pg_nth")
                return result.intent.name.split("_", 1)[1], lang, int(nth.value) if nth else None
        return None

    # ── state ──────────────────────────────────────────────────────────────
    def _key(self, conversation_id: str | None, satellite_id: str) -> tuple:
        return (conversation_id, satellite_id)

    def has_pending(self, conversation_id: str | None, satellite_id: str) -> bool:
        p = self.pending.get(self._key(conversation_id, satellite_id))
        return p is not None and p.until >= time.monotonic()

    def drop(self, conversation_id: str | None, satellite_id: str) -> None:
        self.pending.pop(self._key(conversation_id, satellite_id), None)

    # ── the conversation ───────────────────────────────────────────────────
    def _offerable(self, items: list[dict[str, Any]], config: dict[str, Any], approved: set[str], rt_lists: dict) -> list[dict[str, Any]]:
        from .voice_rules import check_voice_item  # noqa: PLC0415

        return [i for i in items if check_voice_item(self.hass, i, config, approved_satellites=approved,
                                                      entry_id=self.entry_id, runtime_lists=rt_lists) is None][:MAX_LISTED]

    @staticmethod
    def _nothing_by_voice(waiting: int, runtime: dict[str, Any], lang: str) -> str:
        """Nothing may be accepted by voice — say why, and what the user can do."""
        from .const import CONF_MONITOR, CONF_MONITOR_TIME, CONF_TRANSLATION_AI_TASK  # noqa: PLC0415

        opts = runtime["entry"].options
        if waiting:
            return say("learn_none_panel_one" if waiting == 1 else "learn_none_panel", lang, count=str(waiting))
        if not opts.get(CONF_MONITOR):
            return say("learn_none_monitor_off", lang)
        if not opts.get(CONF_TRANSLATION_AI_TASK):
            return say("learn_none_no_ai", lang)
        return say("learn_none_listening", lang, time=str(opts.get(CONF_MONITOR_TIME) or "03:00"))

    def _describe(self, item: dict[str, Any], lang: str, config: dict[str, Any] | None = None) -> str:
        """Spoken description: an example sentence (not the template syntax) and what it does."""
        p = item.get("payload") or {}
        if item.get("op") == "add_names":
            st = self.hass.states.get(str(p.get("target", "")))
            return say("learn_item_name", lang, names=" / ".join(p.get("names") or []),
                       target=st.name if st else str(p.get("target", "")))
        cfg = ((config or {}).get("intents") or {}).get(p.get("intent")) or {}
        lists, rules = self._lists_rules(config or {})
        return say("learn_item_sentence", lang, sentence=_example(p.get("sentences") or [], lang, lists, rules),
                   command=cfg.get("description") or str(p.get("intent", "")))

    def _lists_rules(self, config: dict[str, Any]) -> tuple[dict, dict]:
        """Static + runtime lists and parsed rules, for speakable examples."""
        from .corpus import build_intents, string_rules  # noqa: PLC0415
        from .matcher import MultilangMatcher  # noqa: PLC0415
        from .proposals import parse_rules  # noqa: PLC0415

        cached = getattr(self, "_lr_cache", None)
        if cached is None or cached[0] is not config:  # once per catalog state, not per item
            try:
                lists = MultilangMatcher(build_intents(config)).static_slot_lists()
            except Exception:  # noqa: BLE001
                lists = {}
            cached = self._lr_cache = (config, lists, parse_rules(string_rules(config)))
        return {**cached[1], **(self._rt_lists or {})}, cached[2]

    async def async_handle(
        self, runtime: dict, *, text: str, conversation_id: str | None, satellite_id: str | None,
        approved: set[str], languages: list[str],
    ) -> tuple[str, str, bool] | None:
        """(speech, lang, ask_again) for a learn command, None if this is not one.

        Only "what have you learned?" works without context; every other
        learn sentence is ours only in its context (an open question, a fresh
        list, something to undo) — otherwise it goes on to the catalog/LLM.
        """
        if not satellite_id or satellite_id not in approved:
            return None
        hit = await self.hass.async_add_executor_job(self.recognize, text, languages)
        key = self._key(conversation_id, satellite_id)
        pending = self.has_pending(conversation_id, satellite_id)
        if hit is None:
            self.pending.pop(key, None)  # anything else ends an open question
            return None
        kind, lang, nth = hit
        until, ids = self.listing.get(key, (0, []))
        listed = until >= time.monotonic() and bool(ids)
        await self._load_last()
        if kind in ("yes", "no", "later") and not pending:
            return None
        if kind == "undo" and (not self._undoable(satellite_id) or runtime["source"].kind != _catalog()):
            return None  # nothing to undo — or the active source is no longer the catalog
        if kind in ("detail", "accept", "reject") and not listed:
            return None
        from .accept import queue_of  # noqa: PLC0415
        from .const import SOURCE_CATALOG  # noqa: PLC0415

        queue = queue_of(self.hass, runtime)
        config = runtime["matcher"].config
        rt_lists = runtime["lists"].async_get()
        self._rt_lists = rt_lists

        if kind == "list":
            if runtime["source"].kind != SOURCE_CATALOG:
                return say("learn_none_yaml", lang), lang, False
            waiting = await queue.async_items()
            offer = await self.hass.async_add_executor_job(
                self._offerable, waiting, config, approved, rt_lists)
            self.listing[key] = (time.monotonic() + LISTING_SECONDS, [i["id"] for i in offer])
            self.focus.pop(key, None)
            if not offer:
                return self._nothing_by_voice(len(waiting), runtime, lang), lang, False
            parts = [f"{n}. {self._describe(i, lang, config)}." for n, i in enumerate(offer, 1)]
            text = say("learn_list_one" if len(offer) == 1 else "learn_list", lang,
                       count=str(len(offer)), items=" ".join(parts))
            if (more := len(waiting) - len(offer)) > 0:
                text += " " + say("learn_more_in_panel", lang, count=str(more))
            return text, lang, False
        if kind == "later":
            self.pending.pop(key, None)
            return say("learn_later", lang), lang, False
        if kind == "no":
            self.pending.pop(key, None)
            return say("learn_nothing_changed", lang), lang, False
        if kind == "yes":
            return await self._confirm(runtime, key, satellite_id, lang, approved, rt_lists)
        if kind == "undo":
            last = self.last[satellite_id]
            self.pending[key] = _Pending("undo", None, None, None, time.monotonic() + CONFIRM_SECONDS, undo=last)
            return say("learn_confirm_undo", lang, what=last["what"]), lang, True
        if kind == "reject":
            # discarding by voice could wipe suggestions (a TV saying "yes") — panel only
            return say("learn_reject_panel", lang), lang, False

        # detail / accept: the named one, else the one talked about, else the only one
        if nth is not None:
            index = nth - 1
            if index >= len(ids):
                return say("learn_no_such", lang), lang, False
            item_id = ids[index]
        elif self.focus.get(key) in ids:
            item_id = self.focus[key]
        elif len(ids) == 1:
            item_id = ids[0]
        else:
            return say("learn_which", lang), lang, False
        item = next((i for i in await queue.async_items() if i["id"] == item_id), None)
        if item is None:
            return say("learn_gone", lang), lang, False
        self.focus[key] = item_id
        if kind == "detail":
            origin = (item.get("origin") or {}).get("source") or "?"
            return say("learn_detail", lang, what=self._describe(item, lang, config),
                       origin=say("origin_" + origin, lang) if origin in ("monitor", "correction", "translation", "starter", "new_devices") else origin), lang, False
        from .voice_rules import check_voice_item  # noqa: PLC0415

        reason = await self.hass.async_add_executor_job(
            lambda: check_voice_item(self.hass, item, config, approved_satellites=approved,
                                     entry_id=self.entry_id, runtime_lists=rt_lists))
        if reason:
            return say("learn_panel_only", lang), lang, False
        from .guard import revision  # noqa: PLC0415

        self.pending[key] = _Pending("accept", item["id"], item_hash(item), revision(config), time.monotonic() + CONFIRM_SECONDS)
        what = self._describe(item, lang, config)
        origin = item.get("origin") or {}
        if origin.get("source") == "monitor":
            heard = max((e.get("count", 0) for e in origin.get("evidence") or []), default=0)
            return say("learn_confirm_accept_monitor", lang, what=what, count=str(heard)), lang, True
        return say("learn_confirm_accept", lang, what=what), lang, True

    # ── undo memory (stored, survives restarts) ────────────────────────────
    def _store(self):
        from homeassistant.helpers.storage import Store  # noqa: PLC0415

        from .const import DOMAIN  # noqa: PLC0415

        return Store(self.hass, 1, f"{DOMAIN}.voice_undo.{self.entry_id}", atomic_writes=True)

    async def _load_last(self) -> None:
        if getattr(self, "_last_loaded", False):
            return
        data = await self._store().async_load() or {}
        self.last.update({k: v for k, v in (data.get("last") or {}).items() if isinstance(v, dict)})
        self._last_loaded = True

    async def _save_last(self) -> None:
        await self._store().async_save({"last": self.last})

    def _undoable(self, satellite_id: str) -> bool:
        last = self.last.get(satellite_id)
        return bool(last and last.get("inverse") and time.time() - last.get("at", 0) <= UNDO_SECONDS)

    async def _confirm(self, runtime: dict, key: tuple, satellite_id: str, lang: str, approved: set[str], rt_lists: dict):
        p = self.pending.pop(key, None)
        if p is None or p.until < time.monotonic():
            return say("learn_expired", lang), lang, False
        from .accept import AcceptError, async_accept, queue_of  # noqa: PLC0415
        from .guard import async_guarded_update  # noqa: PLC0415
        from .proposals import apply_op  # noqa: PLC0415
        from .voice_rules import check_voice_item, get_rate  # noqa: PLC0415

        queue = queue_of(self.hass, runtime)
        if p.action == "undo":
            undo = p.undo or {}
            if runtime["source"].kind != _catalog():
                return say("learn_panel_only", lang), lang, False

            def _mutate(data: dict) -> None:
                apply_op(data, undo["inverse"])  # exactly what that acceptance added, nothing else

            try:
                await async_guarded_update(self.hass, runtime, _mutate, actor=f"voice-undo:{satellite_id}")
            except Exception:  # noqa: BLE001
                return say("learn_failed", lang), lang, False
            self.last.pop(satellite_id, None)
            await self._save_last()
            return say("learn_undone", lang), lang, False

        item = next((i for i in await queue.async_items() if i["id"] == p.item_id), None)
        if item is None or item_hash(item) != p.item_hash:
            return say("learn_gone", lang), lang, False
        config = runtime["matcher"].config
        delta = _added(item, config)  # what this acceptance REALLY adds (for an exact undo)
        rate = get_rate(self.hass, self.entry_id)
        async with rate.lock:
            if await rate.async_left() <= 0:
                return say("learn_rate", lang), lang, False

            def extra(it: dict, data: dict) -> str | None:
                return check_voice_item(self.hass, it, data, approved_satellites=approved,
                                        entry_id=self.entry_id, runtime_lists=rt_lists)

            try:
                result = await async_accept(self.hass, runtime, [item["id"]], actor=f"voice:{satellite_id}",
                                            force=False, extra_reason=extra, base_rev=p.rev)
            except AcceptError:
                return say("learn_panel_only", lang), lang, False
            if not (result.get("saved") and result.get("applied") == [item["id"]] and not result.get("refused")):
                return say("learn_failed", lang), lang, False
            await rate.async_charge()
        if delta is not None:
            # describe only what the undo will remove
            shown = {"op": item["op"], "payload": {**(item.get("payload") or {}), **{k: v for k, v in delta["payload"].items() if k in ("sentences", "names")}}}
            self.last[satellite_id] = {"at": time.time(), "what": self._describe(shown, lang, config), "inverse": delta}
            await self._save_last()
        return say("learn_accepted", lang), lang, False


def _catalog() -> str:
    from .const import SOURCE_CATALOG  # noqa: PLC0415

    return SOURCE_CATALOG


def _example(sentences: list[str], lang: str, slot_lists: dict | None = None, rules: dict | None = None) -> str:
    """A speakable example of a template ("(an|ein)schalten [bitte]" → "anschalten")."""
    from hassil.parse_expression import parse_sentence  # noqa: PLC0415

    from .proposals import bounded_samples  # noqa: PLC0415

    for template in sentences:
        try:
            samples = bounded_samples(parse_sentence(template), slot_lists or {}, rules or {}, lang, 1)
        except Exception:  # noqa: BLE001
            samples = []
        if samples:
            return samples[0]
    return " / ".join(sentences)


def _added(item: dict[str, Any], config: dict[str, Any]) -> dict[str, Any] | None:
    """The counter-operation of only what is NEW (existing sentences/names stay on undo)."""
    p = item.get("payload") or {}
    if item.get("op") == "add_sentences":
        have = set((((config.get("intents") or {}).get(p.get("intent")) or {}).get("sentences") or {}).get(p.get("lang"), []) or [])
        new = [x for x in p.get("sentences") or [] if x not in have]  # exact, as apply_op adds
        return {"op": "remove_sentences", "payload": {**p, "sentences": new}} if new else None
    if item.get("op") == "add_names":
        have = set(((config.get("names") or {}).get(p.get("target")) or {}).get(p.get("lang")) or [])
        new = [x for x in p.get("names") or [] if x not in have]  # exact, as apply_op adds
        return {"op": "remove_names", "payload": {**p, "names": new}} if new else None
    return None


def get_learn(hass: HomeAssistant, entry_id: str) -> VoiceLearn:
    from .const import DOMAIN  # noqa: PLC0415

    store = hass.data.setdefault(f"{DOMAIN}_learn", {})
    if entry_id not in store:
        store[entry_id] = VoiceLearn(hass, entry_id)
    return store[entry_id]
