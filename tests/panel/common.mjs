// Headless panel harness (happy-dom). The panel is loaded from the integration itself.
import { Window } from "happy-dom";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

export const PANEL = new URL("../../custom_components/polyglot_assist/frontend/polyglot-panel.js", import.meta.url);

export async function setup(lang) {
  const win = new Window({ url: "http://localhost/polyglot-assist" });
  for (const k of ["document", "customElements", "HTMLElement", "Event", "location", "history", "setTimeout", "clearTimeout", "localStorage", "NodeFilter"]) globalThis[k] = win[k] ?? globalThis[k];
  globalThis.window = win;
  globalThis.fetch = async (url) => {
    const u = new URL(url);
    try { const body = await readFile(fileURLToPath(u.href.split("?")[0]), "utf8"); return { ok: true, status: 200, json: async () => JSON.parse(body) }; }
    catch (e) { return { ok: false, status: 404, json: async () => { throw new Error("404"); } }; }
  };
  globalThis.document.execCommand = () => true;
  await import(PANEL.href + "?t=" + lang);
  const flat = (o, p = "", out = {}) => { for (const [k, v] of Object.entries(o)) { const key = p ? `${p}.${k}` : k; if (typeof v === "string") out[key] = v; else flat(v, key, out); } return out; };
  const i18n = (l) => readFile(new URL(`../../custom_components/polyglot_assist/frontend/i18n/${l}.json`, import.meta.url), "utf8").then((x) => flat(JSON.parse(x)));
  const T = await i18n(lang), EN = await i18n("en");
  const groups = [...new Set(Object.keys(EN).map((k) => k.split(".")[0]))];
  const rawKey = new RegExp(`(?<![\\w.])(${groups.join("|")})\\.[a-z_]+(?![\\w.])`);
  return { win, T, rawKey, tick: (n = 30) => new Promise((r) => setTimeout(r, n)) };
}
