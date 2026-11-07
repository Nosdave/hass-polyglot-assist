"""Learning from corrections — the pure part (no Home Assistant needed, fully testable).

Language-independent: no word lists. Requests of the same conversation are
cut into short *episodes* around every place where something was done and
the user spoke again shortly after. The AI task (no tools) reads them and says
whether something went wrong and what was wanted; for a few it may suggest a
NEW command. Everything it writes is parsed defensively here and validated
again by the normal proposal checks (proposals.check_action_safe/check_item,
accept.dynamic_reason, the corpus run) — an AI answer can never act.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Callable

MAX_EPISODES = 12
MAX_TURNS = 8
TURNS_BEFORE, TURNS_AFTER = 3, 2
PAIR_GAP_S = 120
MAX_TEXT = 200
MAX_INSIGHT_TEXT = 160
KINDS = ("wrong_device", "missing_command", "misheard", "wrong_value", "answer", "other")
COMMAND_KINDS = ("wrong_device", "missing_command")  # only these may lead to a command proposal
MAX_COMMAND_PROPOSALS = 3
NAME_RE = re.compile(r"^[A-Z][A-Za-z0-9]{2,59}$")
LANG_RE = re.compile(r"^[a-z]{2,3}(-[a-z]{2,4})?$")


def _ts(entry: dict[str, Any]) -> datetime | None:
    """Timestamp as an aware datetime (naive values count as UTC), or None."""
    try:
        value = datetime.fromisoformat(str(entry.get("ts")))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _gap(a: dict[str, Any], b: dict[str, Any]) -> float:
    return (_ts(b) - _ts(a)).total_seconds()


def _did_something(entry: dict[str, Any]) -> bool:
    return entry.get("result") == "hit" or bool(entry.get("targets")) or bool(entry.get("switched"))


def find_episodes(entries: list[dict[str, Any]], *, max_episodes: int = MAX_EPISODES) -> list[list[dict[str, Any]]]:
    """Short windows of a conversation where the user spoke again right after something was done.

    Candidate = request B directly after request A of the SAME conversation,
    ≤ PAIR_GAP_S apart, where A did something (a hit or a tool call). B can be
    anything — a correction may itself be a hit. Window: TURNS_BEFORE before
    A … TURNS_AFTER after B; overlapping windows are merged. Newest first.
    """
    by_conv: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        conv = e.get("conversation")
        if conv and _ts(e):
            by_conv.setdefault(str(conv), []).append(e)
    episodes: list[tuple[datetime, list[dict[str, Any]]]] = []
    for turns in by_conv.values():
        turns.sort(key=lambda e: _ts(e))
        spans: list[list[int]] = []
        for i in range(1, len(turns)):
            a, b = turns[i - 1], turns[i]
            if not _did_something(a) or _gap(a, b) > PAIR_GAP_S:
                continue
            lo, hi = i - 1, i
            # widen only across short pauses: a later, unrelated request is not part of it
            while lo > 0 and i - 1 - lo < TURNS_BEFORE and _gap(turns[lo - 1], turns[lo]) <= PAIR_GAP_S:
                lo -= 1
            while hi < len(turns) - 1 and hi - i < TURNS_AFTER and _gap(turns[hi], turns[hi + 1]) <= PAIR_GAP_S:
                hi += 1
            if spans and lo <= spans[-1][1]:
                spans[-1][1] = max(spans[-1][1], hi)
            else:
                spans.append([lo, hi])
        for lo, hi in spans:
            window = turns[lo:hi + 1]
            # a very long merged window keeps its LAST turns (the correction is usually late)
            window = window[-MAX_TURNS:]
            episodes.append((_ts(window[-1]), window))
    episodes.sort(key=lambda x: x[0], reverse=True)
    return [w for _, w in episodes[:max_episodes]]


def _clip(text: Any, n: int = MAX_TEXT) -> str:
    return " ".join(str(text or "").split())[:n]


def format_episodes(episodes: list[list[dict[str, Any]]], name_of: Callable[[str], str] | None = None) -> list[str]:
    """Data lines for the AI prompt (inside the <data> fence)."""
    name_of = name_of or (lambda e: e)
    lines: list[str] = []
    for n, episode in enumerate(episodes, 1):
        lines.append(f"E{n}:")
        for turn in episode:
            how = "own command" if turn.get("result") == "hit" else "AI"
            lang = turn.get("lang") if isinstance(turn.get("lang"), str) and LANG_RE.match(turn["lang"]) else "?"
            lines.append(f"  user [{lang}]: {_clip(turn.get('text'))}")
            done = [_clip(name_of(e), 80) for e in (turn.get("switched") or []) if isinstance(e, str)][:10]
            calls = [f"{t.get('tool')}({_clip(t.get('args'), 120)})" for t in (turn.get("targets") or [])][:5]
            what = "; ".join(x for x in ("switched: " + ", ".join(done) if done else "",
                                         "tool calls: " + ", ".join(calls) if calls else "") if x)
            lines.append(f"  {how}{' (' + what + ')' if what else ''}: {_clip(turn.get('speech')) or '–'}")
    return lines


def parse_review(lines: list[str], episodes: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """``E<n> | ok|correction | KIND | WENT_WRONG | WANTED`` → insights (corrections only)."""
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for line in lines:
        parts = [p.strip() for p in str(line).split("|")]
        if len(parts) != 5:
            continue
        m = re.fullmatch(r"E(\d{1,2})", parts[0])
        if not m or parts[1].lower() != "correction":
            continue
        idx = int(m.group(1)) - 1
        kind = parts[2].lower()
        if not 0 <= idx < len(episodes) or idx in seen or kind not in KINDS:
            continue
        went_wrong, wanted = _clip(parts[3], MAX_INSIGHT_TEXT), _clip(parts[4], MAX_INSIGHT_TEXT)
        if not went_wrong:
            continue
        seen.add(idx)
        episode = episodes[idx]
        out.append({
            "episode": idx, "kind": kind, "went_wrong": went_wrong, "wanted": wanted,
            "ts": episode[-1].get("ts"),
            # the language of the correction itself (the latest turns), not of earlier context
            "lang": next((t["lang"] for t in reversed(episode) if isinstance(t.get("lang"), str) and LANG_RE.match(t["lang"])), None),
            "said": [_clip(t.get("text"), 120) for t in episode][-4:],
            "satellites": sorted({t["satellite"] for t in episode if t.get("satellite")}),
        })
    return out


def parse_command(line: str) -> dict[str, Any] | None:
    """``NAME | LANG | TEMPLATE ; TEMPLATE … | ACTION_JSON`` → parts (None = unusable / NONE).

    Templates contain "|" themselves ((a|b) alternatives), so: name and language
    from the left, the JSON from its opening bracket after the LAST "| [" / "| {".
    """
    text = str(line).strip()
    if re.fullmatch(r"NONE\.?", text, re.I):
        return None
    head = text.split("|", 2)
    if len(head) != 3:
        return None
    name, lang, rest = head[0].strip(), head[1].strip(), head[2]
    # the JSON part: after the LAST "|" whose remainder (an optional "action_json:" label
    # stripped) is valid JSON — templates themselves contain "|" in (a|b) alternatives
    templates = action = None
    for cut in [i for i, ch in enumerate(rest) if ch == "|"][::-1]:
        tail = re.sub(r"^\s*action[_ ]?json\s*:\s*", "", rest[cut + 1:], flags=re.I).strip()
        if tail[:1] in ("[", "{"):
            try:
                action = json.loads(tail)
            except ValueError:
                continue
            templates = rest[:cut]
            break
    if templates is None:
        return None
    if not NAME_RE.match(name) or not LANG_RE.match(lang):
        return None
    sentences = [" ".join(t.split()) for t in templates.split(";") if t.strip()][:6]
    if not sentences or any(len(t) > 160 for t in sentences):
        return None
    if isinstance(action, dict):
        action = [action]
    if not isinstance(action, list):
        return None
    return {"name": name, "lang": lang, "sentences": sentences, "action": action}


def action_fingerprint(action: Any, lang: str) -> str:
    """Same switching in the same language = same proposal, whatever name the AI picks.

    Entities within a step are sorted; the order of the steps is kept.
    """
    steps = []
    for step in action if isinstance(action, list) else [action]:
        if not isinstance(step, dict):
            continue
        target = (step.get("target") or {}).get("entity_id") if isinstance(step.get("target"), dict) else None
        ents = sorted([target] if isinstance(target, str) else [e for e in (target or []) if isinstance(e, str)])
        steps.append([step.get("action"), ents, json.dumps(step.get("data") or {}, sort_keys=True)])
    raw = json.dumps([lang, steps], sort_keys=True)  # step ORDER matters (on→off ≠ off→on)
    return "fp:" + hashlib.sha256(raw.encode()).hexdigest()[:12]


def switched_along(group: str, offered: list[str], members: dict[str, set[str]]) -> list[str]:
    """Offered devices a group switches along: the device is a member, or ALL its members are.

    ``members`` maps entity id → its member entity ids (empty for a plain device).
    """
    g = members.get(group) or set()
    if not g:
        return []
    return [o for o in offered if o != group and (o in g or (bool(members.get(o)) and members[o] <= g))]
