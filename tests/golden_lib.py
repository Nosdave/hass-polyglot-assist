"""Replays recorded AI answers through Polyglot's real proposal checks (no HA needed).

Mirrors translate.async_propose_sentences: the same source sentences, the same
matcher (every language, target language first), the same samples of the other
commands and the same check_proposal call.
"""
from __future__ import annotations

import ast
import copy
import importlib.util
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "custom_components" / "polyglot_assist"


def _load(name: str):
    key = f"_pg_{name}"
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, PKG / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod
    spec.loader.exec_module(mod)
    return mod


def prompt_constants() -> tuple[str, str]:
    """_SYNTAX and _FENCE of translate.py (read, not imported: it needs HA)."""
    tree = ast.parse((PKG / "translate.py").read_text("utf-8"))
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) in ("_SYNTAX", "_FENCE"):
            found[node.targets[0].id] = ast.literal_eval(node.value)
    return found["_SYNTAX"], found["_FENCE"]


def without_language(config: dict, lang: str) -> dict:
    """The catalog as if ``lang`` had never been added (what a translation starts from)."""
    out = copy.deepcopy(config)
    for cfg in (out.get("intents") or {}).values():
        if isinstance(cfg, dict):
            (cfg.get("sentences") or {}).pop(lang, None)
    return out


def check(config: dict, intent: str, lang: str, proposals: list[str]) -> list[dict]:
    corpus, matcher_mod, proposals_mod = _load("corpus"), _load("matcher"), _load("proposals")
    from hassil.parse_expression import parse_sentence  # noqa: PLC0415

    import yaml  # noqa: PLC0415

    matcher = matcher_mod.MultilangMatcher(corpus.build_intents(config))
    fixture = yaml.safe_load((Path(__file__).parent / "fixture_example.yaml").read_text("utf-8"))
    slot_lists = {**matcher.static_slot_lists(), **corpus.slot_lists_from_fixture(fixture)}  # runtime lists ({area} …)
    cfg = config["intents"][intent]
    source = [s for lst in (cfg.get("sentences") or {}).values() for s in (lst or []) if isinstance(s, str)]
    rules = corpus.string_rules(config)
    parsed_rules = proposals_mod.parse_rules(rules)
    per_lang = corpus.bucket_by_language(config)
    other = []
    for name, c in [(n, c) for bucket in per_lang.values() for n, c in bucket.items()]:
        for s in c["data"][0]["sentences"][:6]:
            try:
                for t in proposals_mod.bounded_samples(parse_sentence(s), slot_lists, parsed_rules, lang, 2):
                    other.append((name, t))
            except Exception:  # noqa: BLE001
                continue
    out = []
    for p in proposals:
        p = proposals_mod.clean_alternatives(p)
        r = proposals_mod.check_proposal(
            intent, p, source, lambda text: matcher.match_all(text, lang, slot_lists),
            slot_lists, rules, lang, other, corpus.static_lists(config),
        )
        out.append({"sentence": p, "status": r["status"]})
    return out
