// Proposals tab: failed proposals stay selectable for rejection only; check notes in the panel's language.
import { setup } from "./common.mjs";

const lang = process.argv[2] || "de";
const { T, rawKey, tick } = await setup(lang);
const X = "<img src=x onerror=alert(1)>";
const calls = [];
const MISS = "does not match the sentences that were actually said";
const TAKE = "would take over a Home Assistant command (HassGetCurrentTime/de) for “wie spät ist es”";
let props = [
  { id: "ok1", op: "add_sentences", title: "Uhrzeit (de): wie viel uhr ist es", origin: { source: "monitor", detail: "d" }, payload: { sentences: ["wie viel uhr ist es"] }, check: { status: "ok", notes: [] } },
  { id: "bad1", op: "add_sentences", title: "Uhrzeit (de): sag die zeit", origin: { source: "monitor", detail: "d" }, payload: { sentences: ["sag die zeit"] },
    check: { status: "error", notes: [MISS, "some old note" + X], i18n: { [MISS]: { key: "no_said_match" } } } },
  { id: "bad2", op: "add_sentences", title: "Uhrzeit (de): wie spät ist es", origin: { source: "monitor", detail: "d" }, payload: { sentences: ["wie spät ist es"] },
    check: { status: "error", notes: [TAKE], i18n: { [TAKE]: { key: "takes_over_builtin", params: { hit: "HassGetCurrentTime/de", sample: "wie spät ist es" + X } } } } },
];
const hass = { language: lang, locale: { language: lang }, areas: {}, states: {},
  callWS: async (m) => {
    calls.push(m);
    const ty = m.type.replace("polyglot_assist/", "");
    if (ty === "entries") return { entries: [{ entry_id: "E", title: "P" }] };
    if (ty === "overview") return { entry_id: "E", title: "P", rev: "r1", source: "catalog", languages: ["de"], builtin_languages: ["de"],
      summary: { intents: 1, active: 1, sentences: { de: 1 }, categories: {} }, problems: [], categories: [], intents: {},
      options: {}, fallback_agent: "conversation.fast", monitor: false };
    if (ty === "get") return { rev: "r1", data: { intents: {} } };
    if (ty === "proposals/list") return { items: JSON.parse(JSON.stringify(props)) };
    if (ty === "proposals/reject") { props = props.filter((p) => !m.ids.includes(p.id)); return { removed: m.ids.length }; }
    if (ty === "proposals/accept") return { saved: true, applied: m.ids, refused: {}, corpus_cases: 1 };
    throw Object.assign(new Error("Unknown command."), { code: "unknown_command" });
  },
  callService: async () => ({}) };
const el = document.createElement("polyglot-assist-panel"); document.body.appendChild(el); el.hass = hass;
await tick(); await tick(); await tick();
const root = el.shadowRoot, errors = [];
const box = (id) => root.querySelector(`[data-pid="${id}"]`);
const main = () => root.querySelector("main").textContent;
const click = async (act) => { root.querySelector(`[data-act="${act}"]`).click(); await tick(); await tick(); };
root.querySelector('[data-act="tab"][data-v="proposals"]').click(); await tick(); await tick();

if (!box("bad1") || box("bad1").disabled) errors.push("failed proposal not selectable");
if (!main().includes(T["proposals.reject_only"])) errors.push("no 'reject only' hint");
if (!main().includes(T["checks.no_said_match"])) errors.push("check note not translated");
if (lang !== "en" && main().includes(MISS)) errors.push("English check note shown");
if (!main().includes(T["checks.takes_over_builtin"].split("{")[0].trim())) errors.push("note with params not translated");
if (!main().includes("some old note")) errors.push("uncoded note lost (no fallback)");
if (root.querySelector("main img")) errors.push("XSS in notes");
const rk = main().match(rawKey); if (rk) errors.push("raw key: " + rk[0]);

// "select all checked" still picks only status ok
await click("pall");
if (!box("ok1").checked || box("bad1").checked || box("bad2").checked) errors.push("select-checked picked a failed one");

// accept never sends a failed proposal
box("bad1").checked = true;
await click("paccept");
const acc = calls.find((c) => c.type.endsWith("proposals/accept"));
if (!acc || acc.ids.join() !== "ok1") errors.push("accept sent " + JSON.stringify(acc && acc.ids));

// only failed ones selected → accept refuses with a message, reject removes them
calls.length = 0;
box("ok1") && (box("ok1").checked = false);
box("bad1").checked = true; box("bad2").checked = true;
await click("paccept");
if (calls.some((c) => c.type.endsWith("proposals/accept"))) errors.push("accept called with only failed ones");
if (!main().includes(T["messages.failed_reject_only"].replace("{count}", "2"))) errors.push("no reject-only message");
box("bad1").checked = true; box("bad2").checked = true;
await click("preject");
const rej = calls.find((c) => c.type.endsWith("proposals/reject"));
if (!rej || rej.ids.sort().join() !== "bad1,bad2") errors.push("reject sent " + JSON.stringify(rej && rej.ids));
if (box("bad1") || box("bad2")) errors.push("rejected proposals still listed");

console.log(`proposals [${lang}]`, errors.length ? "FAIL\n  " + errors.join("\n  ") : "OK");
process.exit(errors.length ? 1 : 0);
