# Polyglot Assist

> **A local, multi-language voice layer for Home Assistant Assist.**
> Deterministic local matching across multiple languages, with your AI agent
> as fallback. A single voice pipeline can serve a multilingual household.
> It solves HA's `hass.config.language`-only sentence-trigger limit and
> includes a panel, safe suggestions, learning by voice and a designable
> confirmation tone that works with any TTS.

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

### Shared slot lists

An optional top-level `lists:` section in `intents.yaml` is passed to
hassil for every language, so sentences can use `{slot}` placeholders:

```yaml
lists:
  room:
    values:
      - {in: kitchen, out: light.kitchen}
      - {in: küche, out: light.kitchen}
  percent:
    range: {from: 1, to: 100}
  wish:
    wildcard: true
intents:
  LightSet:
    sentences:
      en: ["set [the] {room} to {percent} percent"]
      de: ["{room} auf {percent} prozent"]
```

Matched slot values reach the `intent_script` as template variables.

### Runtime lists (no manual upkeep)

Two lists are always available at runtime, like in Home Assistant's own
sentences (unless `intents.yaml` defines a list with the same name). Only lists
your sentences use are built:

| List | Contains | Value |
|---|---|---|
| `{area}` | every area name and alias | area name (also for an alias; the default agent returns the alias) |
| `{name}` | every entity exposed to Assist, with the names Assist uses (aliases) | entity name |

Declarative lists are resolved from your installation and refreshed
automatically in the background (registry, area, device and expose changes,
added/removed entities; templates every 60 s, with a 3 s render limit). A list
that cannot be built is empty instead of breaking the language:

```yaml
lists:
  lamps:
    entities:
      domain: light          # one or a list
      area: [kitchen]        # optional, area ids (entity or device area)
      exposed: true          # default: only entities exposed to Assist
      exclude: [light.test]  # optional
    # in = name + aliases, out = entity_id
  rooms:
    areas: {}                # in = name + aliases, out = area_id
  kids:
    template: "{{ ['Anna', 'Ben'] }}"   # strings or {in, out} mappings
```

Offline, simulate runtime lists with a fixture file:
`polyglot_test.py run intents.yaml tests.yaml --lists fixture.yaml`
(`{area: [{in: kitchen, out: Kitchen}], lamps: [...]}`).

### Hits in the conversation history (opt-in)

After a Polyglot hit the fallback LLM normally sees nothing of it, so a
follow-up like "and the piano too" lacks context. With the option
*Hits in conversation history* a hit is added to the shared conversation
history as a **tool call of a tool the fallback LLM really has**, declared per
intent:

```yaml
intents:
  LightZoneOnOff:
    llm_equivalent:
      tool: HassTurnOn                  # resolved against the fallback agent's tools
      args: {name: "{{ zone_text }}"}   # templates: slot values and <slot>_text
```

The tool name must resolve uniquely to an offered tool (exact, or the single
tool ending in `__HassTurnOn`); otherwise nothing is recorded. Hits are never
recorded as plain assistant text: in tests with a local model that made the
LLM claim success for follow-ups without calling any tool, and bare intent
names that are not offered tools were copied into invalid tool calls.

### Catalog (intents in Home Assistant's storage)

Intents can live in `intents.yaml` (default) or in a catalog in
`.storage/polyglot_assist.catalog.<entry>` — same schema, identical
behaviour. Switch in the options (*Intents source*): the first switch copies
`intents.yaml` 1:1 into the catalog and verifies the round-trip; the YAML file
is kept. `polyglot_assist.export_yaml` writes the catalog to
`polyglot_assist/export/`. If the YAML file and the catalog differ when you
switch, you choose what happens; the replaced side is exported first.
Options-flow edits of the YAML source rewrite the file atomically (comments
are lost; the last 10 versions are kept as `intents.yaml.bak-<time>-polyglot`).
The panel only edits the catalog, never a hand-maintained YAML file.

Per-intent safety level:

```yaml
intents:
  FrontDoorOpen:
    safety: disabled    # normal (default) | sensitive | disabled
```

`disabled` takes the intent out of matching (it stays in the catalog);
`sensitive` only marks it (UI warning, translation proposals are never
applied automatically).

The diagnostic sensor *Intents* shows the number of active intents and, as
attributes, sentences per language, lists, disabled/sensitive intents,
validation problems, the source and hit/miss counters since start.

Admin WebSocket API (backend of the panel): `polyglot_assist/stats`, `/get`,
`/test`, `/save_intent`, `/delete_intent`. Every write (panel and options
flow) is guarded: a change that adds errors (e.g. an unparsable sentence,
which would silently disable its whole language) is always refused; a change
that makes a previously passing case of `polyglot_assist/tests.yaml` fail is
refused unless `force: true`. Writes are serialised and start from a fresh
read; an optional `base_rev` (from `/get`) detects stale editors.

Unknown safety levels count as `disabled` (fail closed). If the active source
cannot be loaded, Home Assistant shows a repair issue — Polyglot never falls
back to the other source on its own.

### Translation proposals and the name table

With an AI Task entity selected in the options (*Translation proposals*;
prefer one without tools), the panel/WebSocket command
`polyglot_assist/translate` proposes sentence templates for other languages.
Every proposal is validated before you see it: parsable by hassil, the same
slots as a source sentence, and sample expansions must not match another
intent (collision) — already covered variants are flagged. **Nothing is saved
automatically**; accepting a proposal is a normal, regression-guarded save.

Spoken names per language live in a `names:` table instead of Home Assistant
aliases (the LLM never sees them); they extend `{name}`, `{area}` and all
`entities:`/`areas:` lists:

```yaml
names:
  light.couch: {en: [sofa], fr: [canapé]}
  area.living_room: {de: [wohnzimmer], fr: [salon]}
```

`polyglot_assist/suggest_names` proposes names the same way. Names apply in
every language (a French name also works in a German sentence).

Privacy: proposals send the command's sentences and device/room names to the
selected AI task — with a cloud provider they leave your home. AI tasks with
tools (an LLM API) are refused, because sentences and device names are
untrusted text for the model.


### Injected slots

Every Tier-1 match gets two extra slots in addition to the matched ones:

| Slot | Content |
|---|---|
| `lang` | Language code of the matched sentence (`de`, `en`, `fr`, …) |
| `utterance` | The full original sentence, e.g. to pass a wildcard request on unchanged |

### Options

| Option | Default | What it does |
|---|---|---|
| Translation proposals | – | AI Task entity for sentence/name proposals. |
| Hits in conversation history | off | See above; the tools are read from the fallback agent's own settings. |
| Fallback language hint | off | On a miss, if the utterance is clearly in another configured language (small de/en/fr word lists), a short "reply in <language>" note is appended to `extra_system_prompt` for the fallback agent. Pipelines run with one fixed language, so LLMs otherwise tend to answer in the language of (German) tool results. Otherwise the input is passed on unchanged. |

## Services

| Service | What it does |
|---|---|
| `polyglot_assist.reload` | Rebuilds the matcher from `intents.yaml` without restart. Safe — keeps old matcher if reload fails. |
| `polyglot_assist.test_match` | Returns which intent (if any) would match a given text, without dispatching. Useful for sentence tuning. Response service. |
| `polyglot_assist.import_yaml` | Copies `intents.yaml` 1:1 into the catalog (verified). Admin only. |
| `polyglot_assist.export_yaml` | Writes the catalog to `polyglot_assist/export/intents-<time>.yaml`. Admin only. |
| `polyglot_assist.run_tests` | Matches every sentence of a corpus file (default `polyglot_assist/tests.yaml`; only `.yaml` files below `<config>/polyglot_assist/`) and returns `total`, `passed` and the `failed` cases (expected vs. got). Optional `language` = pipeline language hint, as at runtime. Nothing is executed. Admin-only response service. |

## Regression testing (no regressions when editing sentences)

A corpus file lists sentences with the expected outcome:

```yaml
cases:
  - text: "turn on the couch"
    intent: LightZoneOnOff      # expected intent
    lang: en                    # optional
    slots: {target: light.couch} # optional; every listed slot must be present
    hint: en                    # optional: pipeline language (tried first, like at runtime)
  - text: "tell me a joke"
    intent: null                # must NOT match (goes to the fallback agent)
```

Workflow for every edit of `intents.yaml`:

1. Before the edit, snapshot the current behaviour for your real sentences
   (one per line, e.g. exported from your voice logs):
   `python scripts/polyglot_test.py snapshot intents.yaml sentences.txt > tests.yaml`
2. Edit, then `python scripts/polyglot_test.py validate intents.yaml`
   (flags `{lists}`/`<rules>` that are referenced but not defined — hassil only
   raises those while matching, which silently disables matching for that
   language) and `python scripts/polyglot_test.py run intents.yaml tests.yaml`.
3. Every failure is either a regression (fix the sentences) or an intended
   change (update the case). Then reload and run `polyglot_assist.run_tests`
   against the live matcher.

The CLI uses the integration's own matcher code and needs only
`pip install -r requirements_test.txt` (hassil pinned to the version shipped
with Home Assistant). CI runs the same on every pull request with the
synthetic corpus in `tests/corpus_example.yaml`. Keep real household sentences
(names, places) out of the repository — they belong in your local
`tests.yaml`.

## The Polyglot panel

A sidebar entry **Polyglot** (admins only) for everything above, written for
people without developer knowledge — every option has an info button:

- **Start** — what works in which language, open proposals, problems.
- **Proposals** — new, changed and removed commands, sentences and names,
  each with its origin (new devices, starter set, monitoring, translation)
  and check result; select several and accept them in one checked step.
  *Starter set* and *Check new devices* let the AI task propose commands for
  scripts, scenes and buttons Polyglot does not cover yet, names in every
  enabled language and categories for existing commands.
- **Commands** — grouped by category, room or device; protection level,
  category, kind (switching command / question), answer (speech / tone) and
  sentences per language; translate missing languages; categories editable.
- **Try it** — test a sentence (nothing is executed) and run the test
  sentences.
- **Names** — spoken names per language for rooms and devices.
- **Monitoring** — optional local log and a daily evaluation by your AI task.
- **Settings** — languages (the AI task can tell which it handles well),
  answer to commands, AI task for proposals (must have no tools), follow-ups.

Editing in the panel needs the catalog as intents source (the YAML file stays
as backup).

## Built-in commands in every language

Home Assistant pipelines run in one language. Polyglot also understands Home
Assistant's own commands (lights, switches, covers, timers, time, weather,
shopping list, media …) in every enabled language, using Home Assistant's
sentence collection with Polyglot's lists — so the name table works for them
too. Order: Polyglot commands → built-in commands → assistant. The panel's
settings have a switch "Built-in commands in every language" (on by default)
for setups that want only their own commands.

Free-text placeholders are accepted only where they name what is being
created (shopping/todo items, the name of a new timer); elsewhere they would
swallow unrelated questions ("how long does it take to get to work?" is not a
timer question), so those sentences go to the assistant.

Before a built-in command runs, Polyglot resolves its real targets with Home
Assistant's own matching (duplicate names included) and refuses — handing the
sentence to your assistant instead — if any target is protected: locks,
alarms, valves, sirens, cameras, scripts, scenes, buttons, covers that are not
clearly window coverings, anything whose name, alias, room or device mentions
doors, gates, garages, locks, openers, alarms or cars (de/en/fr), and anything
a command marked *security-relevant* or *off* uses. This is deliberately
cautious: even a lamp named "Garage" or anything with "Auto" in its name is
left to the assistant. German compounds are covered ("Gartentor",
"Haustüröffner", "Kellertür", "Rolltor"); a gate whose name contains a window
word and that has no device class cannot be recognised — give such covers the
device class `gate` or `garage`.

Proposals may only bring simple actions: a script is accepted only if every
step is a static service call on plain entity ids (no templates, no area/
device/floor/label targets, no device actions, no conditions) and every entity
it touches — recursively through called scripts and scenes — passes the same
protection check; otherwise it has to be added by hand. Area-only
switching and free-text timer names are not handled this way either.

## Answer as a tone

With *Answer to commands = Tone*, successful switching commands
(`kind: command`) answer with a short tone instead of speech. Polyglot sends
the marker `[[polyglot:chime]]` to TTS. Questions are always spoken.

- **Polyglot TTS** turns the marker into a tone by itself.
- **Any other TTS** (Piper, Home Assistant Cloud, …): Polyglot brings a small
  speech output **Polyglot Assist (tone)**. Choose your real TTS under
  *Speech output behind the tone* (panel → Settings, or the options), then
  select *Polyglot Assist (tone)* as speech output of your voice assistant.
  Every text — streamed answers of your AI included — is passed on unchanged;
  only the marker becomes a short tone.

### Designing the tone (panel → Settings → Confirmation tone)

Only for *Polyglot Assist (tone)*. If Polyglot TTS answers the marker by itself,
its own tone sounds.

- **Classic**: the tone Polyglot shipped so far. It is the default, so nothing
  changes unless you pick something else.
- **Design**: choose these settings, try them with *Listen*, then press *Apply*:
  - single, double or triple;
  - rising, falling or the same pitch;
  - separate notes or one flowing (gliding) tone;
  - low, medium or high pitch;
  - a soft, bright or bell-like sound;
  - volume.
  Polyglot generates the tone itself in pure Python, in a few milliseconds even
  on small hardware, and caches it.
- **Own tone**: upload a WAV, MP3, OGG or FLAC file (≤ 512 KB), or record up to
  3 s in the panel. Recording needs HTTPS or the companion app.
  - The file is converted once with Home Assistant's ffmpeg to a short mono
    WAV (cut to 4 s, normalised). It is stored privately in
    `/config/polyglot_assist/tones/`, not under `/local`, which anyone can open.
  - Without ffmpeg only 16-bit mono WAV is accepted.
- **Play on the device** plays the *applied* tone on a voice satellite, through
  the same path as a real answer and without the pre-announce sound.

A changed tone takes effect immediately: its hash is part of the TTS cache key,
so an old tone is never replayed. Side effect: cached spoken answers made
through *Polyglot Assist (tone)* are produced again once. The options dialog
keeps the tone but does not edit it — that is done in the panel.

Why this detour: Home Assistant plays its own acknowledge sound only when its
built-in agent answered (and every target is in the satellite's area); a
custom conversation agent cannot ask for it. The companion app never gets the
tone (it would show the marker as text) — only voice satellites do.

## Learning by voice (optional, off by default)

Talk about suggestions on approved voice satellites:
“What have you learned?” → “Tell me more about the first one” → “Accept it” →
Polyglot asks back (“Shall I accept … ? Say yes.”) → “Yes”. “Undo that” takes
the last acceptance back. Works in German, English and French.

Because anyone in earshot can say “yes”, voice may only add **new sentences
and names for harmless commands**: commands Polyglot runs itself with a
plain, fully inspectable action on unprotected devices, no free text, no name
that already means something else. Suggestions from monitoring additionally
need to have been said at least twice, on approved satellites, in two
conversations an hour apart. Everything else — new commands, protection
levels, anything security-related — stays in the panel. At most 5 per hour.
Switch it on and choose the satellites in the panel (Settings).

## Events

| Event | When | Payload |
|---|---|---|
| `polyglot_assist_match` | Successful Tier-1 match | `intent`, `lang`, `input`, `took_ms`, `matched_sentence` |
| `polyglot_assist_miss_proxied` | Miss → forwarded to fallback | `input`, `language`, `language_hint`, `fallback_agent`, `match_took_ms` |

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
