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
           ("dritte", 3), ("dritten", 3), ("dritter", 3), ("vierte", 4), ("vierten", 4), ("fünfte", 5), ("fünften", 5)],
    "en": [("first", 1), ("second", 2), ("third", 3), ("fourth", 4), ("fifth", 5)],
    "fr": [("premier", 1), ("première", 1), ("deuxième", 2), ("second", 2), ("troisième", 3), ("quatrième", 4), ("cinquième", 5)],
}
_SENTENCES = {
    "de": {
        "list": ["was hast du gelernt", "(welche|gibt es) [neue] vorschläge", "was hast du [neu] gelernt"],
        "detail": ["(erzähl|sag) mir mehr [zum|zu dem|über den] {pg_nth} [vorschlag]", "was ist der {pg_nth} [vorschlag]"],
        "accept": ["übernimm (ihn|den|das|es)", "übernimm den {pg_nth} [vorschlag]"],
        "reject": ["verwirf (ihn|den|das|es)", "verwirf den {pg_nth} [vorschlag]"],
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
        "detail": ["dis-m'en plus sur la {pg_nth} [suggestion]", "dis-m'en plus sur le {pg_nth}", "quelle est la {pg_nth} [suggestion]"],
        "accept": ["(accepte|applique)[-la|-le]", "(accepte|applique) la {pg_nth} [suggestion]"],
        "reject": ["(rejette|refuse)[-la|-le]", "(rejette|refuse) la {pg_nth} [suggestion]"],
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
    last: dict[str, dict[str, Any]] = field(default_factory=dict)  # satellite → last voice acceptance

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
    def _describe(item: dict[str, Any], lang: str) -> str:
        p = item.get("payload") or {}
        if item.get("op") == "add_names":
            return say("learn_item_name", lang, names=" / ".join(p.get("names") or []), target=str(p.get("target", "")))
        return say("learn_item_sentence", lang, sentence=" / ".join(p.get("sentences") or []), command=str(p.get("intent", "")))

    async def async_handle(
        self, runtime: dict, *, text: str, conversation_id: str | None, satellite_id: str | None,
        approved: set[str], languages: list[str],
    ) -> tuple[str, str, bool] | None:
        """(speech, lang, ask_again) for a learn command, None if this is not one."""
        if not satellite_id or satellite_id not in approved:
            return None
        hit = await self.hass.async_add_executor_job(self.recognize, text, languages)
        key = self._key(conversation_id, satellite_id)
        if hit is None:
            self.pending.pop(key, None)  # anything else ends an open question
            return None
        kind, lang, nth = hit
        if kind in ("yes", "no") and not self.has_pending(conversation_id, satellite_id):
            return None  # a plain "yes" is not ours unless we asked
        from .accept import queue_of  # noqa: PLC0415

        queue = queue_of(self.hass, runtime)
        config = runtime["matcher"].config
        rt_lists = runtime["lists"].async_get()

        if kind == "list":
            offer = await self.hass.async_add_executor_job(
                self._offerable, await queue.async_items(), config, approved, rt_lists)
            self.listing[key] = (time.monotonic() + LISTING_SECONDS, [i["id"] for i in offer])
            if not offer:
                return say("learn_none", lang), lang, False
            parts = [f"{n}. {self._describe(i, lang)}." for n, i in enumerate(offer, 1)]
            return say("learn_list_one" if len(offer) == 1 else "learn_list", lang,
                       count=str(len(offer)), items=" ".join(parts)), lang, False

        if kind == "later":
            self.pending.pop(key, None)
            return say("learn_later", lang), lang, False

        if kind == "no":
            self.pending.pop(key, None)
            return say("learn_nothing_changed", lang), lang, False

        if kind == "yes":
            return await self._confirm(runtime, key, satellite_id, lang, approved, rt_lists)

        if kind == "undo":
            last = self.last.get(satellite_id)
            if not last or time.time() - last["at"] > UNDO_SECONDS:
                return say("learn_nothing_to_undo", lang), lang, False
            self.pending[key] = _Pending("undo", None, None, None, time.monotonic() + CONFIRM_SECONDS, undo=last)
            return say("learn_confirm_undo", lang, what=last["what"]), lang, True

        # detail / accept / reject: which item?
        until, ids = self.listing.get(key, (0, []))
        if until < time.monotonic() or not ids:
            return say("learn_ask_list_first", lang), lang, False
        index = (nth or 1) - 1
        if index >= len(ids):
            return say("learn_no_such", lang), lang, False
        item = next((i for i in await queue.async_items() if i["id"] == ids[index]), None)
        if item is None:
            return say("learn_gone", lang), lang, False
        if kind == "detail":
            origin = (item.get("origin") or {}).get("source") or "?"
            return say("learn_detail", lang, what=self._describe(item, lang), origin=say("origin_" + origin, lang) if origin in ("monitor", "translation", "starter", "new_devices") else origin), lang, False
        from .voice_rules import check_voice_item  # noqa: PLC0415

        reason = await self.hass.async_add_executor_job(
            lambda: check_voice_item(self.hass, item, config, approved_satellites=approved,
                                     entry_id=self.entry_id, runtime_lists=rt_lists))
        if kind == "accept" and reason:
            return say("learn_panel_only", lang), lang, False
        from .guard import revision  # noqa: PLC0415

        self.pending[key] = _Pending(kind, item["id"], item_hash(item), revision(config), time.monotonic() + CONFIRM_SECONDS)
        what = self._describe(item, lang)
        origin = (item.get("origin") or {})
        if kind == "accept" and origin.get("source") == "monitor":
            heard = max((e.get("count", 0) for e in origin.get("evidence") or []), default=0)
            return say("learn_confirm_accept_monitor", lang, what=what, count=str(heard)), lang, True
        return say("learn_confirm_" + kind, lang, what=what), lang, True

    async def _confirm(self, runtime: dict, key: tuple, satellite_id: str, lang: str, approved: set[str], rt_lists: dict):
        p = self.pending.pop(key, None)
        if p is None or p.until < time.monotonic():
            return say("learn_expired", lang), lang, False
        from .accept import async_accept, queue_of  # noqa: PLC0415
        from .guard import async_guarded_update  # noqa: PLC0415
        from .proposals import apply_op  # noqa: PLC0415
        from .voice_rules import check_voice_item, get_rate  # noqa: PLC0415

        queue = queue_of(self.hass, runtime)
        if p.action == "undo":
            undo = p.undo or {}

            def _mutate(data: dict) -> None:
                apply_op(data, undo["inverse"])  # exactly the counter-operation, nothing else

            try:
                await async_guarded_update(self.hass, runtime, _mutate, actor=f"voice-undo:{satellite_id}")
            except Exception:  # noqa: BLE001
                return say("learn_failed", lang), lang, False
            self.last.pop(satellite_id, None)
            return say("learn_undone", lang), lang, False

        item = next((i for i in await queue.async_items() if i["id"] == p.item_id), None)
        if item is None or item_hash(item) != p.item_hash:
            return say("learn_gone", lang), lang, False
        if p.action == "reject":
            await queue.async_remove([item["id"]])
            return say("learn_rejected", lang), lang, False
        rate = get_rate(self.hass, self.entry_id)
        async with rate.lock:
            if await rate.async_left() <= 0:
                return say("learn_rate", lang), lang, False

            def extra(it: dict, data: dict) -> str | None:
                return check_voice_item(self.hass, it, data, approved_satellites=approved,
                                        entry_id=self.entry_id, runtime_lists=rt_lists)

            result = await async_accept(self.hass, runtime, [item["id"]], actor=f"voice:{satellite_id}",
                                        force=False, extra_reason=extra, base_rev=p.rev)
            if not (result.get("saved") and result.get("applied") == [item["id"]] and not result.get("refused")):
                return say("learn_failed", lang), lang, False
            await rate.async_charge()
        inverse_op = {"add_sentences": "remove_sentences", "add_names": "remove_names"}[item["op"]]
        self.last[satellite_id] = {"at": time.time(), "what": self._describe(item, lang),
                                   "inverse": {"op": inverse_op, "payload": item.get("payload") or {}}}
        return say("learn_accepted", lang), lang, False


def get_learn(hass: HomeAssistant, entry_id: str) -> VoiceLearn:
    from .const import DOMAIN  # noqa: PLC0415

    store = hass.data.setdefault(f"{DOMAIN}_learn", {})
    if entry_id not in store:
        store[entry_id] = VoiceLearn(hass, entry_id)
    return store[entry_id]
