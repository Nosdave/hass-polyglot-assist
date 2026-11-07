<img src="https://raw.githubusercontent.com/Nosdave/hass-polyglot-assist/main/docs/images/logo.png" alt="Polyglot Assist" width="320">

**Many languages. One Home Assistant pipeline. One wake word.**

Speak German, English or French to the same voice satellite, without
switching the wake word or the pipeline. The same command runs locally in a
few milliseconds and answers in the language you spoke. Only what no command
matches goes on to your AI agent (any HA conversation agent).

**New: local intents that teach themselves.** Polyglot notices which
sentences keep ending up at your AI agent. Your AI task turns the recurring
ones into validated local commands in every language. You accept them in the
panel, or by voice: *"What have you learned?" → "Accept the first one" →
"Yes"*.

- **HA's built-in commands in every language**, with target checks.
- **Panel:** commands with hit counters, a test field, the editor, names per
  language, suggestions, version history with restore, and all settings.
  Available in English, German and French; more languages can be added as
  files or translated by your AI.
- **Safe by design:** suggestions are validated against a regression corpus
  and never applied silently. Doors, locks and alarms are protected.
- **Confirmation tone like HA's own, with any TTS:** classic, designed or your
  own uploaded or recorded tone.
- **Light:** about 16 MB for 3 languages and about 2 ms per hit. Runs on
  typical HA hardware.

**Companion:** [Polyglot TTS](https://github.com/Nosdave/polyglot-tts) speaks
every answer in its own language with one voice: seven languages, streaming,
Wyoming for HA. Together with a multilingual STT, the whole pipeline becomes
multilingual.

See the README for setup.
