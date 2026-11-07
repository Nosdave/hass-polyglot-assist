# Polyglot Assist

A local, multi-language voice layer for Home Assistant Assist.

One voice pipeline serves a multilingual household. "Licht Küche an",
"Turn on the kitchen light" and "Allume la cuisine" all run the same command
deterministically, in the language spoken, in a few milliseconds and without
an LLM. Only what no command matches goes on to your AI agent (any HA
conversation agent).

- **Panel:** commands with hit counters, a test field, the editor, names per
  language, version history with restore, and settings. Available in English,
  German and French; more languages can be added as files or translated by
  your AI.
- **HA's built-in commands in every language**, with target checks.
- **Safe suggestions:** translations and new sentences are only proposed, each
  validated and run against the regression corpus. They are applied only when
  you click. Doors, locks and alarms are protected.
- **Learning by voice (optional):** *"What have you learned?" → "Accept the
  first one" → "Yes"*. Limited to harmless additions on approved satellites,
  and fully undoable.
- **Confirmation tone like HA's own, with any TTS:** classic, designed
  (single/double/triple, rising/falling, flowing, bell …) or your own uploaded
  or recorded tone.
- **Light:** ~16 MB for 3 languages, about 2 ms per hit. Runs on typical HA
  hardware.

See the README for setup and ARCHITECTURE.md for the full stack
(STT → Polyglot → LLM → TTS).
