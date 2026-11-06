#!/usr/bin/env python3
"""Offline regression runner for Polyglot Assist intents files.

Uses the integration's own ``matcher.py`` and ``corpus.py`` (no Home Assistant
needed, only ``hassil`` and ``PyYAML``), so results equal the runtime matcher.

  # validate an intents file (unknown lists/rules, counts)
  polyglot_test.py validate intents.yaml

  # run a corpus, exit code 1 on any failure
  polyglot_test.py run intents.yaml tests.yaml

  # write a corpus snapshot of the current behaviour for a list of sentences
  # (one per line) — the baseline for "no regression" before an edit
  polyglot_test.py snapshot intents.yaml sentences.txt > tests.yaml

Pass --hint <lang> to mirror the pipeline language (the runtime tries that
language first). Unlike the runtime, which skips a language hassil cannot
parse, the CLI fails on it — on purpose, so broken files are noticed.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import yaml

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "polyglot_assist"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_pg_{name}", PKG / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


corpus = _load("corpus")
matcher_mod = _load("matcher")


def build_matcher(intents_path: str):
    data = yaml.safe_load(Path(intents_path).read_text("utf-8")) or {}
    return data, matcher_mod.MultilangMatcher(corpus.build_intents(data))


def match_fn(matcher):
    def _fn(text: str, hint: str | None = None):
        res = matcher.match(text, hint)
        if res is None:
            return None
        return res.intent_name, res.lang, {k: v.get("value") for k, v in res.slots.items()}

    return _fn


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate")
    v.add_argument("intents")
    v.add_argument("--extra-lists", default="", help="comma-separated lists supplied at runtime (e.g. area,name)")
    r = sub.add_parser("run")
    r.add_argument("intents")
    r.add_argument("corpus")
    r.add_argument("--json", action="store_true")
    r.add_argument("--hint", help="pipeline language passed to the matcher (runtime: user_input.language)")
    s = sub.add_parser("snapshot")
    s.add_argument("intents")
    s.add_argument("sentences")
    s.add_argument("--hint", help="pipeline language passed to the matcher")
    args = ap.parse_args()

    if args.cmd == "validate":
        data = yaml.safe_load(Path(args.intents).read_text("utf-8")) or {}
        extra = {x for x in args.extra_lists.split(",") if x}
        problems = corpus.validate(data, extra_lists=extra)
        print(json.dumps(corpus.count_summary(data), ensure_ascii=False))
        for p in problems:
            print("WARN", p)
        return 1 if problems else 0

    _, matcher = build_matcher(args.intents)
    fn = match_fn(matcher)

    if args.cmd == "snapshot":
        cases = []
        for line in Path(args.sentences).read_text("utf-8").splitlines():
            text = line.strip()
            if not text:
                continue
            got = fn(text, args.hint)
            case = {"text": text, "intent": got[0] if got else None}
            if args.hint:
                case["hint"] = args.hint
            if got:
                case["lang"] = got[1]
                if got[2]:
                    case["slots"] = got[2]
            cases.append(case)
        yaml.safe_dump({"cases": cases}, sys.stdout, allow_unicode=True, sort_keys=False, width=200)
        return 0

    cases = corpus.parse_corpus(yaml.safe_load(Path(args.corpus).read_text("utf-8")) or {})
    report = corpus.run_corpus(fn, cases, args.hint)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        print(f"{report['passed']}/{report['total']} passed")
        for f in report["failed"]:
            print(f"FAIL {f['text']!r}: expected {f['expected']} got {f['got']}")
    return 0 if not report["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
