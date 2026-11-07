"""Recorded answers of a real AI model, replayed through the real proposal checks.

Re-record with scripts/record_golden.py when the prompt changes. A changed
status means the validation behaves differently than when the answers were
recorded — check whether that is intended, then re-record.
"""
import json
from pathlib import Path

import pytest
import yaml

import golden_lib

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "tests" / "golden" / "translations.json").read_text("utf-8"))
FULL = yaml.safe_load((ROOT / "config_example" / "intents.yaml").read_text("utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[f"{c['intent']}-{c['lang']}" for c in CASES])
def test_recorded_answers_keep_their_status(case):
    config = golden_lib.without_language(FULL, case["lang"])
    if "description" in case:
        config["intents"][case["intent"]]["description"] = case["description"]
    results = golden_lib.check(config, case["intent"], case["lang"], case["proposals"])
    assert [r["status"] for r in results] == case["expected"]


def test_real_answers_are_mostly_usable():
    statuses = [s for c in CASES if "description" not in c for s in c["expected"]]
    assert statuses and statuses.count("ok") / len(statuses) >= 0.6


def test_injected_instructions_never_pass():
    # whatever the model wrote, nothing with foreign placeholders or markup may be "ok"
    for case in (c for c in CASES if "description" in c):
        config = golden_lib.without_language(FULL, case["lang"])
        for r in golden_lib.check(config, case["intent"], case["lang"], case["proposals"] + [
            "{secret_list}", "<script>alert(1)</script>", "turn off {all_doors}", "is the laundry done {secret_list}",
        ]):
            if any(x in r["sentence"] for x in ("{secret_list}", "{all_doors}", "<")):
                assert r["status"] == "error", r
