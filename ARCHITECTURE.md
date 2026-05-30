# Architecture — The full polyglot Voice-PE stack

This document describes the complete multilingual voice pipeline that
Polyglot Assist is one layer of. Each layer is independently configurable
and replaceable.

## The stack at a glance

```
                          🎤 Mic (HA Voice-PE / ESP32-S3)
                                       │
                                       ▼
  ┌──────────────────────────────────────────────────────────────┐
  │ Layer 1 — STT (polyglot)                                      │
  │   NVIDIA Parakeet on DGX Spark                                │
  │   + Whisper-API-compatible wrapper                            │
  │   → text, optional detected_language                          │
  └─────────────────────────────┬────────────────────────────────┘
                                ▼
  ┌──────────────────────────────────────────────────────────────┐
  │ Layer 2 — HA Voice-PE Pipeline                                │
  │   Single pipeline, conversation_engine = polyglot_assist      │
  │   (NOT hass.config.language-bound — we own multilang now)     │
  └─────────────────────────────┬────────────────────────────────┘
                                ▼
  ┌──────────────────────────────────────────────────────────────┐
  │ Layer 3 — POLYGLOT ASSIST (this integration)                  │
  │   Per-language Hassil matcher (DE / EN / FR / …)              │
  │   ┌─ Match → intent.async_handle → intent_script.*            │
  │   │         (lang slot injected for Jinja branching)          │
  │   └─ Miss  → proxy ConversationInput → Layer 4                │
  └─────────────────────────────┬────────────────────────────────┘
                                ▼
  ┌──────────────────────────────────────────────────────────────┐
  │ Layer 4 — LLM Fallback Agent                                  │
  │   skye-harris / ollama / anthropic / openai / custom-conv …   │
  │   Free-form Q&A + tool-calls + MCP-servers + RAG              │
  │   Returns text response (with language tag)                   │
  └─────────────────────────────┬────────────────────────────────┘
                                ▼
  ┌──────────────────────────────────────────────────────────────┐
  │ Layer 5 — TTS (polyglot)                                      │
  │   Wyoming pocket-tts v1.6.0                                   │
  │   ├─ text_norm.py preprocess (numbers→words, units, markdown) │
  │   ├─ Lingua-LID on text → pick voice state (eve_de/en/fr)     │
  │   └─ Kyutai Mimi decoder + FlowLM → 24 kHz PCM                │
  └─────────────────────────────┬────────────────────────────────┘
                                ▼
                       🔊 Speaker (Voice-PE output)
```

## Layer-by-layer

### Layer 1 — STT polyglot (Parakeet)

**Engine**: NVIDIA Parakeet (NeMo) on DGX Spark.
**Wrapper**: a Whisper-API-compatible bridge so HA's `stt.openai_2`
integration can talk to it without modification.

Parakeet's multilingual variants (parakeet-tdt-multilingual, etc.) support
the languages we care about (DE/EN/FR). Compared to Whisper:

| Aspect | Whisper | Parakeet |
|---|---|---|
| Architecture | Encoder-decoder | RNN-T / TDT |
| Latency | Higher | Lower (~2-3× faster for short utterances) |
| WER (multilingual short) | Excellent | Comparable, often better on short |
| Language detection | Native | Variant-specific; some variants need hint |
| Streaming-friendly | Limited | Yes (RNN-T is naturally streaming) |

**Why Parakeet on Spark**: latency benefit + we already have the GPU,
running Whisper on the same GPU is wasteful when Parakeet does the same
job faster.

**Polyglot configuration**:
- If Parakeet variant supports auto-detect: set HA pipeline's
  `language: auto` (or leave the STT request without language) and let
  the model detect.
- If variant is language-tagged: use HA 2025.10's multi-pipeline + wake-word
  approach, **or** set pipeline language to one primary and let Parakeet
  silently fall back to that language's recognizer. The downstream
  Polyglot Assist matcher tries all configured languages regardless of
  the STT-attached language hint, so a misdetection only costs a few
  milliseconds.

### Layer 2 — HA Voice-PE pipeline

A standard HA Voice-PE pipeline, but with `conversation_engine` set to
the Polyglot Assist entity (e.g. `conversation.polyglot_assist`). This is
the only configuration change required on the HA side.

`prefer_local_intents` is irrelevant once Polyglot Assist is the selected
agent — the local-intent filter only fires when a DIFFERENT agent is
selected and HA tries a local-fallback companion path. Polyglot Assist
*is* the local fast path now.

### Layer 3 — Polyglot Assist

See [README.md](README.md) and the source in `custom_components/polyglot_assist/`.

Three components:
- **`matcher.py`** — owns one `hassil.Intents` per language. Linear
  iteration in hint-language-first order. First match wins.
- **`conversation.py`** — overrides `_async_handle_message`. Declares
  `ConversationEntityFeature.CONTROL` unconditionally so HA does not
  apply `_async_local_fallback_intent_filter` to us.
- **`loader.py`** — reads the user's `intents.yaml` and splits it into
  N Hassil `Intents` objects.

The matched language is injected as the `lang` slot in
`intent.async_handle`, so user-side `intent_script:` templates can simply
branch:

```yaml
intent_script:
  WetterJetztUndHeute:
    speech:
      text: |-
        {%- if lang == 'en' -%}…english…
        {%- elif lang == 'fr' -%}…french…
        {%- else -%}…german…
        {%- endif -%}
```

The integration does not implement any matching beyond what's in
`intents.yaml`. If your sentence is not listed, the miss-path proxies to
the LLM unchanged.

### Layer 4 — LLM Fallback agent

Any HA-registered conversation agent. Examples that are known to work:

| Agent | Use-case |
|---|---|
| `skye-harris/local_openai` | Local OpenAI-compatible LLM with built-in Weaviate RAG. The user's current default. |
| `michelle-avery/custom-conversation` | LiteLLM-multi-provider with rich tool-config |
| `homeassistant/components/anthropic` | Claude API (cloud) |
| `homeassistant/components/ollama` | Local Ollama models |
| `homeassistant/components/google_generative_ai_conversation` | Gemini API |

Future features that belong on this layer (not on Polyglot Assist):

- **Web search** → MCP server (HA 2025.2+ has native MCP-client support)
- **RAG** → skye-harris's Weaviate, or LLM-tool via MCP
- **Music search beyond fixed phrases** → MediaSearchAndPlay built-in,
  or LLM-tool

### Layer 5 — TTS polyglot (Wyoming pocket-tts)

Wyoming protocol server using Kyutai's Pocket-TTS (Mimi decoder + FlowLM
transformer) at `dnest/wyoming-pocket-tts-multi:0.5-cuda-streaming-ha`.

**Pipeline**:
1. **text_norm.py** preprocesses the speech text:
   - Markdown strip (bullets, bold, code, headers)
   - Sonderzeichen → ASCII / punctuation
   - Units expansion (kWh → kilowatt hours / Kilowattstunden / kilowattheures)
   - Numbers → words via `num2words` (de/en/fr)
   - Whitespace collapse
2. **Lingua-LID** detects the language of the (now-normalized) text.
3. **Voice state lookup**: `eve_<lang>` voice-state encoded from a
   per-language WAV reference.
4. **FlowLM** generates Mimi codes (8 streams, ~12.5 Hz frame rate).
5. **Mimi decoder** generates 24 kHz PCM.
6. **Wyoming AudioChunk** streaming to the satellite.

**Why this works for polyglot**: Lingua detects "Hallo Welt" as `de` and
"Hello world" as `en` from the text alone — the matched intent's
`{% if lang == 'fr' %}…{% endif %}` produces French speech, Lingua sees
French, pocket-tts switches to `french_24l` + `eve_fr` voice. The
French Mimi-decoder produces native French intonation, not French-text-
in-German-voice.

See `wyoming_pocket_tts/text_norm.py` for the preprocess pipeline and
`wyoming_pocket_tts/handler.py::_stream_frames` for the LID + voice-state
lookup.

## Pipeline-language vs matched-language vs response-language

Three subtly different things:

| Concept | Source | Used for |
|---|---|---|
| **Pipeline language** | HA Voice-PE config (`pipeline.language`) | STT default + hint to Polyglot's matcher |
| **Matched language** | Polyglot's per-language Hassil match outcome | Slot `lang` to intent_script, IntentResponse language |
| **Response language** | What the speech-text actually contains | Detected by pocket-tts Lingua-LID for voice selection |

These don't have to agree. Example:
- Pipeline language = `de` (HA setup)
- User says "Quel temps fait-il" → Parakeet returns FR text (or DE; either works)
- Polyglot tries languages in `[de, en, fr]` order — DE misses, EN misses,
  FR matches → matched_language = `fr`
- intent_script renders "À Uccle…" (French text)
- pocket-tts receives the French text, Lingua sees `fr`, picks french_24l
  voice → native French speech

This is robust against STT language misdetection: even if Parakeet labels
"Quel temps fait-il" as `de`, Polyglot still tries FR patterns on miss
and matches there.

## Latency budget

Measured on the user's HA Green (4 GB ARM) + Spark for compute layers:

| Layer | Median | p99 | Notes |
|---|---|---|---|
| Wake-word + STT (Parakeet) | ~400 ms | ~700 ms | Spark + Whisper-wrapper |
| Polyglot match (hit, 3 langs × 20 patterns) | <30 ms | <60 ms | Hassil in executor |
| Polyglot proxy (miss → fallback) | <5 ms overhead | <10 ms | Just async_get_agent + delegate |
| LLM fallback (Qwen3 80B on Spark, first token) | ~600 ms | ~1500 ms | depends on prompt+context |
| TTS pocket-tts (first audio frame) | ~150 ms | ~250 ms | streaming, ~10× realtime decode |

**Total Tier-1 round-trip (mic → first audio)**: ~600 ms median.
**Total LLM-fallback round-trip**: ~1.5-3 s median.

The 5× difference for matched-vs-LLM utterances is what makes Tier-1
worth the bookkeeping.

## Why each layer is replaceable

- **STT** — swap Parakeet for Whisper, faster-whisper, Vosk, anything
  that fits HA's STT interface. Polyglot doesn't care.
- **Tier-1 matcher** — this is Polyglot. The contract is just "be a
  ConversationEntity with CONTROL".
- **LLM** — swap skye for ollama / anthropic / claude / custom by
  changing one config value. Polyglot proxies the unmodified input.
- **TTS** — swap pocket-tts for Piper, ElevenLabs, OpenAI TTS, anything
  Wyoming-compatible. Polyglot returns an `IntentResponse` tagged with
  the matched language; the TTS layer takes it from there.

## When Polyglot Assist is NOT the right tool

- If you only need ONE language, the built-in `intent_script:` with
  `custom_sentences/<lang>/*.yaml` works fine — you'll never hit the
  language-tag limit.
- If you need open-ended queries against a knowledge base, that's RAG —
  configure your LLM agent (skye-harris) with Weaviate or layer a
  vector-DB MCP server, **not** a Hassil matcher.
- If you want HA's two-wake-word multi-pipeline approach (HA 2025.10+),
  that solves a different shape: separate users speaking separate
  languages on the same satellite. Polyglot is for "one user speaks
  multiple languages."

## See also

- `custom_components/polyglot_assist/conversation.py` — the agent
- `custom_components/polyglot_assist/matcher.py` — Hassil wrapper
- `config_example/intents.yaml` — config shape
- HA core source pointers (for the limits we work around):
  - [`assist_pipeline/pipeline.py`](https://github.com/home-assistant/core/blob/dev/homeassistant/components/assist_pipeline/pipeline.py)
  - [`conversation/default_agent.py`](https://github.com/home-assistant/core/blob/dev/homeassistant/components/conversation/default_agent.py)
  - [`conversation/trigger.py`](https://github.com/home-assistant/core/blob/dev/homeassistant/components/conversation/trigger.py)
