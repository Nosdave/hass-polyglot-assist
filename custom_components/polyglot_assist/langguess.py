"""Tiny dependency-free language guess for the fallback hint (de/en/fr).

Only meant to decide "is this utterance clearly English/French/German?" for
short voice commands. Returns None when unsure — callers must then do nothing.
"""
from __future__ import annotations

import re

_WORDS: dict[str, frozenset[str]] = {
    "de": frozenset(
        "der die das den dem des ist sind und oder ich du mir mich dir wie was wer wann wo "
        "warum bitte mach mache schalte schalt aus auf zu im ein eine einen nicht es heute "
        "morgen spät wetter noch mit für von bei gibt jemand zuhause wieviel viel uhr "
        "kannst kann soll sag sage mal spiel spiele lauter leiser hast hat sind wird licht lichter dein mein".split()
    ),
    "en": frozenset(
        "the are was what who how when where why it you me my your please turn can "
        "could would do does did there anyone home time weather tell today tomorrow and "
        "of to it's what's isn't with for from this that will have has any lights "
        "light play stop next i'm you're that's hello yes reply english speaking".split()
    ),
    "fr": frozenset(
        "le la les est et je tu moi toi quel quelle quels qui comment où quand pourquoi "
        "il elle du des un une dans pour avec c'est qu'est-ce aujourd'hui demain heure "
        "fait y a s'il plaît allume éteins mets dis-moi peux-tu temps quoi ce cette mon "
        "ton pas lumière lumières joue".split()
    ),
}
_TOKEN = re.compile(r"[a-zà-ÿß'’-]+")


def guess_language(text: str, languages: list[str] | None = None) -> str | None:
    """Return a language code when one language clearly dominates, else None."""
    tokens = [t.replace("’", "'") for t in _TOKEN.findall(text.lower())]
    if not tokens:
        return None
    scores: dict[str, float] = {}
    for lang, words in _WORDS.items():
        if languages is not None and lang not in languages:
            continue
        score = 0.0
        for tok in tokens:
            if tok in words:
                score += 1
            elif "'" in tok and tok.split("'", 1)[0] + "'" in ("l'", "d'", "j'", "c'", "qu'", "n'", "s'", "m'", "t'"):
                score += 1 if lang == "fr" else 0
        scores[lang] = score
    if re.search(r"[äöüß]", text.lower()) and "de" in scores:
        scores["de"] += 1
    if re.search(r"[éèêàçùœ]", text.lower()) and "fr" in scores:
        scores["fr"] += 1
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    if not ranked:
        return None
    best, top = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    # Short commands with song titles or loanwords ("Spiel I will always love
    # you") must not flip the language: a clean win needs no competing words,
    # otherwise a clear margin.
    if (top >= 2 and second == 0) or (top >= 3 and top >= 2 * second):
        return best
    return None


# The hint is always appended as the LAST paragraph of extra_system_prompt and
# starts with this marker, so a hint carried over from an earlier turn can be
# recognised and dropped again (HA's ChatLog re-uses the previous turn's
# extra_system_prompt when the new turn passes none).
HINT_MARKER = "[polyglot_assist language hint]"

LANGUAGE_HINTS: dict[str, str] = {
    "de": "Die letzte Nachricht des Nutzers ist auf Deutsch. Antworte nur auf Deutsch.",
    "en": (
        "The user's latest message is in English. Reply in English only, even when "
        "tool results, device names or earlier turns are in another language."
    ),
    "fr": (
        "Le dernier message de l'utilisateur est en français. Réponds uniquement en "
        "français, même si les résultats des outils, les noms d'appareils ou les "
        "messages précédents sont dans une autre langue."
    ),
}
NEUTRAL_HINT = "Reply in the language of the user's latest message."


def strip_hint(prompt: str | None) -> str | None:
    """Remove a previously appended language hint from ``prompt``."""
    if not prompt or HINT_MARKER not in prompt:
        return prompt
    base = prompt.split(HINT_MARKER, 1)[0].rstrip()
    return base or None


def build_extra_prompt(
    text: str,
    pipeline_language: str | None,
    languages: list[str],
    own_prompt: str | None,
    carried_prompt: str | None,
) -> str | None:
    """Return the extra_system_prompt to send to the fallback, or None to leave
    the input untouched.

    ``own_prompt`` is the turn's own extra_system_prompt, ``carried_prompt`` the
    one HA would otherwise re-use from the previous turn.
    """
    base_lang = (pipeline_language or "").split("-")[0].lower()
    lang = guess_language(text, languages)
    hint = LANGUAGE_HINTS.get(lang) if lang and lang != base_lang else None
    base = own_prompt if own_prompt else strip_hint(carried_prompt)
    stale = not own_prompt and carried_prompt is not None and HINT_MARKER in carried_prompt
    if hint is None:
        if not stale:
            return None
        # A hint from an earlier turn must not stick: HA treats "" like None,
        # so send the base prompt or a neutral instruction instead.
        hint = NEUTRAL_HINT
    return "\n\n".join(p for p in (base, f"{HINT_MARKER}\n{hint}") if p)
