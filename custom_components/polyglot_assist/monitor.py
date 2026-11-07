"""Optional voice-flow monitoring with the household's own AI task.

When switched on, Polyglot keeps a local log of the last ``MAX_LOG`` requests
it saw (stays in Home Assistant, no audio): the sentence, hit or handed to the
assistant, the tools the assistant called (shortened arguments), the first
200 characters of its answer (which may contain web results), errors,
duration and the device id. Switching monitoring off deletes the log. Once a day (configurable) it evaluates the log:

* sentences that repeatedly needed the assistant although they led to a
  plain device action → proposals for Polyglot sentences (via the AI task
  without tools; every proposal validated like any other);
* likely misunderstandings: a correction ("nein", "no", "non", "ich hab
  gesagt" …) shortly after an answer;

and writes proposals (with the evidence: which sentences, when) to the queue
plus a short summary.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .corpus import bucket_by_language, string_rules
from .proposals import bounded_samples, check_proposal, norm, parse_rules

_LOGGER = logging.getLogger(__name__)

MAX_LOG = 500
CORRECTION_WINDOW = timedelta(seconds=90)
CORRECTION_RE = re.compile(
    r"^(nein|falsch|ich hab(e)? (doch )?gesagt|ich meinte|nicht (das|den|die)|"
    r"no[,!]|wrong|i said|i meant|non[,!]|pas (ça|celui)|je voulais dire)",
    re.I,
)


class Monitor:
    """Rolling local log + daily evaluation for one entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.monitor.{entry_id}", atomic_writes=True)
        self._log: list[dict[str, Any]] | None = None
        self._summary: dict[str, Any] = {}
        self._unsub: CALLBACK_TYPE | None = None
        self._active = True

    async def async_load(self) -> None:
        stored = await self._store.async_load() or {}
        self._log = list(stored.get("log") or [])[-MAX_LOG:]
        self._summary = stored.get("summary") or {}

    @property
    def log(self) -> list[dict[str, Any]]:
        return list(self._log or [])

    @property
    def summary(self) -> dict[str, Any]:
        return dict(self._summary)

    @callback
    def async_record(self, **entry: Any) -> None:
        """Add one request (event loop; saved with a delay)."""
        if self._log is None or not self._active:
            return
        self._log.append({"ts": dt_util.utcnow().isoformat(), **entry})
        del self._log[:-MAX_LOG]
        self._store.async_delay_save(lambda: {"log": self._log, "summary": self._summary}, 30)

    @callback
    def async_schedule(self, at: str, runner) -> None:
        """Run ``runner`` daily at HH:MM."""
        self.async_stop()
        try:
            hour, minute = (int(x) for x in at.split(":", 1))
        except ValueError:
            hour, minute = 3, 0
        self._unsub = async_track_time_change(self.hass, runner, hour=hour, minute=minute, second=0)

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def async_deactivate(self) -> None:
        """No more recording or saving (unload, opt-out); in-flight requests too."""
        self.async_stop()
        self._active = False

    async def async_flush(self) -> None:
        """Write pending changes now (on unload) so no timer writes later."""
        if self._log is not None and self._active:
            await self._store.async_save({"log": self._log, "summary": self._summary})

    async def async_clear(self) -> None:
        self._log = []
        self._summary = {}
        await self._store.async_remove()

    # ── evaluation ──────────────────────────────────────────────────────

    def window(self, hours: int = 24) -> list[dict[str, Any]]:
        since = dt_util.utcnow() - timedelta(hours=hours)
        return [e for e in self.log if dt_util.parse_datetime(e["ts"]) and dt_util.parse_datetime(e["ts"]) >= since]

    @staticmethod
    def find_corrections(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for prev, cur in zip(entries, entries[1:]):
            if prev.get("device") != cur.get("device"):
                continue
            t_prev, t_cur = dt_util.parse_datetime(prev["ts"]), dt_util.parse_datetime(cur["ts"])
            if t_prev and t_cur and t_cur - t_prev <= CORRECTION_WINDOW and CORRECTION_RE.search(cur.get("text", "").strip()):
                out.append({"before": prev.get("text"), "answer": prev.get("speech"), "correction": cur.get("text"),
                            "ts": cur["ts"]})
        return out

    @staticmethod
    def find_repeated(entries: list[dict[str, Any]], min_count: int = 2) -> list[dict[str, Any]]:
        """Assistant-handled sentences that led to a device action, ≥ min_count times."""
        groups: dict[str, list[dict[str, Any]]] = {}
        for e in entries:
            if e.get("result") == "assistant" and e.get("targets"):
                groups.setdefault(norm(e.get("text", "")), []).append(e)
        out = []
        for key, items in groups.items():
            if not key or len(items) < min_count:
                continue
            times = [t for t in (dt_util.parse_datetime(i["ts"]) for i in items) if t]
            out.append({
                "text": items[-1]["text"], "count": len(items), "targets": items[-1]["targets"],
                "lang": items[-1].get("lang"), "times": [i["ts"] for i in items][-5:],
                # where it came from — the voice path accepts only repeated, spread-out
                # sentences from approved satellites (see learn.py)
                "satellites": sorted({i["satellite"] for i in items if i.get("satellite")}),
                "from_other": sum(1 for i in items if not i.get("satellite")),
                "conversations": len({i.get("conversation") for i in items if i.get("conversation")}),
                "span_s": int((max(times) - min(times)).total_seconds()) if len(times) > 1 else 0,
            })
        return out

    def stats(self, entries: list[dict[str, Any]]) -> dict[str, Any]:
        hits = [e for e in entries if e.get("result") == "hit"]
        llm = [e for e in entries if e.get("result") == "assistant"]
        return {
            "total": len(entries), "hits": len(hits), "assistant": len(llm),
            "errors": sum(1 for e in entries if e.get("error")),
            "hit_ms_p50": _p50([e.get("ms") for e in hits]),
            "assistant_ms_p50": _p50([e.get("ms") for e in llm]),
        }

    async def async_evaluate(self, runtime: dict[str, Any], propose) -> dict[str, Any]:
        """Evaluate the last 24 h; ``propose(repeated)`` returns queue items."""
        entries = self.window(24)
        repeated = self.find_repeated(entries)
        corrections = self.find_corrections(entries)
        items: list[dict[str, Any]] = []
        error = None
        if repeated:
            try:
                items = await propose(repeated)
            except Exception as err:  # noqa: BLE001 — the summary still counts
                error = str(err)[:200]
                _LOGGER.warning("Polyglot Assist: monitoring proposals failed: %s", err)
        self._summary = {
            "when": dt_util.utcnow().isoformat(),
            **self.stats(entries),
            "repeated": repeated[:10],
            "corrections": corrections[:10],
            "proposals": len(items),
            **({"error": error} if error else {}),
        }
        if self._active:
            self._store.async_delay_save(lambda: {"log": self._log, "summary": self._summary}, 1)
        return {"summary": self._summary, "items": items}


def _p50(values: list[Any]) -> float | None:
    nums = sorted(v for v in values if isinstance(v, (int, float)))
    return round(nums[len(nums) // 2], 1) if nums else None


_ENTITY_ID = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")


def _other_samples(config: dict[str, Any], slot_lists: dict, parsed_rules: dict, lang: str, skip: str) -> list[tuple[str, str]]:
    """(intent, text) samples of the OTHER commands — for the reverse check."""
    from hassil.parse_expression import parse_sentence  # noqa: PLC0415

    out: list[tuple[str, str]] = []
    for bucket in bucket_by_language(config).values():
        for name, cfg in bucket.items():
            if name == skip:
                continue
            for s in cfg["data"][0]["sentences"][:6]:
                try:
                    out.extend((name, t) for t in bounded_samples(parse_sentence(s), slot_lists, parsed_rules, lang, 2))
                except Exception:  # noqa: BLE001
                    continue
    return out


def _all_list_refs(expr: Any, parsed_rules: dict, _depth: int = 0) -> list[str]:
    """List references of a sentence, following referenced <rules> (bounded)."""
    from hassil.expression import ListReference, RuleReference, Sentence  # noqa: PLC0415

    if expr is None or _depth > 10:
        return []
    if isinstance(expr, Sentence):
        return _all_list_refs(expr.expression, parsed_rules, _depth + 1)
    if isinstance(expr, ListReference):
        return [expr.list_name]
    if isinstance(expr, RuleReference):
        return _all_list_refs(parsed_rules.get(expr.rule_name), parsed_rules, _depth + 1)
    out: list[str] = []
    for item in getattr(expr, "items", None) or []:
        out += _all_list_refs(item, parsed_rules, _depth + 1)
    return out


def _template_matches(template: str, lang: str, text: str, slot_lists: dict, rules: dict[str, str]) -> bool:
    """Does the template itself recognise ``text`` (the sentence actually spoken)?"""
    from hassil.intents import Intents  # noqa: PLC0415
    from hassil.recognize import recognize_all  # noqa: PLC0415

    try:
        intents = Intents.from_dict({"language": lang, "intents": {"_t": {"data": [{"sentences": [template]}]}},
                                     **({"expansion_rules": rules} if rules else {})})
        return any(True for _ in recognize_all(text, intents, slot_lists=slot_lists))
    except Exception:  # noqa: BLE001
        return False


def _harden(check: dict[str, Any], *, name: str, cfg: dict[str, Any], template: str, lang: str, evidence: list[dict[str, Any]],
            slot_lists: dict, rules: dict[str, str], builtin_match, names_of) -> dict[str, Any]:
    """Extra rules for proposals that come from strangers' sentences (monitoring)."""
    from hassil.intents import WildcardSlotList  # noqa: PLC0415
    from hassil.parse_expression import parse_sentence  # noqa: PLC0415

    def fail(note: str) -> dict[str, Any]:
        return {**check, "status": "error", "notes": [*check.get("notes", []), note]}

    if check.get("status") == "error":
        return check
    try:
        parsed = parse_sentence(template)
    except Exception:  # noqa: BLE001
        return fail("not parsable")
    if any(isinstance(slot_lists.get(r), WildcardSlotList) for r in _all_list_refs(parsed, parse_rules(rules))):
        return fail("free-text placeholders are not allowed in learned sentences")
    # bound to the evidence: the template must recognise a sentence that was really said,
    # and only THOSE sentences count for the target check below
    evidence = [e for e in evidence if e.get("text") and _template_matches(template, lang, e["text"], slot_lists, rules)]
    if not evidence:
        return fail("does not match the sentences that were actually said")
    # must not take over Home Assistant's built-in commands
    if builtin_match is not None:
        for sample in bounded_samples(parsed, slot_lists, parse_rules(rules), lang):  # same sample set as the normal check
            if (hit := builtin_match(sample, lang)) is not None:
                return fail(f"would take over a Home Assistant command ({hit}) for “{sample}”")
    # the command must switch what the assistant switched back then
    def warn(note: str) -> dict[str, Any]:
        return {**check, "status": "warn" if check.get("status") == "ok" else check.get("status"),
                "notes": [*check.get("notes", []), note]}

    if not cfg.get("action"):
        return warn("what this command switches is not known (intent_script) — review it")
    entities = _action_entities(cfg.get("action"))
    if entities is None:
        return warn("the command's targets cannot be fully checked (template, nested steps or area/device targets) — review it")
    if not entities:
        return warn("the command names no device (e.g. it runs a script) — review it")
    calls = [t.get("args") for e in evidence for t in (e.get("targets") or [])]
    said = {_n(v) for args in calls for v in _target_values(args)}
    if any(_area_only(args) for args in calls):
        return warn("the assistant switched a whole area or floor — review which devices it means")
    if entities and said and names_of is not None:
        # EVERY target of the command must be one the assistant switched for these sentences
        # (complete names / ids only — "couch" is not "couch strip")
        extra = [e for e in entities if not {_n(n) for n in [e, *names_of(e)] if n} & said]
        if extra:
            return fail(f"this command switches something else than the assistant did ({', '.join(extra[:3])})")
    elif entities and not said:
        return warn("the assistant's tool calls name no target — review it")
    return check


def _n(text: Any) -> str:
    return " ".join(str(text).casefold().split())


_PLAIN_STEP_KEYS = {"action", "service", "target", "data", "entity_id", "alias", "metadata", "enabled", "continue_on_error"}
_OTHER_TARGETS = ("area_id", "device_id", "floor_id", "label_id")


def _action_entities(action: Any) -> list[str] | None:
    """Plain entity ids of plain service steps; None if anything cannot be fully inspected
    (templates, nested blocks such as parallel/choose, area/device/floor/label targets)."""
    found: list[str] = []
    for step in action if isinstance(action, list) else [action]:
        if not isinstance(step, dict) or set(step) - _PLAIN_STEP_KEYS:
            return None
        for container in (step, step.get("target") or {}, step.get("data") or {}):
            if isinstance(container, dict) and any(k in container for k in _OTHER_TARGETS):
                return None
            value = container.get("entity_id") if isinstance(container, dict) else None
            for v in value if isinstance(value, list) else ([value] if value is not None else []):
                if not isinstance(v, str) or "{" in v or not _ENTITY_ID.match(v.strip()):
                    return None
                found.append(v.strip())
    return sorted(set(found))


def _area_only(args: Any) -> bool:
    """A recorded call that targets an area/floor (not named devices)."""
    import ast  # noqa: PLC0415

    try:
        data = ast.literal_eval(args) if isinstance(args, str) else args
    except (ValueError, SyntaxError):
        return False
    return isinstance(data, dict) and any(data.get(k) for k in ("area", "floor")) and not any(data.get(k) for k in ("name", "entity_id"))


def _target_values(args: Any) -> list[str]:
    """Target words of one recorded tool call (names, areas, entity ids), as complete values."""
    import ast  # noqa: PLC0415

    data: Any = args
    if isinstance(args, str):
        try:
            data = ast.literal_eval(args)
        except (ValueError, SyntaxError):
            return [a or b for a, b in re.findall(r"'([^']+)'|\"([^\"]+)\"", args)]
    out: list[str] = []
    if isinstance(data, dict):
        for key in ("name", "entity_id"):
            v = data.get(key)
            out += [x for x in (v if isinstance(v, list) else [v]) if isinstance(x, str)]
    return out


def build_monitor_items(
    repeated: list[dict[str, Any]],
    suggestions: list[str],
    config: dict[str, Any],
    matches,
    slot_lists: dict,
    builtin_match=None,
    names_of=None,
) -> list[dict[str, Any]]:
    """Turn ``INTENT | LANG | TEMPLATE`` lines from the AI task into queue items.

    Besides the normal checks, every item must recognise a sentence that was
    really said, switch what the assistant switched, not swallow other or
    built-in commands, and use no free-text placeholder.
    """
    intents = config.get("intents") or {}
    rules = string_rules(config)  # the user's <rules> are known, unknown ones are refused
    evidence = "; ".join(f"„{r['text']}“ {r['count']}×" for r in repeated[:5])
    items: list[dict[str, Any]] = []
    for line in suggestions[:20]:
        parts = [p.strip() for p in line.split("|", 2)]  # the template may contain |
        if len(parts) != 3:
            continue
        name, lang, template = parts
        lang = lang.lower()
        if not re.match(r"^[a-z]{2,3}(-[a-z]{2,4})?$", lang):
            continue  # untrusted model output
        cfg = intents.get(name)
        if not isinstance(cfg, dict) or not template:
            continue
        source = [s for lst in (cfg.get("sentences") or {}).values() for s in (lst or []) if isinstance(s, str)]
        parsed_rules = parse_rules(rules)
        check = check_proposal(name, template, source, lambda t, lang=lang: matches(t, lang), slot_lists, rules, lang,
                               _other_samples(config, slot_lists, parsed_rules, lang, name))
        mine = [r for r in repeated if (r.get("lang") or lang) == lang] or repeated
        check = _harden(check, name=name, cfg=cfg, template=template, lang=lang, evidence=mine,
                        slot_lists=slot_lists, rules=rules, builtin_match=builtin_match, names_of=names_of)
        items.append({
            "op": "add_sentences",
            "payload": {"intent": name, "lang": lang, "sentences": [template]},
            "title": f"{name} ({lang}): {template}",
            "origin": {"source": "monitor", "detail": f"ging wiederholt an den Assistenten: {evidence}",
                       "evidence": repeated[:5]},
            "check": {"status": check["status"], "notes": check["notes"]},
        })
    return items
