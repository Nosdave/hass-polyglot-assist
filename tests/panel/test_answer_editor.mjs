// Answer editor: take-over, values at the caret, picker, save payload, AI draft, leave warning, proposal edit. XSS checks throughout.
import { setup } from "./common.mjs";

const lang = process.argv[2] || "de";
const { T, rawKey, tick } = await setup(lang);
const calls = [];
const X = "<img src=x onerror=alert(1)>";
let owned = false, saved = null, updated = null;
const options = { translation_ai_task: "ai_task.brain" };
const intents = { EnergieZusammenfassung: { description: "Energie" + X, sentences: { de: ["energiezusammenfassung"], en: ["energy summary"] }, kind: "question" } };
let props = [{ id: "p1", op: "add_intent", title: "Außentemperatur (de)" + X,
  origin: { source: "ai_answer", detail: "4× → sensor.temp", evidence: [{ said: ["Wie warm ist es draußen?" + X], ai_said: "Draußen sind es 12,4 Grad." + X, entity: "sensor.temp" }] },
  check: { status: "ok", notes: ["2 of 2 questions recognised"] },
  payload: { name: "AussentemperaturFrage", intent: { kind: "question", sentences: { de: ["wie warm ist es draußen"] }, answer_blocks: { de: [{ t: "satz", p: ["Draußen sind es ", { e: "sensor.temp", f: "unit", r: 1 }, "."] }] } } } }];
const ov = () => ({ entry_id: "E", title: "P", rev: "r1", source: "catalog", languages: ["de", "en", "fr"], builtin_languages: ["de"],
  summary: { intents: 1, active: 1, sentences: { de: 1, en: 1 }, categories: {} }, problems: [], categories: [],
  intents: { EnergieZusammenfassung: { description: "Energie", category: null, safety: "normal", sentences: { de: 1, en: 1 }, rooms: [], devices: [], hits: 3 } },
  options, fallback_agent: "conversation.fast", monitor: false });
const hass = { language: lang, locale: { language: lang }, areas: {}, states: {},
  callWS: async (m) => {
    calls.push(m);
    const ty = m.type.replace("polyglot_assist/", "");
    if (ty === "entries") return { entries: [{ entry_id: "E", title: "P" }] };
    if (ty === "overview") return ov();
    if (ty === "get") return { rev: "r1", data: { intents } };
    if (ty === "proposals/list") return { items: props };
    if (ty === "proposals/update") { updated = m; props = [{ ...props[0], id: "p2", edited: true, check: { status: "ok", notes: ["edited by hand"] } }]; return { proposal_id: "p2", check: { status: "ok" } }; }
    if (ty === "answer/source") return owned
      ? { name: m.name, kind: "question", answered_by: "polyglot", owned: true, yaml: { speech: "x" }, yaml_changed: false, answers: { de: { template: "T", blocks: [{ t: "satz", p: ["Die PV liefert ", { e: "sensor.pv", f: "unit", r: 0 }, "."] }] } } }
      : { name: m.name, kind: "question", answered_by: "yaml", owned: false, yaml: { speech: "{{ x }}", unknown_variables: ["targets" + X], has_card: false }, answers: {} };
    if (ty === "answer/takeover") { owned = true; return { saved: true, rev: "r2" }; }
    if (ty === "answer/preview") return { text: "Vorschau " + X, chars: 42, template: "{{ 'x' }}" };
    if (ty === "answer/entities") return { entities: [{ entity_id: "sensor.pv", name: "PV-Leistung" + X, area: "Technik", spoken: "0 Watt" }, { entity_id: "sensor.temp", name: "Außen", area: "Terrasse", spoken: "12 Grad" }] };
    if (ty === "answer/save") { saved = m; return { saved: true, corpus_cases: 400, rev: "r3" }; }
    if (ty === "answer/translate") return { blocks: [{ t: "satz", p: ["The PV delivers ", { e: "sensor.pv", f: "unit", r: 0 }, "."] }], draft: true };
    throw Object.assign(new Error("Unknown command."), { code: "unknown_command" });
  },
  callService: async () => ({}) };
const el = document.createElement("polyglot-assist-panel"); document.body.appendChild(el); el.hass = hass;
await tick(); await tick(); await tick();
const root = el.shadowRoot, errors = [];
const aed = () => root.querySelector("#aed-slot polyglot-answer-editor");
const ar = () => aed()?.shadowRoot;
const q = (s) => ar()?.querySelector(s);
const txt = () => ar()?.textContent || "";
const xss = (w) => {
  if (root.querySelector("main img") || ar()?.querySelector("img, script")) errors.push("XSS " + w);
  const v = txt(), rk = v.match(rawKey); if (rk) errors.push("raw key " + w + ": " + rk[0]);
  if (/undefined|\bnull\b|NaN|\[object/.test(v)) errors.push("undefined in " + w);
};
root.querySelector('[data-act="tab"][data-v="commands"]').click(); await tick();
root.querySelector('[data-act="open"][data-name="EnergieZusammenfassung"]').click(); await tick(); await tick(); await tick(500);
if (!aed()) errors.push("no answer editor");
if (!txt().includes(T["aed.yaml_title"])) errors.push("no yaml banner");
if (q('.satz[contenteditable="true"]')) errors.push("editable before takeover");
xss("yaml");
q('[data-a="takeover"]').click(); await tick();
if (!q('[role="alertdialog"]')) errors.push("no takeover dialog");
q('[data-a="dyes"]').click(); await tick(); await tick(); await tick(500);
if (!calls.some((c) => c.type.endsWith("answer/takeover"))) errors.push("takeover not sent");
if (!txt().includes(T["aed.owned_title"])) errors.push("no owned banner");
if (!q('.satz[contenteditable="true"]')) errors.push("not editable after takeover");
if (!q('.val[data-j="1"]')) errors.push("value chip missing");
await tick(500);
if (!txt().includes("Vorschau")) errors.push("preview not shown");
xss("owned");
{ // a value AT THE CARET, in the middle of "Die PV liefert "
  const sz = q('.satz[data-path="0"]'); sz.focus();
  const tn = [...sz.childNodes].find((n) => n.nodeType === 3);
  const sel = ar().getSelection ? ar().getSelection() : document.getSelection();
  const r = document.createRange(); r.setStart(tn, 4); r.collapse(true); sel.removeAllRanges(); sel.addRange(r);
  q('[data-a="insval"]').click(); await tick(); await tick(100);
  q('[data-a="pickent"][data-e="sensor.temp"]').click(); await tick();
  q('[data-a="pickok"]').click(); await tick();
  const parts = aed().st.model.de[0].p;
  if (!(parts[0] === "Die " && parts[1] && parts[1].e === "sensor.temp" && parts[2] === "PV liefert ")) errors.push("caret insert: " + JSON.stringify(parts));
}
q('[data-a="insval"]').click(); await tick(); await tick(100);
if (!q('[role="dialog"]')) errors.push("picker not open");
if (!txt().includes(T["aed.fmt_unit"])) errors.push("format choices missing");
xss("picker");
q('[data-a="pickent"][data-e="sensor.temp"]').click(); await tick();
q('[data-a="pickr"][data-r="1"]').click(); await tick();
q('[data-a="pickok"]').click(); await tick();
if (q('[role="dialog"]')) errors.push("picker still open");
if (!q(".savebar")) errors.push("no save bar after change");
q('[data-a="save"]').click(); await tick(); await tick(500);
const sv = saved && saved.answers && saved.answers.de && saved.answers.de.blocks;
if (!sv || !JSON.stringify(sv).includes('"e":"sensor.temp"') || !JSON.stringify(sv).includes('"r":1')) errors.push("save payload " + JSON.stringify(saved));
const tr = q('[data-a="translate"][data-to="en"]');
if (!tr) errors.push("no translate button"); else { tr.click(); await tick(); await tick(500); }
if (!txt().includes(T["aed.draft_title"])) errors.push("no draft banner after translate");
if (!q('.ltab[aria-pressed="true"][data-l="en"]')) errors.push("did not switch to en");
root.querySelector('[data-act="tab"][data-v="start"]').click(); await tick();
if (!root.textContent.includes(T["aed.leave_warning"].slice(0, 20))) errors.push("no leave warning");
// leave (second click) and edit an AI-answer proposal
root.querySelector('[data-act="tab"][data-v="proposals"]').click(); await tick();
root.querySelector('[data-act="tab"][data-v="proposals"]').click(); await tick(); await tick();
const main = () => root.querySelector("main").textContent;
if (!main().includes(T["proposals.ai_said"])) errors.push("no 'AI said'");
if (!main().includes(T["origins.ai_answer"])) errors.push("no ai_answer origin");
if (!main().includes(T["proposals.reads_only"])) errors.push("no trust line");
if (root.querySelector("main img")) errors.push("XSS proposals");
root.querySelector('[data-act="pedit"]').click(); await tick(); await tick(500);
const pe = root.querySelector("#aedp-slot polyglot-answer-editor");
if (!pe) errors.push("no proposal editor"); else {
  if (!pe.shadowRoot.querySelector('.val[data-j="1"]')) errors.push("proposal blocks not shown");
  if (pe.shadowRoot.querySelector('[data-a="mode"]')) errors.push("template mode offered for a proposal");
  const ta = root.getElementById("psents"); ta.value = "wie warm ist es draußen\nwie kalt ist es draußen";
  ta.dispatchEvent(new window.Event("input", { bubbles: true, composed: true })); await tick();
  const sb = pe.shadowRoot.querySelector('[data-a="save"]');
  if (!sb) errors.push("sentences alone do not enable saving"); else { sb.click(); await tick(); await tick(400); }
  if (!updated || updated.proposal_id !== "p1" || !updated.sentences?.de?.includes("wie kalt ist es draußen") || !updated.answer_blocks?.de) errors.push("update payload " + JSON.stringify(updated));
  if (!root.textContent.includes(T["proposals.edited"].split("{")[0].trim())) errors.push("no edited message");
}
console.log(`answer editor [${lang}]`, errors.length ? "FAIL\n  " + errors.join("\n  ") : "OK");
process.exit(errors.length ? 1 : 0);
