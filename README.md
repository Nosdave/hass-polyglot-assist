<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/logo-dark.png">
    <img src="docs/images/logo.png" alt="Polyglot Assist" width="440">
  </picture>
</p>

# Polyglot Assist

**Many languages. One Home Assistant pipeline. One wake word.**

Speak any language your speech recognition and TTS understand — and several
of them to the same voice satellite, without switching the wake word or the
pipeline. The same command runs locally in a few milliseconds and answers in
the language you spoke. "Licht Küche an", "Turn on the kitchen light" and
"Enciende la luz de la cocina" are one command, not three setups.

**New: local intents that teach themselves.** Polyglot notices which
sentences keep ending up at your AI agent. Your **AI task** turns the
recurring ones into local commands, in every language, each validated
against a regression corpus. You accept them in the panel, or simply by
voice: *"What have you learned?" → "Accept the first one" → "Yes"*. Over
time more of your household's everyday sentences run locally, fast and
without an LLM.

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## Highlights

- **One pipeline, one wake word, many languages.** A deterministic
  per-language matcher sits in front of any HA conversation agent (OpenAI,
  Anthropic, Ollama, local LLMs, …). That agent remains the fallback for
  everything else.
- **Self-learning local intents (opt-in).** Nightly monitoring plus your AI
  task turn repeated sentences into suggestions. They are always validated
  and never applied silently. Learning by voice is limited to harmless
  additions on the satellites you approve, at most 5 per hour, and can be
  undone.
- **HA's built-in commands in every language.** Turn on/off, timers
  (incl. named timers) and more, with every target checked.
- **A panel in the sidebar:**
  - commands with hit counters, a test field and an editor;
  - names per language;
  - suggestions with their origin and check;
  - version history with restore;
  - all settings, including the fallback agent, the language check
    (STT, AI, Polyglot, TTS) and the interface language (ships with English,
    German and French; any other language as a file or translated by your AI).
- **Safe by design.** Every command has a safety level, and doors, locks and
  alarms are protected. The AI task used for learning must have no tools.
  Every change runs against the corpus before it is saved.
- **Confirmation tone like HA's own, with any TTS.** Choose classic, design
  your own (single/double/triple, rising/falling, flowing, bell …), or upload
  or record a tone.
- **Light.** About 16 MB with 3 languages enabled (≈ 37 MB with 8) and about 2 ms per hit. Runs on
  typical HA hardware.

## Which languages?

Any. Polyglot Assist has no language list of its own — it matches the
sentences you (or your AI) write, in whatever languages you enable. What a
language needs is the rest of the chain:

| Part | Needed for | Required? |
|---|---|---|
| **Speech-to-text** that recognises the language | hearing it | yes |
| **Text-to-speech** that speaks it | answering in it | yes |
| **AI task** (an LLM without tools) that knows it | translations, suggestions, self-learning | recommended |
| **Fallback conversation agent** that knows it | everything no command matches | recommended |
| HA's own sentences for the language | built-in commands (turn on, timers, …) | optional |

The panel's **language check** shows this per language (STT, AI round trip,
Polyglot, TTS) and an STT test speaks a sentence and listens to it again.

A few small built-in word lists ship for German, English and French today:
- Polyglot's own short answers; other languages get English.
- The spoken learning commands ("What have you learned?").
- The extra safety *keywords* for names such as "garage door". Locks, alarms,
  valves, sirens, cameras and garage/door/gate covers are protected by their
  device type in every language.
- The correction words the monitor recognises.

Contributions for more languages are welcome; each one is a small table.

## The multilingual voice stack

Polyglot Assist handles the *understanding*. For the whole household to be
multilingual, the other two ends of the pipeline need to be too:

- **Speech-to-text** that recognises several languages in one pipeline, for
  example a multilingual Whisper or Parakeet model.
- **Text-to-speech:** [**Polyglot TTS**](https://github.com/Nosdave/polyglot-tts)
  is the companion project. One voice speaks seven languages, detects the
  language per sentence and streams the audio. It also turns Polyglot's tone
  marker into the confirmation tone by itself. It offers a Wyoming endpoint
  for HA and an OpenAI-Speech-compatible HTTP endpoint, and you can clone a
  voice from a short WAV.

Any other TTS works as well. For the confirmation tone, put *Polyglot Assist
(tone)* in front of it (see [Answer as a tone](#answer-as-a-tone)).

## How it works

A custom **conversation agent** sits in front of your LLM agent on the voice
pipeline. For each spoken sentence it tries the following in order:

1. **Your commands.** A fast, deterministic per-language Hassil match
   against the catalog, in every language you enable. On a match it
   runs the command and answers in the matched language.
2. **HA's built-in commands**, in every language, with the targets checked
   (optional).
3. **Your AI agent.** Everything else is forwarded unmodified to the
   configured fallback agent: free-form questions, tool calls, RAG and so on.

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

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full stack: multilingual STT
→ Polyglot Assist → LLM fallback → Polyglot TTS.

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

## Answers: edit them in the panel

Each command shows **who answers it**: Polyglot, or a YAML file
(`intent_script`).

- **Take over from YAML.** Polyglot copies the answer and action of an
  `intent_script` 1:1, then answers the command itself. The YAML file stays
  untouched as a fallback and keeps serving the LLM tool. *Back to the YAML
  file* undoes it, and every change is in the version history.
  - Commands whose action is asynchronous stay in YAML.
  - So do commands that use HA's resolved `targets`, and actions that cannot be
    stored as data.
  - The script mode (single, queued …) is kept.
- **The block editor.** An answer is made of sentences: text with values in
  between.
  - Insert a value at the cursor with **+ Insert value** or by typing **@**.
  - Search entities by name, entity or area. Show a value as number with unit,
    number only, state as text, or name. Choose the precision and what to say
    when the value is unavailable.
  - Units are **spoken** in each language ("12 Grad", "8 percent"), and numbers
    use the decimal comma where the language does.
  - **Depending on a value** gives different sentences for >, < or = cases and
    "otherwise".
- **Preview.** The panel shows the answer rendered with the current values,
  plus its length and roughly how long it takes to speak. Nothing is executed.
- **Languages.** ✨ translates an answer into a missing language, or makes it
  shorter. Only the text changes; the values stay. The result is a marked draft
  until you save it.
- **Expert view.** You can edit the Jinja template directly. Templates taken
  over from YAML that branch on `lang` stay one shared template.
- **No Jinja from suggestions.** Templates are always generated by the server
  from blocks. AI suggestions and proposals never carry Jinja. Every entity an
  answer reads must be exposed to Assist and not protected.

## Learning from AI answers

When you ask the same question more than once and the AI answers it by
*reading* a device, the nightly monitoring run proposes a local command. For
example: "How warm is it outside?" → the AI read the outdoor sensor and said
"It is 12.4 degrees."

- **The sentences** are the questions you actually asked.
- **The answer** is the AI's own wording, with the value replaced by a live
  value block.
- **The device** is the one whose value appears in the AI's answers. If more
  than one could match, nothing is proposed.
- **Checks:** the suggestion says "only answers – switches nothing" and is
  checked against the corpus and HA's own commands.
- **Editing:** **Edit** opens the same answer editor, plus the sentences. An
  edited suggestion is checked again.
- **Cost:** no extra AI call is needed.

## Learning from corrections (optional, off by default)

People correct a voice assistant the way they would correct a person, in their
own language. Examples: "That's wrong, the bed light went off too." or "Nothing
changed, I wanted it warmer." Polyglot uses no word lists for this, so it
works in any language.

- **Excerpts:** at night it cuts short excerpts from the monitoring log (up
  to 12). An excerpt is where you spoke again within two minutes of something
  being done. It contains what was said, what was answered and which entities
  were actually switched.
- **One AI call:** your AI task (it must have no tools) says for each excerpt
  whether it was a correction, what went wrong and what was wanted. The
  results appear in the panel under **Monitoring → What went wrong**.
- **New commands, at most three a night:** for a wrong device or a missing
  command, the AI may suggest a new command with a fixed list of devices,
  e.g. "everything off except the reading lamp".
  - It may only use devices from the room in question that are exposed to
    Assist and unprotected, and only simple actions: turn lights, switches
    and fans on, off or toggle them, or play/pause media. Lights may also get
    brightness or colour temperature.
  - The sentences are checked against the regression corpus.
  - Such a suggestion is always marked "from a correction" and shows what it
    would switch. It can only be accepted in the panel, never by voice.
  - The device list is fixed, so new devices in that room are not added
    automatically.
  - A rejected suggestion does not come back under another name.
- **Things a fixed command cannot do** (e.g. "warmer than now") stay an insight
  without a suggestion.

**Privacy:** turn on *Monitoring → Let the AI review corrections* only if you
are fine with your AI task reading these short excerpts. They contain the
spoken sentences, the answers and the names of switched devices. Use a local
model if they should not leave your home.

## Learning by voice (optional, off by default)

Talk about suggestions on approved voice satellites:
“What have you learned?” → “Tell me more about the first one” → “Accept it” →
Polyglot asks back (“Shall I accept … ? Say yes.”) → “Yes”. “Undo that” takes
the last acceptance back. The voice commands ship in German, English and French.

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
