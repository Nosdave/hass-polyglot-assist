# Polyglot Assist

Multi-language Tier-1 intent matcher for Home Assistant Voice.

Deterministic local Hassil matching across multiple languages with LLM
fallback proxy. A single Voice-PE pipeline can serve a multilingual
household — say "Wettervorhersage", "What's the weather" or
"Quel temps fait-il" and the same intent fires deterministically in the
correct language, without LLM mediation.

Works in front of any HA conversation agent (skye-harris, ollama,
anthropic, …) which is used as the LLM fallback for utterances that no
Tier-1 pattern matches.

See the repo README for setup; ARCHITECTURE.md for the full polyglot
stack (STT → Tier-1 → LLM → TTS).
