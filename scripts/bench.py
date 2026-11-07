#!/usr/bin/env python3
"""Resource benchmark for Polyglot Assist (no Home Assistant needed).

Measures what Polyglot adds to a Home Assistant installation:

* Home Assistant's built-in commands (``home-assistant-intents``) for 1, 3 and
  8 languages: load time, memory held (tracemalloc), first recognition and
  p50/p95 per sentence;
* the user's own commands (an intents file): build time, memory, p50/p95.

Only ``hassil``, ``home-assistant-intents`` and ``PyYAML`` are required — the
packages Home Assistant ships — so it runs on the target hardware (HA Green,
Raspberry Pi, a VM) inside the HA container or any venv:

  python3 scripts/bench.py [--intents config_example/intents.yaml] [--json out.json]

Every language set runs in its own fresh process (comparable memory, cold
first sentence). "in use" includes the caches built while recognising.
"hit" = a sentence recognised in its own language; "miss" = a sentence no
language knows, tried in every enabled language one after the other — what
Polyglot does at runtime before handing a sentence to the AI. The name/area
lists are empty placeholders, so device commands mostly take the miss path
inside hassil too. CI runners are much faster than an HA Green or a
Raspberry Pi — run it on the target hardware for real numbers.
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import platform
import statistics
import sys
import time
import tracemalloc
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "polyglot_assist"

SENTENCES = {
    "de": ["schalte das licht in der küche an", "wie spät ist es", "stell einen timer auf fünf minuten",
           "mach das licht im wohnzimmer aus", "wie ist das wetter", "dimme das licht auf zwanzig prozent"],
    "en": ["turn on the kitchen light", "what time is it", "set a timer for five minutes",
           "turn off the living room light", "what's the weather like", "set the light to twenty percent"],
    "fr": ["allume la lumière de la cuisine", "quelle heure est-il", "mets un minuteur de cinq minutes",
           "éteins la lumière du salon", "quel temps fait-il", "règle la lumière à vingt pour cent"],
}
LANG_SETS = (["de"], ["de", "en", "fr"], ["de", "en", "fr", "nl", "es", "it", "pt", "pl"])


def _allowed_intents() -> set[str]:
    """ALLOWED_INTENTS from builtin.py (read, not imported: that module needs HA)."""
    tree = ast.parse((PKG / "builtin.py").read_text("utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "ALLOWED_INTENTS" for t in node.targets):
            return set(ast.literal_eval(node.value))
    raise SystemExit("ALLOWED_INTENTS not found in builtin.py")


def _empty_list(name: str):
    from hassil.intents import TextSlotList  # noqa: PLC0415

    return TextSlotList.from_tuples([("xyzzy", "xyzzy")], allow_template=False, name=name)


def _pct(values: list[float], q: float) -> float:
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def _build_builtin(langs: list[str]) -> dict:
    from hassil.intents import Intents  # noqa: PLC0415
    from home_assistant_intents import get_intents  # noqa: PLC0415

    allowed = _allowed_intents()
    loaded = {}
    for lang in langs:
        data = get_intents(lang)
        if not data:
            continue
        built = Intents.from_dict({
            "language": lang,
            "intents": {k: v for k, v in data["intents"].items() if k in allowed},
            "lists": data.get("lists") or {}, "expansion_rules": data.get("expansion_rules") or {},
            "skip_words": data.get("skip_words") or [],
        })
        for sl in built.slot_lists.values():
            try:
                sl.get_candidates("")
            except Exception:  # noqa: BLE001
                pass
        loaded[lang] = (built, data.get("skip_words") or [])
    return loaded


def _recognize_all(loaded: dict, lists: dict, rounds: int) -> list[float]:
    from hassil.recognize import recognize_best  # noqa: PLC0415

    times = []
    for _ in range(rounds):
        for lang, sentences in SENTENCES.items():
            if lang not in loaded:
                continue
            intents, skip = loaded[lang]
            for text in sentences:
                t = time.perf_counter()
                recognize_best(text, intents, slot_lists=lists, skip_words=skip, language=lang)
                times.append((time.perf_counter() - t) * 1000)
    return times


MISSES = ["erzähl mir einen witz über katzen", "tell me a joke about cats", "raconte-moi une blague sur les chats"]


def _miss_all(loaded: dict, lists: dict, rounds: int) -> list[float]:
    """A sentence nobody knows, tried in every loaded language (per utterance)."""
    from hassil.recognize import recognize_best  # noqa: PLC0415

    times = []
    for _ in range(rounds):
        for text in MISSES:
            t = time.perf_counter()
            for lang, (intents, skip) in loaded.items():
                recognize_best(text, intents, slot_lists=lists, skip_words=skip, language=lang)
            times.append((time.perf_counter() - t) * 1000)
    return times


def bench_builtin(langs: list[str], rounds: int) -> dict:
    """Run in a fresh process: cold timing first, then a separate memory pass."""
    lists = {n: _empty_list(n) for n in ("name", "area", "floor")}
    t0 = time.perf_counter()
    loaded = _build_builtin(langs)
    load_s = time.perf_counter() - t0
    first = _recognize_all({k: v for k, v in list(loaded.items())[:1]}, lists, 1)[:1]  # cold
    hits = _recognize_all(loaded, lists, rounds)
    misses = _miss_all(loaded, lists, rounds)
    del loaded
    tracemalloc.start()  # memory pass (slows everything down — no timing here)
    mem_loaded = _build_builtin(langs)
    held = tracemalloc.get_traced_memory()[0]
    _recognize_all(mem_loaded, lists, 1)
    _miss_all(mem_loaded, lists, 1)
    used, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {"languages": list(mem_loaded), "load_s": round(load_s, 3), "memory_mb": round(held / 2**20, 1),
            "memory_in_use_mb": round(used / 2**20, 1), "memory_peak_mb": round(peak / 2**20, 1),
            "first_ms": round(first[0], 1) if first else None,
            "hit_p50_ms": round(statistics.median(hits), 2) if hits else None, "hit_p95_ms": round(_pct(hits, 0.95), 2) if hits else None,
            "miss_p50_ms": round(statistics.median(misses), 2), "miss_p95_ms": round(_pct(misses, 0.95), 2)}


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_pg_{name}", PKG / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def bench_own(intents_file: Path, rounds: int) -> dict:
    import yaml  # noqa: PLC0415

    sys.modules.setdefault("polyglot_assist", type(sys)("polyglot_assist"))
    corpus = _load("corpus")
    matcher_mod = _load("matcher")
    data = yaml.safe_load(intents_file.read_text("utf-8")) or {}
    tracemalloc.start()
    t0 = time.perf_counter()
    matcher = matcher_mod.MultilangMatcher(corpus.build_intents(data))
    build_s = time.perf_counter() - t0
    held, _ = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    lists = matcher.static_slot_lists()
    times = []
    for _ in range(rounds):
        for lang, sentences in SENTENCES.items():
            for text in sentences:
                t = time.perf_counter()
                matcher.match(text, lang, lists)
                times.append((time.perf_counter() - t) * 1000)
    return {"commands": len(data.get("intents") or {}), "build_s": round(build_s, 3), "memory_mb": round(held / 2**20, 2),
            "p50_ms": round(statistics.median(times), 2), "p95_ms": round(_pct(times, 0.95), 2), "sentences": len(times)}


def _fresh(index: int, rounds: int) -> dict:
    import subprocess  # noqa: PLC0415

    out = subprocess.run([sys.executable, __file__, "--set", str(index), "--rounds", str(rounds)],
                         check=True, capture_output=True, text=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--intents", type=Path, default=Path(__file__).resolve().parent.parent / "config_example" / "intents.yaml")
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--set", type=int, help=argparse.SUPPRESS)  # internal: one language set, JSON on stdout
    args = ap.parse_args()
    if args.set is not None:
        print(json.dumps(bench_builtin(LANG_SETS[args.set], args.rounds)))
        return 0
    tracemalloc.start()
    t0 = time.perf_counter()
    import hassil.recognize  # noqa: F401, PLC0415
    import home_assistant_intents  # noqa: F401, PLC0415
    imports = {"s": round(time.perf_counter() - t0, 3), "mb": round(tracemalloc.get_traced_memory()[0] / 2**20, 1)}
    tracemalloc.stop()
    result = {
        "imports": imports,
        "machine": {"python": platform.python_version(), "arch": platform.machine(), "system": platform.system(),
                    "cpu": platform.processor() or platform.machine()},
        "builtin": [_fresh(i, args.rounds) for i in range(len(LANG_SETS))],
        "own": bench_own(args.intents, args.rounds) if args.intents.is_file() else None,
    }
    print(f"{result['machine']['arch']} · Python {result['machine']['python']}")
    print(f"Imports (hassil + home-assistant-intents, already loaded by HA itself): {imports['s']} s · {imports['mb']} MB")
    print("Built-in commands (home-assistant-intents)")
    for r in result["builtin"]:
        print(f"  {len(r['languages'])} language(s): load {r['load_s']} s · {r['memory_mb']} MB, in use {r['memory_in_use_mb']} MB "
              f"(peak {r['memory_peak_mb']}) · first {r['first_ms']} ms (cold) · hit p50 {r['hit_p50_ms']} / p95 {r['hit_p95_ms']} ms · "
              f"miss over all {len(r['languages'])} p50 {r['miss_p50_ms']} / p95 {r['miss_p95_ms']} ms")
    if result["own"]:
        o = result["own"]
        print(f"Own commands ({o['commands']}): build {o['build_s']} s · {o['memory_mb']} MB · p50 {o['p50_ms']} ms · p95 {o['p95_ms']} ms")
    if args.json:
        args.json.write_text(json.dumps(result, indent=2) + "\n", "utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
