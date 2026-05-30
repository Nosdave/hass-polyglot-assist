# Polyglot Assist

> **Multi-language Tier-1 intent matcher for Home Assistant Voice.**
> Deterministic local Hassil matching across multiple languages, with LLM
> fallback proxy. Solves HA's `hass.config.language`-only sentence-trigger
> limit so a single Voice-PE pipeline can serve a multilingual household.

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## What it does

A custom **Conversation Agent** that sits in front of your LLM agent
(skye-harris, ollama, anthropic, …) on the Voice-PE pipeline. For each
spoken utterance:

1. Try a fast, deterministic **per-language Hassil match** against your
   `intents.yaml` — patterns can be defined separately for **DE, EN, FR**
   (or any languages you add).
2. **On match**: dispatch to your existing `intent_script` (the speech is
   rendered in the matched language because we inject `lang` as a slot).
3. **On miss**: forward the unmodified `ConversationInput` to your
   configured fallback agent. The LLM handles free-form, tool-calls,
   RAG, etc.

The result: "Wettervorhersage", "What's the weather", "Quel temps fait-il"
all match a single intent **deterministically**, run **without LLM
mediation** when matched, and fall through to the LLM only when the intent
is genuinely unknown.

## Why this exists

Home Assistant currently has two architectural limits that block native
multi-language Tier-1 voice intents:

1. **`assist_pipeline/pipeline.py::_async_local_fallback_intent_filter`**
   restricts `prefer_local_intents` to `HassGetState` +
   `MediaSearchAndPlay` when the conversation agent has CONTROL — every
   other `intent_script` is filtered out of the local-first path.
2. **`conversation/default_agent.py::_rebuild_trigger_intents`** loads
   `platform: conversation` triggers into a single Hassil `Intents`
   object tagged with `hass.config.language` — patterns in any other
   language fail to tokenize.

The HA-team's official answer (Voice Chapter 11) is two wake-words per
satellite, one per language. That works for "wife speaks Dutch, husband
speaks German on the same satellite" but **not** for "I speak three
languages on the same wake-word."

Polyglot Assist fixes that by being **the** selected agent (not a
local-fallback companion) and owning its own per-language matcher.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full stack: STT polyglot
(Parakeet on a GPU host) → polyglot_assist → LLM fallback → TTS polyglot
(Wyoming pocket-tts with Lingua-LID).

## Companion project

Polyglot Assist pairs naturally with [**Polyglot TTS**](https://github.com/Nosdave/polyglot-tts) —
a multi-language streaming TTS server with voice cloning, exposing both
Wyoming (for HA) and OpenAI-Speech-compatible HTTP (for OpenClaw and
other consumers). Same multi-language philosophy, opposite end of the
voice pipeline.

## Installation

### Via HACS (custom repository)

1. HACS → ⋮ → Custom repositories → add this repo URL → category
   "Integration".
2. Install "Polyglot Assist".
3. Restart Home Assistant.
4. Settings → Devices & Services → Add Integration → "Polyglot Assist".
5. Pick the **fallback conversation agent** (your LLM) and the path to
   your **intents.yaml**.
6. Set this entity as the conversation engine on your Voice-PE pipeline.

### Manual

Copy `custom_components/polyglot_assist/` into `<config>/custom_components/`,
restart HA, then proceed from step 4 above.

## Configuration

Create `<config>/polyglot_assist/intents.yaml` modelled after
[`config_example/intents.yaml`](config_example/intents.yaml). Each intent
name must already be registered as an `intent_script` entry — Polyglot
Assist only adds the language-aware matching layer; speech rendering stays
in your existing `intent_script:` templates with `{{ lang }}` branching.

## Services

| Service | What it does |
|---|---|
| `polyglot_assist.reload` | Rebuilds the matcher from `intents.yaml` without restart. Safe — keeps old matcher if reload fails. |
| `polyglot_assist.test_match` | Returns which intent (if any) would match a given text, without dispatching. Useful for sentence tuning. Response service. |

## Events

| Event | When | Payload |
|---|---|---|
| `polyglot_assist_match` | Successful Tier-1 match | `intent`, `lang`, `input`, `took_ms`, `matched_sentence` |
| `polyglot_assist_miss_proxied` | Miss → forwarded to fallback | `input`, `language`, `fallback_agent`, `match_took_ms` |

Use these to track your Tier-1 hit-rate in the HA logbook.

## What this integration does **not** do

- ❌ Run an LLM (use a separate agent for that — any HA conversation agent
  works as fallback)
- ❌ Manage tools / function-calling (the LLM agent or MCP servers do)
- ❌ RAG / vector storage (skye-harris has Weaviate built-in, or use MCP)
- ❌ STT or TTS (pipeline stages — see ARCHITECTURE.md for the stack)
- ❌ Web search, music catalog, anything beyond *match sentence → dispatch
  intent → render response*

If you need any of those, layer them with this — they live in the LLM
agent or behind MCP, not inside Polyglot Assist.

## License

MIT. See [LICENSE](LICENSE).
