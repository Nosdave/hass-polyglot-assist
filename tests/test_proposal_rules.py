"""<rule> references in proposals: the user's own rules pass, unknown ones (and markup) do not."""
import golden_lib

P = golden_lib._load("proposals")
corpus = golden_lib._load("corpus")
matcher_mod = golden_lib._load("matcher")

CONFIG = {
    "expansion_rules": {"bitte": "[bitte]"},
    "intents": {"LichtAn": {"sentences": {"de": ["<bitte> mach das licht an"]}}},
}


def _check(sentence: str, rules):
    matcher = matcher_mod.MultilangMatcher(corpus.build_intents(CONFIG))
    sl = matcher.static_slot_lists()
    return P.check_proposal("LichtAn", sentence, ["<bitte> mach das licht an"],
                            lambda t: matcher.match_all(t, "de", sl), sl, rules, "de")


def test_known_rule_is_accepted():
    r = _check("<bitte> schalte das licht ein", corpus.string_rules(CONFIG))
    assert r["status"] != "error", r


def test_unknown_rule_and_markup_are_errors():
    rules = corpus.string_rules(CONFIG)
    for s in ("<foo> schalte das licht ein", "<b>licht</b> an", "<script>alert(1)</script>"):
        assert _check(s, rules)["status"] == "error", s
