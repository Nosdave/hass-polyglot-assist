// Every tab renders without raw keys, undefined values or injected markup (hostile names everywhere).
import { setup } from "./common.mjs";

const lang = process.argv[2] || "de";
const { rawKey, tick } = await setup(lang);
const X = '"><img src=x onerror=alert(1)>';
const hass = { language: lang, locale: { language: lang }, areas: {}, states: { "ai_task.brain": { attributes: { friendly_name: "Brain" + X } }, "assist_satellite.k": { attributes: { friendly_name: "Küche" + X } } },
  callWS: async (m) => {
    const ty = m.type.replace("polyglot_assist/", "");
    const r = {
      entries: { entries: [{ entry_id: "E", title: "P" + X }] },
      overview: { entry_id: "E", title: "P", rev: "r1", source: "catalog", languages: ["de", "en", "fr"], builtin_languages: ["de"],
        summary: { intents: 1, active: 1, sentences: { de: 1 }, categories: {} }, problems: [], categories: ["Licht" + X],
        intents: { Licht: { description: "d" + X, category: "Licht" + X, safety: "normal", sentences: { de: 1 }, rooms: ["Küche" + X], devices: [], hits: 1 } },
        options: { response_mode: "chime", monitor: true, translation_ai_task: "ai_task.brain", chime_tone: {} }, fallback_agent: "conversation.fast", monitor: true },
      get: { rev: "r1", data: { intents: { Licht: { sentences: { de: ["licht an" + X] } } } } },
      "proposals/list": { items: [{ id: "p", op: "add_names", payload: { target: "light.a", lang: "en", names: ["x" + X] }, title: "t" + X, origin: { source: "starter" }, check: { status: "ok", notes: [] } }] },
      "monitor/status": { enabled: true, summary: { when: "2026-01-01T03:00:00Z", total: 1, hits: 1, assistant: 0, proposals: 0, repeated: [], corrections: [], insights: [{ kind: "wrong_device", went_wrong: "w" + X, wanted: "v" + X, said: ["s" + X], outcome: { result: "no_devices" } }] }, today: {}, log: [{ ts: "2026-01-01T01:00:00Z", text: "t" + X, result: "hit", ms: 5 }] },
      pipelines: { pipelines: [] }, "ui_texts/languages": { languages: [] },
    }[ty];
    if (r) return JSON.parse(JSON.stringify(r));
    throw Object.assign(new Error("Unknown command."), { code: "unknown_command" });
  }, callService: async () => ({}) };
const el = document.createElement("polyglot-assist-panel"); document.body.appendChild(el); el.hass = hass;
await tick(); await tick(); await tick();
const root = el.shadowRoot, errors = [];
for (const v of ["start", "proposals", "commands", "test", "names", "monitor", "settings"]) {
  root.querySelector(`[data-act="tab"][data-v="${v}"]`).click(); await tick(); await tick();
  const main = root.querySelector("main");
  if (!main.querySelector(".card")) errors.push("no card in " + v);
  if (main.querySelector("img, script")) errors.push("XSS in " + v);
  const vis = main.textContent + " " + [...main.innerHTML.matchAll(/(?:aria-label|placeholder|title|data-tip)="([^"]*)"/g)].map((m) => m[1]).join(" ");
  const rk = vis.match(rawKey); if (rk) errors.push("raw key in " + v + ": " + rk[0]);
  if (/undefined|\bNaN\b|\[object/.test(vis)) errors.push("undefined in " + v);
}
console.log(`tabs [${lang}]`, errors.length ? "FAIL\n  " + errors.join("\n  ") : "OK");
process.exit(errors.length ? 1 : 0);
