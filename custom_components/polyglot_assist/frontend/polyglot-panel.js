// Polyglot Assist panel — plain web component (no build step).
// Talks to the admin WebSocket API of the integration. All text that comes
// from the configuration, from an AI task or from a translation file is
// escaped before rendering; translated texts are never inserted as HTML.

// ── texts ───────────────────────────────────────────────────────────────────
// UI texts live in i18n/<lang>.json next to this module (HA language codes,
// nested objects, keys addressed as dot paths, placeholders as {name}).
// Lookup order for a key: WS polyglot_assist/ui_texts/get > full language
// file (pt-BR) > base language file (pt) > en.json > the key itself.
const MOD_URL = new URL(import.meta.url);
const STATIC = new URL(".", MOD_URL).href.replace(/\/$/, "");
const VERSION = MOD_URL.searchParams.get("v") || "";
const FILE_LANGS = ["en", "de", "fr"];
const LS_UI = "polyglot.uiLang";
const beLang = (l) => typeof l === "string" && /^[a-z]{2,3}(-[A-Za-z]{2,4})?$/.test(l); // what the backend accepts
const okLang = (l) => typeof l === "string" && /^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$/.test(l);
const baseLang = (l) => l.split("-")[0].toLowerCase();
const langChain = (l) => [...new Set([l, baseLang(l), "en"])];

// nested {a: {b: "x"}} → {"a.b": "x"}; empty and non-string leaves are dropped
const flatten = (obj, prefix = "", out = {}) => {
  if (!obj || typeof obj !== "object" || Array.isArray(obj)) return out;
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (typeof v === "string") { if (v) out[key] = v; }
    else if (v && typeof v === "object") flatten(v, key, out);
  }
  return out;
};

async function fetchTexts(lang) {
  try {
    const r = await fetch(`${STATIC}/i18n/${encodeURIComponent(lang)}.json?v=${encodeURIComponent(VERSION)}`);
    if (!r.ok) return {};
    return flatten(await r.json());
  } catch (e) { return {}; }
}

const TABS = ["start", "proposals", "commands", "test", "names", "monitor", "settings"];
const OP_KIND = {
  add_intent: "new", add_sentences: "new", add_names: "new", set_list_value: "change",
  set_field: "change", remove_sentences: "remove", remove_names: "remove", remove_list_value: "remove",
};
const ORIGINS = ["new_devices", "starter", "monitor", "correction", "translation", "manual"];
const SAFETY = ["normal", "sensitive", "disabled"];
// The backend's "no category" value (corpus.OTHER_CATEGORY). It is stored data,
// so it stays as is; only its label is translated.
const OTHER = "Sonstiges";

const CSS_ESC = (v) => (globalThis.CSS && typeof globalThis.CSS.escape === "function" ? globalThis.CSS.escape(String(v)) : String(v).replace(/["\\\]]/g, "\\$&"));
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const CSS = `
:host{display:block;background:var(--primary-background-color);color:var(--primary-text-color);font-family:var(--paper-font-body1_-_font-family,Roboto,system-ui,sans-serif);font-size:14px;min-height:100%}
*{box-sizing:border-box}
.top{position:sticky;top:0;z-index:5;background:var(--app-header-background-color,var(--primary-color));color:var(--app-header-text-color,#fff)}
.wrap{max-width:1180px;margin:0 auto;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
.appbar{display:flex;align-items:center;gap:12px;padding-block:14px}
.appbar h1{font-size:20px;font-weight:400;margin:0}.appbar .src{margin-left:auto;font-size:12px;opacity:.85}
.appbar select.ui{font-size:12px;padding:4px 6px}
.menu{background:none;border:0;color:inherit;font-size:22px;padding:0 6px 0 0;cursor:pointer}
.tabs{display:flex;gap:4px;overflow-x:auto;scrollbar-width:none}
.tabs button{background:none;border:0;border-bottom:2px solid transparent;color:inherit;opacity:.75;padding:10px 14px;font:inherit;font-weight:500;font-size:13px;letter-spacing:.03em;text-transform:uppercase;white-space:nowrap;cursor:pointer}
.tabs button[aria-current="page"]{opacity:1;border-color:currentColor}
main{padding-block:20px 48px}
.grid{display:grid;gap:16px}.kpis{grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
.two{grid-template-columns:minmax(0,2fr) minmax(0,1fr)}@media(max-width:820px){.two{grid-template-columns:1fr}}
.card{background:var(--card-background-color);border-radius:var(--ha-card-border-radius,12px);border:1px solid var(--divider-color);padding:16px;min-width:0}
.card h2{font-size:16px;font-weight:500;margin:0 0 12px}
.kpi .v{font-size:28px;font-variant-numeric:tabular-nums}.kpi .l{font-size:12px;color:var(--secondary-text-color);text-transform:uppercase;letter-spacing:.05em}.kpi .sub{font-size:12px;color:var(--secondary-text-color);margin-top:4px}
.chip{display:inline-flex;align-items:center;border-radius:999px;padding:2px 8px;font-size:12px;font-weight:500;white-space:nowrap}
.ok{background:rgba(var(--rgb-success-color,67,160,71),.15);color:var(--success-color,#2e7d32)}
.warn{background:rgba(var(--rgb-warning-color,255,166,0),.18);color:var(--warning-color,#b26a00)}
.err{background:rgba(var(--rgb-error-color,219,68,55),.15);color:var(--error-color,#c62828)}
.pri{background:rgba(var(--rgb-primary-color,3,169,244),.14);color:var(--primary-color)}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
input[type=text],input[type=search],input[type=time],select,textarea{font:inherit;color:var(--primary-text-color);background:var(--primary-background-color);border:1px solid var(--divider-color);border-radius:8px;padding:8px 10px}
textarea{width:100%;min-height:90px;font-family:var(--code-font-family,monospace);font-size:12.5px}
button.b{font:inherit;font-weight:500;border-radius:8px;border:1px solid var(--divider-color);background:var(--card-background-color);color:var(--primary-color);padding:8px 14px;cursor:pointer}
button.b.p{background:var(--primary-color);color:var(--text-primary-color,#fff);border-color:var(--primary-color)}
button.b:disabled{opacity:.5;cursor:default}
button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{outline:2px solid var(--primary-color);outline-offset:1px}
.tbl{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--divider-color);vertical-align:middle}
th{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--secondary-text-color);font-weight:500}
th.group{background:var(--primary-background-color);color:var(--primary-text-color);font-size:12px;text-transform:none}
tr.click{cursor:pointer}tr.click:hover td{background:rgba(var(--rgb-primary-color,3,169,244),.06)}
.lang{font-family:var(--code-font-family,monospace);font-size:11px;border-radius:4px;padding:1px 5px}
.name{font-weight:500}.desc,.hint{color:var(--secondary-text-color);font-size:12px}
.mono{font-family:var(--code-font-family,monospace);font-size:12.5px}
.qrow{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:10px;align-items:start;padding:10px 0;border-bottom:1px solid var(--divider-color)}
.ex{font-family:var(--code-font-family,monospace);font-size:12px;background:var(--primary-background-color);border:1px solid var(--divider-color);border-radius:6px;padding:4px 8px;margin-top:4px;overflow-x:auto;white-space:nowrap}
.set{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:6px 16px;align-items:center;padding:12px 0;border-bottom:1px solid var(--divider-color)}.set:last-child{border-bottom:0}.set .t{font-weight:500}
.switch{width:40px;height:22px;border-radius:999px;background:var(--divider-color);position:relative;border:0;padding:0;cursor:pointer}
.switch::after{content:"";position:absolute;top:3px;left:3px;width:16px;height:16px;border-radius:50%;background:var(--card-background-color);transition:left .15s}
.switch[aria-checked="true"]{background:var(--primary-color)}.switch[aria-checked="true"]::after{left:21px}
@media(prefers-reduced-motion:reduce){.switch::after{transition:none}}
.seg{display:inline-flex;border:1px solid var(--divider-color);border-radius:8px;overflow:hidden}
.seg button{border:0;padding:6px 12px;background:var(--card-background-color);color:var(--primary-text-color);font:inherit;cursor:pointer}
.seg button[aria-pressed="true"]{background:var(--primary-color);color:var(--text-primary-color,#fff)}
.info{display:inline-grid;place-items:center;width:20px;height:20px;border-radius:50%;border:1px solid var(--divider-color);background:var(--card-background-color);color:var(--primary-color);font-size:12px;font-weight:700;padding:0;margin-left:6px;vertical-align:middle;cursor:pointer}
.tip{position:relative}
.bubble{position:absolute;z-index:20;top:calc(100% + 8px);left:0;width:min(320px,80vw);background:var(--primary-text-color);color:var(--card-background-color);border-radius:10px;padding:10px 12px;font-size:13px;line-height:1.45;font-weight:400;text-transform:none;letter-spacing:0;box-shadow:0 6px 20px rgba(0,0,0,.25)}
.step{display:grid;grid-template-columns:28px minmax(0,1fr);gap:10px;align-items:start;padding:10px 0;border-bottom:1px solid var(--divider-color)}.step:last-child{border-bottom:0}
.dot{width:28px;height:28px;border-radius:50%;display:grid;place-items:center;font-weight:700;font-size:13px}
.msg{margin-top:8px;font-size:13px}
.amp{display:inline-grid;place-items:center;width:22px;height:22px;border-radius:50%;font-size:12px;font-weight:700}
.spin{display:inline-block;width:12px;height:12px;border:2px solid var(--divider-color);border-top-color:var(--primary-color);border-radius:50%;animation:pgsp .8s linear infinite;vertical-align:-1px;margin-right:6px}
@keyframes pgsp{to{transform:rotate(360deg)}}
@media(prefers-reduced-motion:reduce){.spin{animation:none}}
.dirty{font-size:12px;color:var(--warning-color,#b26a00);margin-left:8px}
.langs label.new{border-style:dashed}.langs .w{color:var(--warning-color,#b26a00)}
button[disabled]{opacity:.55;cursor:not-allowed}
.banner{border-radius:8px;padding:10px 12px;font-size:13px}
dl{display:grid;grid-template-columns:auto minmax(0,1fr);gap:4px 12px;margin:10px 0 0}dt{color:var(--secondary-text-color)}dd{margin:0;font-family:var(--code-font-family,monospace);font-size:12.5px;overflow-wrap:anywhere}
.langs{display:flex;flex-wrap:wrap;gap:8px}.langs label{display:inline-flex;gap:6px;align-items:center;border:1px solid var(--divider-color);border-radius:999px;padding:4px 12px;cursor:pointer}
.loading{padding:40px;text-align:center;color:var(--secondary-text-color)}
`;

// insight kinds (from the AI review of corrections) → label keys
const INSIGHT_KINDS = { wrong_device: "monitor.kind_wrong_device", missing_command: "monitor.kind_missing_command", misheard: "monitor.kind_misheard", wrong_value: "monitor.kind_wrong_value", answer: "monitor.kind_answer", other: "monitor.kind_other" };

// why an insight did (not) become a proposal → label keys
const OUTCOMES = { proposed: "monitor.outcome_proposed", already_known: "monitor.outcome_already_known", insight_only: "monitor.outcome_insight_only", limit: "monitor.outcome_limit", no_time: "monitor.outcome_no_time", no_devices: "monitor.outcome_no_devices", ai_none: "monitor.outcome_ai_none", name_exists: "monitor.outcome_name_exists", wrong_language: "monitor.outcome_wrong_language", placeholders: "monitor.outcome_placeholders", unsafe_action: "monitor.outcome_unsafe_action", unknown_devices: "monitor.outcome_unknown_devices", exists: "monitor.outcome_exists", sentences_rejected: "monitor.outcome_sentences_rejected", invalid: "monitor.outcome_invalid", failed: "monitor.outcome_failed" };

// every label key spelled out (the i18n test checks literal keys)
const TONE_LABELS = {
  source: { "classic": "tone.v_source_classic", "preset": "tone.v_source_preset", "custom": "tone.v_source_custom" },
  count: { "1": "tone.v_count_1", "2": "tone.v_count_2", "3": "tone.v_count_3" },
  contour: { "up": "tone.v_contour_up", "down": "tone.v_contour_down", "same": "tone.v_contour_same" },
  flow: { "separate": "tone.v_flow_separate", "glide": "tone.v_flow_glide" },
  pitch: { "low": "tone.v_pitch_low", "mid": "tone.v_pitch_mid", "high": "tone.v_pitch_high" },
  timbre: { "soft": "tone.v_timbre_soft", "bright": "tone.v_timbre_bright", "bell": "tone.v_timbre_bell" },
};
const TONE_ERRORS = { "tone_type": "tone.err_type", "tone_empty": "tone.err_empty", "tone_too_big": "tone.err_too_big", "tone_format": "tone.err_format", "tone_convert": "tone.err_convert", "tone_silent": "tone.err_silent", "tone_no_satellite": "tone.err_no_satellite", "tone_no_announce": "tone.err_no_announce", "tone_tts_unavailable": "tone.err_tts_unavailable", "no_custom_tone": "tone.err_no_custom_tone" };

class PolyglotAssistPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.s = { view: "start", group: "category", filter: "", query: "", selected: null, proposals: null, monitor: null, data: null, overview: null, msg: {}, propFilter: "", kindFilter: "", lang: null };
    this._tx = {};
    this._machine = new Set();
    this._wsLangs = [];
    this._uiLang = "en";
    this._uiPref = "";
    this._txRun = 0;
  }
  set hass(h) { this._hass = h; if (!this._started) { this._started = true; this._boot(); } }
  get hass() { return this._hass; }
  set narrow(n) { this._narrow = n; }

  ws(type, data = {}) {
    const extra = this.s.entryId ? { entry_id: this.s.entryId } : {};
    return this._hass.callWS({ type: `polyglot_assist/${type}`, ...extra, ...data });
  }

  // ── i18n ──────────────────────────────────────────────────────────────────
  /** Translated plain text (never HTML): callers always pass it through esc(). */
  t(key, vars) {
    const s = this._tx[key] ?? key;
    return vars ? s.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k] ?? "") : m)) : s;
  }

  /** Language the panel shows now: a deliberate choice, else HA's language. */
  _activeUi() { return okLang(this._uiPref) ? this._uiPref : this._haLang(); }

  /** Language the panel would use without a manual choice: HA's language. */
  _haLang() {
    const h = this._hass || {};
    const l = (h.locale && h.locale.language) || h.language || "";
    return okLang(l) ? l : "en";
  }

  async _loadTexts() {
    const run = ++this._txRun;
    const lang = okLang(this._uiPref) ? this._uiPref : this._haLang();
    const chain = langChain(lang); // e.g. pt-BR → pt → en
    const ws = Promise.resolve().then(() => this._hass.callWS({ type: "polyglot_assist/ui_texts/get", lang })).catch(() => null);
    const [files, r] = await Promise.all([Promise.all(chain.map(fetchTexts)), ws]);
    if (run !== this._txRun) return; // a newer choice won
    const wsTexts = flatten(r && r.texts);
    const merged = {};
    [...files].reverse().forEach((f) => Object.assign(merged, f));
    Object.assign(merged, wsTexts);
    this._tx = merged;
    this._machine = new Set(Array.isArray(r && r.machine) ? r.machine : []); // kept for a later "machine translated" hint
    // language for Intl (language names, dates): the most specific one that has texts
    this._uiLang = Object.keys(wsTexts).length ? lang : chain.find((l, i) => Object.keys(files[i]).length) || "en";
  }

  async _loadUiLangs() {
    try {
      const r = await this._hass.callWS({ type: "polyglot_assist/ui_texts/languages" });
      this._wsLangs = (Array.isArray(r && r.languages) ? r.languages : []).filter(okLang);
    } catch (e) { this._wsLangs = []; }
  }

  _cap(n, locale) {
    try { return n.charAt(0).toLocaleUpperCase(locale) + n.slice(1); } catch (e) { return n.charAt(0).toUpperCase() + n.slice(1); }
  }

  /** Name of a language in the panel's language, the code if Intl does not know it. */
  _ln(code) {
    if (this._dnLang !== this._uiLang) {
      try { this._dn = new Intl.DisplayNames([this._uiLang], { type: "language" }); } catch (e) { this._dn = null; }
      this._dnLang = this._uiLang;
    }
    let n = null;
    try { n = this._dn ? this._dn.of(code) : null; } catch (e) { n = null; }
    return n && n !== code ? this._cap(n, this._uiLang) : String(code ?? "");
  }

  /** Name of a language in that language (for the language switcher). */
  _endonym(code) {
    let n = null;
    try { n = new Intl.DisplayNames([code], { type: "language" }).of(code); } catch (e) { n = null; }
    return n && n !== code ? this._cap(n, code) : code;
  }

  _tip(key) { return `<span class="tip"><button class="info" type="button" aria-label="${esc(this.t("app.explanation"))}" data-tip="${esc(this.t("tips." + key))}">i</button></span>`; }
  _link(view, key) { return `<a href="#${view}" data-act="tab" data-v="${view}">${esc(this.t(key))}</a>`; }
  _sec(ms) { return ms != null ? this.t("units.seconds", { n: (ms / 1000).toFixed(1) }) : "–"; }
  _cat(c) { return c === OTHER ? this.t("categories.other") : c; }
  _origin(src) { return ORIGINS.includes(src) ? this.t("origins." + src) : ""; }
  _q(text) { return this.t("messages.quoted", { text }); }
  // what a NEW command would switch — shown before anyone accepts it
  _actionLine(action) {
    const steps = Array.isArray(action) ? action : action ? [action] : [];
    if (!steps.length) return "";
    const nm = (e) => { const st = this._hass?.states?.[e]; return st?.attributes?.friendly_name ? `${st.attributes.friendly_name} (${e})` : e; };
    const parts = steps.map((st) => { const ids = st?.target?.entity_id; const list = Array.isArray(ids) ? ids : ids ? [ids] : [];
      const data = st?.data && Object.keys(st.data).length ? " " + Object.entries(st.data).map(([k, v]) => `${k}=${v}`).join(", ") : "";
      return `${st?.action || "?"}${data} → ${list.length ? list.map(nm).join(", ") : this.t("proposals.no_target")}`; });
    return `<div class="hint"><b>${esc(this.t("proposals.action_label"))}</b> ${esc(parts.join(" · "))}</div>`;
  }
  _date(v) { try { return new Date(v).toLocaleString(this._uiLang); } catch (e) { return new Date(v).toLocaleString(); } }

  async _boot() {
    this.shadowRoot.innerHTML = `<style>${CSS}</style><div class="loading">…</div>`;
    this.shadowRoot.addEventListener("click", (e) => this._click(e));
    this.shadowRoot.addEventListener("change", (e) => this._change(e));
    this.shadowRoot.addEventListener("input", (e) => this._input(e));
    this.shadowRoot.addEventListener("keydown", (e) => { if (e.key === "Escape") this._closeTips(); if (e.key === "Enter" && e.target.id === "tin") this._test(); });
    const hash = location.hash.slice(1);
    if (TABS.includes(hash)) this.s.view = hash;
    try { this._uiPref = localStorage.getItem(LS_UI) || ""; } catch (e) { this._uiPref = ""; }
    if (!okLang(this._uiPref)) this._uiPref = "";
    await Promise.all([this._loadTexts(), this._loadUiLangs()]);
    if (!this.s.overview) this.shadowRoot.innerHTML = `<style>${CSS}</style><div class="loading">${esc(this.t("app.loading"))}</div>`;
    try {
      const r = await this._hass.callWS({ type: "polyglot_assist/entries" });
      this.s.entries = r.entries || [];
      let saved = null;
      try { saved = localStorage.getItem("polyglot-entry"); } catch (e) { saved = null; }
      const pick = this.s.entries.find((x) => x.entry_id === saved) || this.s.entries[0];
      this.s.entryId = pick ? pick.entry_id : null;
    } catch (err) { this.s.entries = []; }
    await this._reload();
  }

  async _reload(parts = ["overview", "data", "proposals"]) {
    try {
      const jobs = [];
      if (parts.includes("overview")) jobs.push(this.ws("overview").then((r) => (this.s.overview = r)));
      if (parts.includes("data")) jobs.push(this.ws("get").then((r) => (this.s.data = r)));
      if (parts.includes("proposals")) jobs.push(this.ws("proposals/list").then((r) => (this.s.proposals = r.items)));
      if (parts.includes("monitor")) jobs.push(this.ws("monitor/status").then((r) => (this.s.monitor = r)));
      await Promise.all(jobs);
      this.s.error = null;
    } catch (err) {
      this.s.error = err.message || String(err);
    }
    this._render();
  }

  _setMsg(key, text, cls = "") { this.s.msg[key] = { text, cls }; this._render(); }
  _msg(key) { const m = this.s.msg[key]; return m ? `<div class="msg ${m.cls}" role="status">${esc(m.text)}</div>` : ""; }

  // ── rendering ─────────────────────────────────────────────────────────────
  _uiSelect() {
    const langs = [...new Set([...FILE_LANGS, ...this._wsLangs])];
    if (this._uiPref && !langs.includes(this._uiPref)) langs.push(this._uiPref);
    const opt = (v, label) => `<option value="${esc(v)}" ${this._uiPref === v ? "selected" : ""}>${esc(label)}</option>`;
    return `<select class="ui" data-act="uilang" aria-label="${esc(this.t("app.ui_language"))}">${opt("", this.t("app.ui_language_auto"))}${langs.map((l) => opt(l, this._endonym(l))).join("")}</select>`;
  }

  /** Selector of the focused control, so a full re-render can put the focus back. */
  _focusKey() {
    const el = this.shadowRoot && this.shadowRoot.activeElement;
    if (!el) return null;
    for (const attr of ["data-uikey", "data-lang-opt", "id", "data-bind"]) {
      const v = el.getAttribute && el.getAttribute(attr);
      if (v) return { sel: `[${attr}="${CSS_ESC(v)}"]`, start: el.selectionStart, end: el.selectionEnd };
    }
    return null;
  }

  _render() {
    const focus = this._focusKey();
    this._renderInner();
    if (focus) {
      const el = this.shadowRoot.querySelector(focus.sel);
      if (el) { el.focus(); try { if (focus.start != null) el.setSelectionRange(focus.start, focus.end); } catch (e) { /* not a text field */ } }
    }
  }

  _renderInner() {
    const o = this.s.overview;
    const nProps = (this.s.proposals || []).length;
    const tabs = TABS.map((k) => `<button data-act="tab" data-v="${k}" ${this.s.view === k ? 'aria-current="page"' : ""}>${esc(this.t("tabs." + k))}${k === "proposals" && nProps ? ` <span class="chip pri">${nProps}</span>` : ""}</button>`).join("");
    const langs = o ? o.languages.map((l) => this._ln(l)).join(" · ") : "";
    let body = `<div class="loading">${esc(this.t("app.loading_short"))}</div>`;
    if (this.s.error) body = `<div class="card"><div class="banner err">${esc(this.t("app.no_answer", { error: this.s.error }))}</div><p class="hint">${esc(this.t("app.no_answer_hint"))}</p></div>`;
    else if (o) body = this[`_v_${this.s.view}`]();
    this.shadowRoot.innerHTML = `<style>${CSS}</style>
      <div class="top"><div class="wrap"><div class="appbar">${this._narrow ? `<button class="menu" data-act="menu" aria-label="${esc(this.t("app.menu"))}">☰</button>` : ""}<h1>${esc(this.t("app.title"))}</h1>${(this.s.entries || []).length > 1 ? `<select data-act="entry" aria-label="${esc(this.t("app.entry"))}" style="margin-left:12px">${this.s.entries.map((x) => `<option value="${esc(x.entry_id)}" ${x.entry_id === this.s.entryId ? "selected" : ""}>${esc(x.title)}</option>`).join("")}</select>` : ""}<span class="src">${esc(langs)}</span>${this._uiSelect()}</div>
      <nav class="tabs" aria-label="${esc(this.t("app.sections"))}">${tabs}</nav></div></div>
      <main class="wrap">${body}</main>`;
  }

  _v_start() {
    const o = this.s.overview, sum = o.summary;
    const total = Object.values(sum.sentences || {}).reduce((a, b) => a + b, 0);
    const props = (this.s.proposals || []).length;
    const hits = Object.values(o.intents).reduce((a, m) => a + (m.hits || 0), 0);
    const today = this.s.monitor && this.s.monitor.today;
    const t = (k, v) => esc(this.t(k, v));
    const builtinHint = o.builtin_languages.length ? t("start.builtin_active", { languages: o.builtin_languages.map((l) => this._ln(l)).join(", ") })
      : (o.options || {}).builtin_commands === false ? `${t("start.builtin_off")} · ${this._link("settings", "start.turn_on")}` : t("start.builtin_unavailable");
    const kpiSub = (k) => t(o.source === "catalog" ? `kpi.catalog_${k}` : `kpi.file_${k}`);
    return `<section class="grid">
      <div class="card"><h2>${t("start.title")}${this._tip("how")}</h2>
        <div class="step"><span class="dot ok">✓</span><div><b>${t("start.languages")}</b><div class="hint">${esc(o.languages.map((l) => this._ln(l)).join(", "))} · ${this._link("settings", "start.change")}</div></div></div>
        <div class="step"><span class="dot ${o.builtin_languages.length ? "ok" : "warn"}">${o.builtin_languages.length ? "✓" : "!"}</span><div><b>${t("start.builtin")}</b>${this._tip("builtin")}<div class="hint">${builtinHint}</div></div></div>
        <div class="step"><span class="dot ${props ? "warn" : "ok"}">${props ? "!" : "✓"}</span><div><b>${props ? t("start.proposals_waiting", { count: props }) : t("start.no_proposals")}</b><div class="hint">${this._link("proposals", "start.view")} · ${t("start.proposals_hint")}</div></div></div>
        <div class="step"><span class="dot pri">${esc(sum.active)}</span><div><b>${t("start.own_commands")}</b><div class="hint">${t("start.own_commands_hint", { active: sum.active, total })} · ${this._link("commands", "start.edit")}</div></div></div>
        ${o.problems.length ? `<div class="step"><span class="dot err">!</span><div><b>${t("start.problems", { count: o.problems.length })}</b><div class="hint mono">${o.problems.slice(0, 3).map(esc).join("<br>")}</div></div></div>` : ""}
      </div>
      <div class="grid kpis">
        <div class="card kpi"><div class="l">${t("kpi.understood")}</div><div class="v">${hits}</div><div class="sub">${t("kpi.understood_sub")}</div></div>
        ${today ? `<div class="card kpi"><div class="l">${t("kpi.to_ai")}</div><div class="v">${esc(today.assistant)}</div><div class="sub">${t("kpi.to_ai_sub", { total: today.total })}</div></div>
        <div class="card kpi"><div class="l">${t("kpi.response_time")}</div><div class="v">${esc(this._sec(today.hit_ms_p50))}</div><div class="sub">${t("kpi.response_time_sub", { time: this._sec(today.assistant_ms_p50) })}</div></div>` : `<div class="card kpi"><div class="l">${t("kpi.monitor")}</div><div class="v">${t("kpi.monitor_off")}</div><div class="sub">${this._link("monitor", "start.turn_on")}</div></div>`}
        <div class="card kpi"><div class="l">${t("kpi.source")}</div><div class="v">${kpiSub("name")}</div><div class="sub">${kpiSub("sub")}</div></div>
      </div></section>`;
  }

  _v_proposals() {
    const t = (k, v) => esc(this.t(k, v));
    const items = (this.s.proposals || []).filter((i) => (!this.s.propFilter || i.origin?.source === this.s.propFilter) && (!this.s.kindFilter || OP_KIND[i.op] === this.s.kindFilter));
    const readonly = this.s.overview.source !== "catalog";
    const rows = items.map((i) => {
      const st = i.check?.status || "ok";
      const stLabel = st === "ok" ? t("proposals.status_ok") : st === "warn" ? t("proposals.status_warn") : t("proposals.status_error");
      const kind = OP_KIND[i.op] || "change";
      const p = i.payload || {};
      const ex = p.sentences ? p.sentences.join(" · ") : p.names ? p.names.join(" · ") : p.intent?.sentences ? Object.values(p.intent.sentences).flat().slice(0, 3).join(" · ") : p.field ? `${p.field} = ${JSON.stringify(p.value)}` : p.in ? `${p.in} → ${p.out ?? "–"}` : "";
      return `<div class="qrow"><input type="checkbox" data-pid="${esc(i.id)}" ${st === "err" || st === "error" ? "disabled" : ""} aria-label="${t("proposals.select")}">
        <div><span class="chip ${kind === "new" ? "ok" : kind === "remove" ? "warn" : "pri"}">${t("kinds." + kind)}</span> <b>${esc(i.title || i.op)}</b> <span class="chip ${st === "ok" ? "ok" : st === "warn" ? "warn" : "err"}">${stLabel}</span>
        <div class="hint"><b>${t("proposals.origin_label")}</b> ${esc(i.origin?.detail || this._origin(i.origin?.source))}</div>
        ${(i.check?.notes || []).length ? `<div class="hint">${esc(i.check.notes.join("; "))}</div>` : ""}
        ${ex ? `<div class="ex">${esc(ex)}</div>` : ""}${this._actionLine(p.intent?.action)}${i.origin?.wanted ? `<div class="hint"><b>${t("proposals.wanted_label")}</b> ${esc(i.origin.wanted)}</div>` : ""}</div><span class="chip pri">${esc(this._origin(i.origin?.source))}</span></div>`;
    }).join("");
    return `<section class="grid"><div class="card">
      <div class="row" style="justify-content:space-between"><h2 style="margin:0">${t("proposals.title")}${this._tip("proposals")}</h2>
        <div class="row"><button class="b" data-act="pall">${t("buttons.select_checked")}</button><button class="b p" data-act="paccept" ${readonly ? "disabled" : ""}>${t("buttons.accept_selected")}</button><button class="b" data-act="preject">${t("buttons.reject")}</button></div></div>
      ${readonly ? `<div class="banner warn" style="margin-top:10px">${t("proposals.readonly")}</div>` : ""}
      <div class="row" style="margin-top:12px">
        <button class="b" data-act="gen" data-what="starter">${t("buttons.starter")}</button>${this._tip("starter")}
        <button class="b" data-act="gen" data-what="new_devices">${t("buttons.new_devices")}</button>${this._tip("newdev")}
        <button class="b" data-act="gen" data-what="starter" data-dry="1" ${this.s.dryBusy ? 'disabled aria-busy="true"' : ""}>${this.s.dryBusy ? `<span class="spin"></span>` : ""}${t("buttons.dry_run")}</button>${this._tip("dry_run")}
        <select data-bind="propFilter" aria-label="${t("proposals.origin")}"><option value="">${t("proposals.all_origins")}</option>${ORIGINS.map((k) => `<option value="${k}" ${this.s.propFilter === k ? "selected" : ""}>${t("origins." + k)}</option>`).join("")}</select>
        <select data-bind="kindFilter" aria-label="${t("proposals.kind")}"><option value="">${t("proposals.all_kinds")}</option>${["new", "change", "remove"].map((k) => `<option value="${k}" ${this.s.kindFilter === k ? "selected" : ""}>${t("kinds." + k)}</option>`).join("")}</select>
      </div>${this._msg("prop")}${this._dryResult()}
      ${rows || `<p class="hint" style="margin-top:16px">${t("proposals.empty", { starter: this.t("buttons.starter"), new_devices: this.t("buttons.new_devices") })}</p>`}
    </div></section>`;
  }

  _groupKey(meta) {
    if (this.s.group === "category") return this._cat(meta.category || OTHER);
    if (this.s.group === "room") return meta.rooms.length === 1 ? meta.rooms[0] : meta.rooms.length ? this.t("groups.several_rooms") : this.t("groups.no_room");
    if (this.s.group === "device") return meta.devices.length === 1 ? meta.devices[0] : meta.devices.length ? this.t("groups.several_devices") : this.t("groups.no_device");
    return "";
  }

  _v_commands() {
    const t = (k, v) => esc(this.t(k, v));
    const o = this.s.overview, q = this.s.query.toLowerCase();
    const langs = Object.keys(o.summary.sentences || {});
    const entries = Object.entries(o.intents).filter(([n, m]) => (!q || n.toLowerCase().includes(q) || (m.description || "").toLowerCase().includes(q)) &&
      (!this.s.filter || (this.s.filter === "sensitive" && m.safety === "sensitive") || (this.s.filter === "off" && m.safety === "disabled") ||
       (this.s.filter === "gap" && Math.min(...langs.map((l) => m.sentences[l] || 0)) < Math.max(...langs.map((l) => m.sentences[l] || 0)) * 0.5)));
    const groups = {};
    entries.forEach(([n, m]) => (groups[this._groupKey(m)] ||= []).push([n, m]));
    const cats = [...new Set([...(o.categories || []), ...Object.values(o.intents).map((m) => m.category).filter(Boolean), OTHER])];
    const row = ([n, m]) => {
      const max = Math.max(1, ...langs.map((l) => m.sentences[l] || 0));
      const cov = langs.map((l) => { const c = m.sentences[l] || 0; return `${esc(l.toUpperCase())} <span class="lang ${c === 0 ? "err" : c < Math.max(2, max * 0.5) ? "warn" : "ok"}">${c}</span>`; }).join(" ");
      return `<tr class="click" data-act="open" data-name="${esc(n)}"><td><div class="name">${esc(n)}</div><div class="desc">${esc(m.description)}</div></td><td>${cov}</td>
        <td><select data-act="safety" data-name="${esc(n)}" aria-label="${t("commands.safety")}">${SAFETY.map((k) => `<option value="${k}" ${m.safety === k ? "selected" : ""}>${t("safety." + k)}</option>`).join("")}</select></td>
        <td style="text-align:right">${m.hits || "–"}</td></tr>`;
    };
    const table = Object.keys(groups).sort().map((g) => (g ? `<tr><th class="group" colspan="4">${esc(g)} · ${groups[g].length}</th></tr>` : "") + groups[g].map(row).join("")).join("");
    return `<section class="grid">
      ${this.s.selected ? this._editor(this.s.selected, cats) : ""}
      <div class="card">
      <div class="row">
        <input type="search" data-bind="query" value="${esc(this.s.query)}" placeholder="${t("commands.search")}" aria-label="${t("commands.search")}">
        <label class="hint" for="grp">${t("commands.group_by")}</label><select id="grp" data-bind="group">${[["category", "commands.group_category"], ["room", "commands.group_room"], ["device", "commands.group_device"], ["", "commands.group_none"]].map(([k, v]) => `<option value="${k}" ${this.s.group === k ? "selected" : ""}>${t(v)}</option>`).join("")}</select>${this._tip("groups")}
        <select data-bind="filter" aria-label="${t("commands.filter")}"><option value="">${t("commands.filter_all")}</option><option value="gap" ${this.s.filter === "gap" ? "selected" : ""}>${t("commands.filter_gap")}</option><option value="sensitive" ${this.s.filter === "sensitive" ? "selected" : ""}>${t("commands.filter_sensitive")}</option><option value="off" ${this.s.filter === "off" ? "selected" : ""}>${t("commands.filter_off")}</option></select>
        <button class="b" data-act="cats">${t("categories.manage")}</button>
        <span class="hint">${t("commands.count", { shown: entries.length, total: Object.keys(o.intents).length })}</span>
      </div>${this._msg("cmd")}
      ${this.s.catsOpen ? this._catsBox(cats) : ""}
      <div class="tbl" style="margin-top:12px"><table><thead><tr><th>${t("commands.col_command")}</th><th>${t("commands.col_sentences")}${this._tip("coverage")}</th><th>${t("commands.safety")}${this._tip("safety")}</th><th style="text-align:right">${t("commands.col_hits")}</th></tr></thead><tbody>${table}</tbody></table></div>
    </div></section>`;
  }

  _catsBox(cats) {
    const t = (k, v) => esc(this.t(k, v));
    const counts = {};
    Object.values(this.s.overview.intents).forEach((m) => { const c = m.category || OTHER; counts[c] = (counts[c] || 0) + 1; });
    this.s.catEdit ||= cats.filter((c) => c !== OTHER).map((c) => ({ from: c, to: c }));
    return `<div class="card" style="margin-top:12px;background:var(--primary-background-color)"><h2>${t("categories.manage")}${this._tip("cats")}</h2>
      <div class="grid" style="gap:6px">${this.s.catEdit.map((c, i) => `<div class="row"><input type="text" data-cat="${i}" value="${esc(c.to)}" aria-label="${t("categories.rename")}" style="flex:1;min-width:0"><span class="hint">${t("categories.count", { count: counts[c.from] || 0 })}</span><button class="b" data-act="catdel" data-i="${i}">${t("buttons.delete")}</button></div>`).join("")}</div>
      <div class="row" style="margin-top:8px"><input type="text" id="newcat" placeholder="${t("categories.new_placeholder")}" aria-label="${t("categories.new")}"><button class="b" data-act="catadd">${t("buttons.add")}</button><button class="b p" data-act="catsave">${t("buttons.save_categories")}</button></div></div>`;
  }

  _editor(name, cats) {
    const t = (k, v) => esc(this.t(k, v));
    const intent = (this.s.data?.data?.intents || {})[name];
    if (!intent) return "";
    const langs = Object.keys(this.s.overview.summary.sentences || {});
    const missing = langs.filter((l) => !(intent.sentences || {})[l]?.length);
    const lang = this.s.lang && langs.includes(this.s.lang) ? this.s.lang : langs[0];
    const sents = ((intent.sentences || {})[lang] || []).join("\n");
    const ro = this.s.overview.source !== "catalog";
    return `<div class="card"><div class="row" style="justify-content:space-between"><h2 style="margin:0">${esc(name)}</h2><button class="b" data-act="close">${t("buttons.close")}</button></div>
      <p class="hint">${esc(intent.description || "")}</p>
      ${intent.safety === "sensitive" ? `<div class="banner warn">${t("editor.sensitive")}</div>` : ""}
      <div class="row" style="margin:10px 0">${langs.map((l) => `<button class="b" data-act="elang" data-l="${esc(l)}" aria-pressed="${l === lang}" style="${l === lang ? "border-color:var(--primary-color)" : ""}">${esc(this._ln(l))} · ${((intent.sentences || {})[l] || []).length}</button>`).join("")}</div>
      <textarea id="esents" aria-label="${t("editor.sentences")}">${esc(sents)}</textarea>
      <p class="hint">${t("tips.syntax", { placeholder: `{${this.t("tips.syntax_placeholder")}}` })}</p>
      <div class="row">
        <label class="hint">${t("editor.category")} <select id="ecat">${cats.map((c) => `<option value="${esc(c)}" ${((intent.category || OTHER) === c) ? "selected" : ""}>${esc(this._cat(c))}</option>`).join("")}</select></label>
        <label class="hint">${t("editor.type")} <select id="ekind"><option value="">–</option><option value="command" ${intent.kind === "command" ? "selected" : ""}>${t("editor.type_command")}</option><option value="question" ${intent.kind === "question" ? "selected" : ""}>${t("editor.type_question")}</option></select></label>${this._tip("kind")}
        <label class="hint">${t("editor.answer")} <select id="eresp"><option value="">${t("editor.answer_default")}</option><option value="text" ${intent.response_mode === "text" ? "selected" : ""}>${t("answer.text")}</option><option value="chime" ${intent.response_mode === "chime" ? "selected" : ""}>${t("answer.chime")}</option></select></label>${this._tip("answer")}
        <label class="hint">${t("commands.safety")} <select id="esafety">${SAFETY.map((k) => `<option value="${k}" ${(intent.safety || "normal") === k ? "selected" : ""}>${t("safety." + k)}</option>`).join("")}</select></label>${this._tip("safety")}
      </div>
      <div class="row" style="margin-top:12px"><button class="b p" data-act="esave" data-name="${esc(name)}" data-lang="${esc(lang)}" ${ro ? "disabled" : ""}>${t("buttons.save")}</button>
        ${missing.length ? `<button class="b" data-act="etrans" data-name="${esc(name)}" data-langs="${esc(missing.join(","))}">${t("buttons.translate_to", { languages: missing.map((l) => this._ln(l)).join(", ") })}</button>` : `<button class="b" data-act="etrans" data-name="${esc(name)}" data-langs="${esc(langs.filter((l) => l !== lang).join(","))}">${t("buttons.more_variants")}</button>`}
        <button class="b" data-act="edel" data-name="${esc(name)}" ${ro ? "disabled" : ""}>${t("buttons.delete")}</button>
        ${ro ? `<span class="hint">${t("editor.readonly")}</span>` : ""}</div>
      ${this.s.confirmDelete === name ? `<div class="banner err" style="margin-top:10px">${t("editor.confirm_delete", { name })} <button class="b" data-act="edelyes" data-name="${esc(name)}">${t("buttons.yes_delete")}</button> <button class="b" data-act="edelno">${t("buttons.cancel")}</button></div>` : ""}
      ${this._msg("edit")}</div>`;
  }

  _v_test() {
    const t = (k, v) => esc(this.t(k, v));
    const r = this.s.testResult;
    const res = r === undefined ? "" : r ? `<span class="chip ok">${t("test.understood")}</span><dl><dt>${t("test.command")}</dt><dd>${esc(r.intent)}</dd><dt>${t("test.language")}</dt><dd>${esc(this._ln(r.lang))}</dd><dt>${t("test.detected")}</dt><dd>${Object.entries(r.slots || {}).filter(([k]) => k !== "lang").map(([k, v]) => esc(k + " = " + (v.text || v.value))).join("<br>") || "–"}</dd><dt>${t("test.sentence")}</dt><dd>${esc(r.sentence)}</dd></dl>`
      : `<span class="chip warn">${t("test.not_recognized")}</span><p class="hint">${t("test.fallback_hint", { agent: this.s.overview.fallback_agent })}</p>`;
    const c = this.s.corpus;
    return `<section class="grid two"><div class="card"><h2>${t("test.title")}${this._tip("test")}</h2>
      <div class="row"><input type="text" id="tin" value="${esc(this.s.testText || "")}" placeholder="${t("test.placeholder")}" style="flex:1;min-width:0" aria-label="${t("test.input")}">
      <select id="tlang" aria-label="${t("test.pipeline_language")}">${this.s.overview.languages.map((l) => `<option value="${esc(l)}">${esc(this._ln(l))}</option>`).join("")}</select>
      <button class="b p" data-act="test">${t("buttons.try")}</button></div><div class="msg">${res}</div></div>
      <div class="card"><h2>${t("corpus.title")}${this._tip("corpus")}</h2>
      ${c ? (c.error ? `<div class="banner err">${esc(c.error)}</div>` : `<div class="banner ${c.failed.length ? "warn" : "ok"}">${t("corpus.result", { passed: c.passed, total: c.total })}</div>${c.failed.slice(0, 8).map((f) => `<div class="hint mono">${t("corpus.failed", { text: f.text, expected: f.expected.intent, got: (f.got || {}).intent })}</div>`).join("")}`) : `<p class="hint">${t("corpus.not_run")}</p>`}
      <button class="b" data-act="corpus" style="margin-top:8px">${t("buttons.check_now")}</button></div></section>`;
  }

  _v_names() {
    const t = (k, v) => esc(this.t(k, v));
    const names = this.s.data?.data?.names || {};
    const langs = this.s.overview.languages;
    const areas = Object.values(this._hass.areas || {});
    const targets = [...areas.map((a) => [`area.${a.area_id}`, a.name, this.t("names.room")]), ...Object.keys(names).filter((x) => !x.startsWith("area.")).map((x) => [x, this._hass.states[x]?.attributes?.friendly_name || x, x.split(".")[0]])];
    const sugg = this.s.nameSugg || {};
    const ro = this.s.overview.source !== "catalog";
    const rows = targets.map(([id, label, kind]) => `<tr><td><span class="name">${esc(label)}</span><div class="desc">${esc(kind)}</div></td>${langs.map((l) => {
      const cur = ((names[id] || {})[l] || []).join(", ");
      const s = (sugg[id] || {})[l];
      const sHtml = Array.isArray(s) ? s.filter((x) => x.status === "ok").map((x) => `<button class="b" data-act="nadd" data-t="${esc(id)}" data-l="${esc(l)}" data-n="${esc(x.name)}" style="padding:2px 8px;margin-top:4px">+ ${esc(x.name)}</button>`).join(" ") : "";
      return `<td><input type="text" data-name-t="${esc(id)}" data-name-l="${esc(l)}" value="${esc(cur)}" aria-label="${esc(label)} ${esc(l)}" ${ro ? "disabled" : ""} style="width:100%">${sHtml}</td>`;
    }).join("")}</tr>`).join("");
    return `<section class="grid"><div class="card"><h2>${t("names.title")}${this._tip("names")}</h2>
      <div class="row"><input type="text" id="newtarget" placeholder="${t("names.add_placeholder")}" aria-label="${t("names.device")}"><button class="b" data-act="ntarget">${t("buttons.add")}</button>
      <button class="b" data-act="nsugg" style="margin-left:auto">${t("buttons.suggest_gaps")}</button></div>${this._msg("names")}
      <p class="hint">${t("names.hint")}</p>
      <div class="tbl"><table><thead><tr><th>${t("names.col_target")}</th>${langs.map((l) => `<th>${esc(this._ln(l))}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div></div></section>`;
  }

  _v_monitor() {
    const t = (k, v) => esc(this.t(k, v));
    const m = this.s.monitor;
    if (!m) { this._reload(["monitor"]); return `<div class="loading">${t("app.loading_short")}</div>`; }
    const opts = this.s.overview.options || {};
    const sw = `<div class="set" style="padding-top:0"><div><div class="t">${t("monitor.title")}${this._tip("monitor")}</div><div class="hint">${t("monitor.daily_at")} <input type="time" id="mtime" value="${esc(opts.monitor_time || "03:00")}" aria-label="${t("monitor.time")}"></div></div>
      <button class="switch" role="switch" data-act="opt" data-k="monitor" aria-checked="${m.enabled ? "true" : "false"}" aria-label="${t("monitor.switch")}"></button></div>`;
    if (!m.enabled) return `<section class="grid"><div class="card">${sw}${this._msg("mon")}</div></section>`;
    const rv = `<div class="set"><div><div class="t">${t("monitor.review")}${this._tip("monreview")}</div><div class="hint">${opts.translation_ai_task ? t("monitor.review_hint") : t("monitor.review_no_ai")}</div></div>
      <button class="switch" role="switch" data-act="opt" data-k="monitor_review" aria-checked="${opts.monitor_review ? "true" : "false"}" aria-label="${t("monitor.review")}" ${opts.translation_ai_task ? "" : "disabled"}></button></div>`;
    const s = m.summary || {};
    const log = (m.log || []).slice(0, 40).map((e) => `<tr><td class="hint">${esc(this._date(e.ts))}</td><td>${esc(e.text)}</td><td><span class="chip ${e.error ? "err" : e.result === "hit" ? "ok" : "pri"}">${e.error ? t("monitor.result_error") : e.result === "hit" ? (e.builtin ? t("monitor.result_builtin") : t("monitor.result_hit")) : t("monitor.result_ai")}</span></td><td class="hint">${e.ms != null ? esc(this._sec(e.ms)) : ""}</td></tr>`).join("");
    const ins = (s.insights || []).map((x) => `<li><b>${esc(x.went_wrong)}</b>${x.wanted ? ` – ${t("monitor.wanted", { wanted: x.wanted })}` : ""} <span class="chip pri">${t(INSIGHT_KINDS[x.kind] || "monitor.kind_other")}</span>${x.outcome ? `<div class="hint">${t(OUTCOMES[x.outcome.result] || "monitor.outcome_other")}${x.outcome.detail ? ": " + esc(x.outcome.detail) : ""}</div>` : ""}${(x.said || []).length ? `<div class="hint">${esc(x.said.map((z) => this._q(z)).join(" → "))}</div>` : ""}</li>`).join("");
    return `<section class="grid two"><div class="card">${sw}${rv}
      <h2 style="margin-top:12px">${t("monitor.last_run")}</h2>
      ${s.when ? `<p class="hint">${t("monitor.summary", { when: this._date(s.when), total: s.total, hits: s.hits, assistant: s.assistant, proposals: s.proposals })}</p>
      <ul class="hint" style="padding-left:18px">${(s.repeated || []).map((r) => `<li>${t("monitor.repeated", { text: r.text, count: r.count })}</li>`).join("")}${(s.insights || []).length ? "" : (s.corrections || []).map((c) => `<li>${t("monitor.correction", { before: c.before, after: c.correction })}</li>`).join("")}</ul>
      ${ins ? `<h3>${t("monitor.insights")}</h3><ul style="padding-left:18px">${ins}</ul>` : ""}${s.error ? `<p class="hint">${t("monitor.run_error", { error: s.error })}</p>` : ""}` : `<p class="hint">${t("monitor.no_run")}</p>`}
      <button class="b" data-act="monrun">${t("buttons.analyse_now")}</button>${this._msg("mon")}</div>
      <div class="card"><h2>${t("monitor.log")}</h2><div class="tbl"><table><tbody>${log || `<tr><td class="hint">${t("monitor.no_log")}</td></tr>`}</tbody></table></div></div></section>`;
  }

  _v_settings() {
    const t = (k, v) => esc(this.t(k, v));
    const o = this.s.overview, opts = o.options || {};
    if (!this.s.setLoaded) { this.s.setLoaded = true; this._loadSettings(); }
    const aiTasks = Object.keys(this._hass.states).filter((e) => e.startsWith("ai_task."));
    const mode = opts.response_mode || "text";
    return `<section class="grid two"><div class="grid" style="gap:16px;align-content:start">
      ${this._card_fallback()}${mode === "chime" ? this._card_tone() : ""}${this._card_pipelines()}${this._card_languages()}${this._card_uitexts()}</div>
      <div class="grid" style="gap:16px;align-content:start"><div class="card"><h2>${t("settings.title")}</h2>
      <div class="set"><div><div class="t">${t("settings.confirmation")}${this._tip("answer")}</div></div>
        <div class="seg" role="group" aria-label="${t("settings.confirmation_label")}"><button data-act="opt" data-k="response_mode" data-v="text" aria-pressed="${mode === "text"}">${t("answer.text")}</button><button data-act="opt" data-k="response_mode" data-v="chime" aria-pressed="${mode === "chime"}">${t("answer.chime")}</button></div></div>
      ${mode === "chime" ? `<div class="set"><div><div class="t">${t("tone.inner")}${this._tip("tone")}</div><div class="hint">${t("tone.hint")}</div></div>
        <select data-act="chimetts" aria-label="${t("tone.inner")}"><option value="">${t("tone.none")}</option>${Object.keys(this._hass.states).filter((e) => e.startsWith("tts.") && !(o.polyglot_tts || []).includes(e)).map((e) => `<option value="${esc(e)}" ${opts.chime_tts === e ? "selected" : ""}>${esc(this._hass.states[e]?.attributes?.friendly_name || e)}</option>`).join("")}</select></div>` : ""}
      <div class="set"><div><div class="t">${t("settings.ai_task")}${this._tip("aitask")}</div></div>
        <select data-act="aitask" aria-label="${t("settings.ai_task_label")}"><option value="">${t("settings.ai_task_none")}</option>${aiTasks.map((e) => `<option value="${esc(e)}" ${opts.translation_ai_task === e ? "selected" : ""}>${esc(this._hass.states[e]?.attributes?.friendly_name || e)}</option>`).join("")}</select></div>
      <div class="set"><div><div class="t">${t("settings.builtin")}${this._tip("builtin")}</div><div class="hint">${t("settings.builtin_hint")}</div></div>
        <button class="switch" role="switch" data-act="opt" data-k="builtin_commands" aria-checked="${opts.builtin_commands === false ? "false" : "true"}" aria-label="${t("settings.builtin_label")}"></button></div>
      <div class="set"><div><div class="t">${t("voicelearn.title")}${this._tip("voicelearn")}</div><div class="hint">${t("voicelearn.hint")}</div></div>
        <button class="switch" role="switch" data-act="opt" data-k="voice_learn" aria-checked="${opts.voice_learn ? "true" : "false"}" aria-label="${t("voicelearn.title")}"></button></div>
      ${opts.voice_learn ? `<div class="set"><div><div class="t">${t("voicelearn.satellites")}</div>
        <div class="langs" style="margin-top:6px">${Object.keys(this._hass.states).filter((e) => e.startsWith("assist_satellite.")).map((e) => `<label><input type="checkbox" data-vsat="${esc(e)}" ${(opts.voice_learn_satellites || []).includes(e) ? "checked" : ""}> ${esc(this._hass.states[e]?.attributes?.friendly_name || e)}</label>`).join("") || `<span class="hint">${t("voicelearn.no_satellites")}</span>`}</div>
        ${(opts.voice_learn_satellites || []).length ? "" : `<div class="hint">${t("voicelearn.choose")}</div>`}</div><span></span></div>` : ""}
      <div class="set"><div><div class="t">${t("settings.followup")}${this._tip("followup")}</div></div>
        <button class="switch" role="switch" data-act="opt" data-k="chat_log_tools" aria-checked="${opts.chat_log_tools ? "true" : "false"}" aria-label="${t("settings.followup_label")}"></button></div>
      ${this._msg("set")}</div>
      <div class="card"><h2>${t("settings.backup")}</h2>
        <p class="hint">${o.source === "catalog" ? t("settings.backup_catalog") : t("settings.backup_file")}</p>
        <div class="grid" style="gap:8px"><button class="b" data-act="export" ${o.source === "catalog" ? "" : "disabled"}>${t("buttons.export")}</button></div>${this._msg("backup")}</div></div></section>`;
  }

  async _loadSettings() {
    const run = this.s.setRun || 0;
    const all = this._setLangs();
    const got = {};
    await Promise.all([
      this.ws("pipelines").then((r) => (got.pipes = r)).catch((err) => (got.pipes = { pipelines: [], error: err.message || err.code })),
      this._hass.callWS({ type: "conversation/agent/list" }).then((r) => (got.agents = r.agents || [])).catch(() => (got.agents = [])),
      this.ws("languages/check", { languages: all, ai: false }).then((r) => (got.langCheck = r)).catch(() => {}),
      this._hass.callWS({ type: "polyglot_assist/ui_texts/languages" }).then((r) => (got.uiShipped = r.shipped || [])).catch(() => (got.uiShipped = [])),
    ]);
    if (run !== (this.s.setRun || 0)) return; // another entry was chosen meanwhile
    Object.assign(this.s, got);
    this._render();
  }

  /** Languages shown in the settings: enabled, with commands, offered by the check. */
  _setLangs() {
    const o = this.s.overview, opts = o.options || {};
    const available = Object.keys(o.summary.sentences || {});
    const enabled = opts.languages_enabled && opts.languages_enabled.length ? opts.languages_enabled : available;
    return [...new Set([...enabled, ...available, ...(this.s.langOffered || [])])].filter(beLang);
  }
  _enabledLangs() {
    const o = this.s.overview, opts = o.options || {};
    return opts.languages_enabled && opts.languages_enabled.length ? opts.languages_enabled : Object.keys(o.summary.sentences || {});
  }

  _dryResult() {
    const d = this.s.dry;
    if (!d) return "";
    const t = (k, v) => esc(this.t(k, v));
    const st = d.by_status || {};
    return `<div class="result" style="margin-top:12px"><b>${t("dry.title")}</b>
      <p class="hint">${t("dry.summary", { total: d.total, ok: st.ok || 0, warn: st.warn || 0, error: st.error || 0, seconds: d.seconds })}</p>
      ${(d.sample || []).length ? `<ul class="hint" style="margin:6px 0 0;padding-left:18px">${d.sample.map((i) => `<li><span class="chip ${i.check && i.check.status === "ok" ? "ok" : i.check && i.check.status === "error" ? "err" : "warn"}">${esc(i.check ? i.check.status : "?")}</span> ${esc(i.title || i.op)}</li>`).join("")}</ul>` : ""}
      ${(d.errors || []).length ? `<p class="hint">${t("dry.notes", { notes: d.errors.slice(0, 3).join("; ") })}</p>` : ""}
      <p class="hint">${t("dry.nothing_changed")}</p></div>`;
  }

  _card_fallback() {
    const t = (k, v) => esc(this.t(k, v));
    const o = this.s.overview, own = new Set(o.polyglot_agents || []);
    const agents = (this.s.agents || []);
    const cur = o.fallback_agent;
    const opts = agents.map((a) => `<option value="${esc(a.id)}" ${a.id === cur ? "selected" : ""} ${own.has(a.id) ? "disabled" : ""}>${esc(a.name || a.id)}${own.has(a.id) ? " – " + t("fallback.not_possible") : ""}</option>`);
    if (cur && !agents.some((a) => a.id === cur)) opts.unshift(`<option value="${esc(cur)}" selected>${esc(cur)}</option>`);
    return `<div class="card"><h2>${t("settings.fallback")}${this._tip("fallback")}</h2>
      <div class="set"><div><div class="t">${t("fallback.label")}</div><div class="hint">${t("fallback.same_setting")}</div></div>
        <select data-act="fallback" aria-label="${t("fallback.label")}" ${this.s.fbBusy ? "disabled" : ""}>${opts.join("") || `<option>${t("app.loading_short")}</option>`}</select></div>${this._msg("fb")}</div>`;
  }

  _card_pipelines() {
    const t = (k, v) => esc(this.t(k, v));
    const p = this.s.pipes;
    const rows = p ? (p.pipelines || []).map((x) => `<tr><td class="name">${esc(x.name)}${x.preferred ? ` <span class="chip pri">${t("pipes.preferred")}</span>` : ""}</td>
      <td><span class="chip ${x.polyglot ? "ok" : "warn"}">${x.polyglot ? "✓" : t("pipes.no")}</span></td>
      <td><span class="chip ${x.polyglot && !x.prefer_local_intents ? "ok" : x.prefer_local_intents ? "warn" : ""}">${x.prefer_local_intents ? t("pipes.local_on", { language: this._ln(x.language) }) : t("pipes.local_off")}</span></td>
      <td class="desc">${esc((x.devices || []).join(", ") || "–")}</td></tr>`).join("") : "";
    return `<div class="card"><h2>${t("pipes.title")}${this._tip("pipelines")}</h2>
      ${p && p.error ? `<div class="banner err">${esc(this.t("messages.error", { error: p.error }))}</div>` : ""}${p ? (p.pipelines || []).length ? `<div class="tbl"><table><thead><tr><th>${t("pipes.assistant")}</th><th>${t("pipes.polyglot")}</th><th>${t("pipes.local")}</th><th>${t("pipes.devices")}</th></tr></thead><tbody>${rows}</tbody></table></div>` : `<p class="hint">${t("pipes.none")}</p>` : `<p class="hint">${t("app.loading_short")}</p>`}
      <div class="row" style="margin-top:12px"><button class="b" data-act="openpipes">${t("pipes.open")}</button><span class="hint">${t("pipes.readonly")}</span></div></div>`;
  }

  _card_languages() {
    const t = (k, v) => esc(this.t(k, v));
    const enabled = this._enabledLangs();
    if (!this.s.langChosen) this.s.langChosen = [...enabled];
    const chosen = new Set(this.s.langChosen);
    const check = this.s.langCheck, rows = (check && check.languages) || [];
    const byLang = Object.fromEntries(rows.map((r) => [r.language, r]));
    const full = (l) => { const r = byLang[l]; return r && r.stt !== false && r.tts !== false && r.ai !== false && (r.commands > 0 || r.builtin); };
    const all = this._setLangs();
    const dirty = !(chosen.size === enabled.length && enabled.every((l) => chosen.has(l)));
    const amp = (v) => v === true ? `<span class="amp ok" role="img" aria-label="${t("langs.yes")}">✓</span>` : v === false ? `<span class="amp err" role="img" aria-label="${t("langs.no")}">✕</span>` : `<span class="amp" role="img" aria-label="${t("langs.unknown")}" title="${t("langs.unknown")}">?</span>`;
    const stt = this.s.sttTest ? Object.fromEntries(this.s.sttTest.languages.map((r) => [r.language, r])) : null;
    return `<div class="card"><h2>${t("settings.languages")}${this._tip("languages")}</h2>
      <div class="langs">${all.map((l) => `<label class="${enabled.includes(l) ? "" : "new"}"><input type="checkbox" data-lang-opt="${esc(l)}" ${chosen.has(l) ? "checked" : ""}> ${esc(this._ln(l))}${!enabled.includes(l) && byLang[l] && !full(l) ? ` <span class="w" title="${t("langs.not_full")}">⚠</span>` : ""}</label>`).join("")}</div>
      <div class="row" style="margin-top:12px">
        <button class="b" data-act="langask" ${this.s.langBusy ? 'disabled aria-busy="true"' : ""}>${this.s.langBusy ? `<span class="spin"></span>` : ""}${t("langs.ask")}</button>
        <button class="b pri" data-act="langapply" ${dirty && !this.s.langApplying ? "" : "disabled"}>${t("langs.apply")}</button>${dirty ? `<span class="dirty">${t("langs.dirty")}</span>` : ""}${this._tip("apply")}</div>
      ${this.s.langSteps ? `<div class="hint" style="margin-top:8px">${this.s.langSteps.map((st, i) => `${st.done ? "✓" : `<span class="spin"></span>`} ${esc(this.t(st.k))}`).join("<br>")} ${this.s.langBusy ? `<button class="b" data-act="langcancel" style="margin-left:8px">${t("langs.stop_waiting")}</button>` : ""}</div>` : ""}
      ${this._msg("lang")}
      <h2 style="margin-top:18px">${t("langs.table_title")}${this._tip("amp")}</h2>
      <div class="tbl"><table><thead><tr><th>${t("langs.language")}</th><th>${t("langs.stt")}</th><th>${t("langs.ai")}</th><th>${t("langs.commands")}</th><th>${t("langs.tts")}</th></tr></thead><tbody>
      ${all.map((l) => { const r = byLang[l] || {}; return `<tr><td>${esc(this._ln(l))}</td><td>${amp(r.stt)}${stt && stt[l] ? ` <span class="hint">${stt[l].ok === true ? "✓" : stt[l].ok === false ? "✕" : "?"} ${esc(stt[l].heard || stt[l].error || "")}</span>` : ""}</td><td>${amp(r.ai)}</td><td>${r.commands > 0 || r.builtin ? amp(true) : amp(r.language ? false : null)}${r.language ? ` <span class="hint">${esc(String(r.commands || 0))}${r.builtin ? " + HA" : ""}</span>` : ""}</td><td>${amp(r.tts)}</td></tr>`; }).join("")}
      </tbody></table></div>
      ${check && check.pipeline ? `<p class="hint">${t("langs.source", { assistant: check.pipeline.name, stt: check.pipeline.stt_engine || "–", tts: check.pipeline.tts_engine || "–", language: this._ln(check.pipeline.language) })}</p>` : ""}
      <div class="row"><button class="b" data-act="stttest" ${this.s.sttBusy ? 'disabled aria-busy="true"' : ""}>${this.s.sttBusy ? `<span class="spin"></span>` : ""}${t("langs.stt_test")}</button>${this.s.sttTest ? `<span class="hint">${t("langs.stt_hint")}${(this.s.sttTest.skipped || []).length ? " " + t("langs.stt_skipped", { languages: this.s.sttTest.skipped.map((l) => this._ln(l)).join(", ") }) : ""}</span>` : ""}</div>${this._msg("stt")}</div>`;
  }

  _card_uitexts() {
    const t = (k, v) => esc(this.t(k, v));
    if (!this.s.uiShipped) return ""; // not loaded yet
    const shipped = this.s.uiShipped;
    const isShipped = (l) => shipped.includes(l) || shipped.includes(baseLang(l));
    const active = okLang(this._uiPref) ? this._uiPref : this._haLang(); // the language the panel shows right now
    const candidates = [...new Set([active, ...this._enabledLangs(), ...this._wsLangs])].filter((l) => beLang(l) && !isShipped(l));
    if (!candidates.length) return "";
    const target = candidates.includes(this.s.uiNew) ? this.s.uiNew : candidates.includes(active) ? active : candidates[0];
    const lang = "";
    const en = this._enTexts || {};
    const own = this.s.uiOwn && this.s.uiOwn.lang === target ? this.s.uiOwn : null;
    const keys = Object.keys(en).filter((k) => !this.s.uiFilter || (k + " " + en[k] + " " + ((own && own.texts[k]) || "")).toLowerCase().includes(this.s.uiFilter.toLowerCase())).slice(0, 60);
    return `<div class="card"><h2>${t("uitexts.title")}${this._tip("uitexts")}</h2>
      <div class="row">${lang ? `<b>${esc(this._endonym(target))}</b>` : `<select data-bind="uiNew" aria-label="${t("uitexts.language")}">${candidates.map((l) => `<option value="${esc(l)}" ${l === target ? "selected" : ""}>${esc(this._ln(l))}</option>`).join("")}</select>`}
        <button class="b" data-act="uitrans" data-l="${esc(target)}" ${this.s.uiBusy ? "disabled" : ""}>${this.s.uiBusy ? `<span class="spin"></span>` : ""}${t("uitexts.translate")}</button>
        <button class="b" data-act="uiexport" data-l="${esc(target)}">${t("uitexts.export")}</button>
        <button class="b" data-act="uiload" data-l="${esc(target)}">${t("uitexts.edit")}</button></div>
      <p class="hint">${own ? (own.machine.length ? t("uitexts.machine", { count: own.machine.length }) : t("uitexts.own", { count: Object.keys(own.texts).length })) : t("uitexts.none")}</p>${this._msg("ui")}
      ${own ? `<input type="search" data-bind="uiFilter" value="${esc(this.s.uiFilter || "")}" placeholder="${t("uitexts.filter")}" aria-label="${t("uitexts.filter")}" style="width:100%;margin:8px 0">
      <div class="tbl"><table><thead><tr><th>English</th><th>${esc(this._endonym(target))}</th></tr></thead><tbody>
      ${keys.map((k) => `<tr><td class="desc">${esc(en[k])}</td><td><input type="text" data-uikey="${esc(k)}" data-l="${esc(target)}" value="${esc((this.s.uiDraft || {})[target + "|" + k] ?? own.texts[k] ?? "")}" style="width:100%" aria-label="${esc(en[k])}">${own.machine.includes(k) ? ` <span class="chip warn">${t("uitexts.machine_chip")}</span>` : ""}</td></tr>`).join("")}
      </tbody></table></div>` : ""}</div>`;
  }

  // ── confirmation tone designer ────────────────────────────────────────────
  _toneDraft() {
    const saved = ((this.s.overview || {}).options || {}).chime_tone || {};
    const savedKey = this.s.entryId + "|" + JSON.stringify(saved);
    if (this.s.toneDraftKey !== savedKey) { this.s.toneDraft = null; this.s.toneDraftKey = savedKey; } // another entry or changed elsewhere
    if (!this.s.toneDraft) this.s.toneDraft = { source: "classic", count: 2, contour: "up", flow: "separate", pitch: "mid", timbre: "soft", volume: 0.6, ...saved };
    return this.s.toneDraft;
  }

  _card_tone() {
    const t = (k, v) => esc(this.t(k, v));
    const d = this._toneDraft();
    const saved = ((this.s.overview || {}).options || {}).chime_tone || {};
    const seg = (k, vals, label) => `<div class="seg" role="group" aria-label="${t(label)}">${vals.map((v) => `<button data-act="tonepick" data-k="${k}" data-v="${v}" aria-pressed="${String(d[k]) === String(v)}">${t(TONE_LABELS[k][String(v)])}</button>`).join("")}</div>`;
    const row = (label, body) => `<div class="set"><div><div class="t">${t(label)}</div></div>${body}</div>`;
    const sats = Object.keys(this._hass.states).filter((e) => e.startsWith("assist_satellite."));
    const busy = this.s.toneBusy;
    const changed = JSON.stringify({ ...d, custom_hash: undefined }) !== JSON.stringify({ source: "classic", count: 2, contour: "up", flow: "separate", pitch: "mid", timbre: "soft", volume: 0.6, ...saved, custom_hash: undefined });
    return `<div class="card"><h2>${t("tone.title")}${this._tip("tonedesign")}</h2>
      ${row("tone.source", seg("source", ["classic", "preset", "custom"], "tone.source"))}
      ${d.source === "preset" ? `${row("tone.count", seg("count", [1, 2, 3], "tone.count"))}
        ${d.count > 1 ? row("tone.contour", seg("contour", ["up", "down", "same"], "tone.contour")) : ""}
        ${row("tone.flow", seg("flow", ["separate", "glide"], "tone.flow"))}
        ${row("tone.pitch", seg("pitch", ["low", "mid", "high"], "tone.pitch"))}
        ${row("tone.timbre", seg("timbre", ["soft", "bright", "bell"], "tone.timbre"))}
        ${row("tone.volume", `<input type="range" min="0.2" max="1" step="0.05" data-tonevol value="${esc(String(d.volume))}" aria-label="${t("tone.volume")}">`)}` : ""}
      ${d.source === "custom" ? `<p class="hint">${saved.custom_hash ? (saved.source === "custom" ? t("tone.custom_have") : t("tone.custom_stored")) : t("tone.custom_none")}</p>
        <div class="row"><label class="b">${t("tone.upload")}<input type="file" accept=".wav,.mp3,.ogg,.flac,audio/wav,audio/mpeg,audio/ogg,audio/flac" data-act="toneupload" hidden></label>
        <button class="b" data-act="tonerec" ${busy ? "disabled" : ""}>${this.s.toneRec ? `<span class="spin"></span>${t("tone.recording", { seconds: this.s.toneRec })}` : t("tone.record")}</button>
        ${saved.custom_hash ? `<button class="b" data-act="tonedel" ${busy ? "disabled" : ""}>${t("tone.delete")}</button>` : ""}</div>
        <p class="hint">${t("tone.custom_hint")}</p>` : ""}
      <div class="row"><button class="b" data-act="tonepreview" ${busy ? "disabled" : ""}>${t("tone.preview")}</button>
        ${d.source !== "custom" || (saved.custom_hash && saved.source !== "custom") ? `<button class="b primary" data-act="tonesave" ${busy || !changed ? "disabled" : ""}>${t("tone.apply")}</button>` : ""}</div>
      ${sats.length ? `<div class="row"><select data-bind="toneSat" aria-label="${t("tone.device")}">${sats.map((e) => `<option value="${esc(e)}" ${this.s.toneSat === e ? "selected" : ""}>${esc(this._hass.states[e]?.attributes?.friendly_name || e)}</option>`).join("")}</select>
        <button class="b" data-act="toneplay" ${busy || changed ? "disabled" : ""}>${t("tone.play_device")}</button></div>${changed ? `<p class="hint">${t("tone.play_saved_only")}</p>` : ""}` : ""}
      ${this._msg("tone")}</div>`;
  }

  _toneErr(err) {
    const code = String(err.code || "");
    this._setMsg("tone", TONE_ERRORS[code] ? this.t(TONE_ERRORS[code]) : this.t("messages.error", { error: err.message || code }), "err");
  }

  async _tonePlayLocal() {
    const d = this._toneDraft();
    const tone = { ...d }; delete tone.custom_hash;
    const r = await this.ws("tone/preview", { tone });
    const bin = Uint8Array.from(atob(r.data_b64), (c) => c.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bin], { type: r.ext === "wav" ? "audio/wav" : "audio/" + r.ext }));
    const audio = new Audio(url);
    audio.onended = () => URL.revokeObjectURL(url);
    await audio.play();
  }

  async _toneUploadBytes(bytes, ext) {
    let bin = ""; const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    const r = await this.ws("tone/upload", { ext, data_b64: btoa(bin) });
    this.s.toneDraft = { ...this._toneDraft(), ...r.tone };
    await this._reload(["overview"]);
    this._setMsg("tone", this.t("tone.saved_custom"), "ok");
  }

  // record ≤3 s straight as PCM (no MediaRecorder codecs), mono, trimmed, faded → WAV
  async _toneRecord() {
    if (!navigator.mediaDevices?.getUserMedia) { this._setMsg("tone", this.t("tone.err_no_mic"), "err"); return; }
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 } }); }
    catch (err) { this._setMsg("tone", this.t("tone.err_no_mic"), "err"); return; }
    const AC = window.AudioContext || window.webkitAudioContext;
    let ctx; try { ctx = new AC({ sampleRate: 22050 }); } catch (e) { ctx = new AC(); } // small file (96 kHz would exceed 512 KB)
    await ctx.resume();
    const src = ctx.createMediaStreamSource(stream);
    const proc = ctx.createScriptProcessor(4096, 1, 1);
    const parts = [];
    proc.onaudioprocess = (ev) => parts.push(new Float32Array(ev.inputBuffer.getChannelData(0)));
    src.connect(proc); proc.connect(ctx.destination);
    for (let s = 3; s > 0; s--) { this.s.toneRec = s; this._render(); await new Promise((res) => setTimeout(res, 1000)); }
    proc.disconnect(); src.disconnect(); stream.getTracks().forEach((tr) => tr.stop());
    const rate = ctx.sampleRate; await ctx.close();
    this.s.toneRec = 0;
    let all = new Float32Array(parts.reduce((n, p) => n + p.length, 0)); let o = 0; for (const p of parts) { all.set(p, o); o += p.length; }
    const peak = all.reduce((m, v) => Math.max(m, Math.abs(v)), 0);
    if (peak < 0.01) { this._setMsg("tone", this.t("tone.err_silent"), "err"); return; }
    const thr = peak * 0.0316; // −30 dB relative to the peak
    let a = all.findIndex((v) => Math.abs(v) > thr), b = all.length - 1; while (b > a && Math.abs(all[b]) <= thr) b--;
    all = all.slice(Math.max(0, a), b + 1);
    const fade = Math.min(Math.floor(rate * 0.005), Math.floor(all.length / 2));
    for (let i = 0; i < fade; i++) { all[i] *= i / fade; all[all.length - 1 - i] *= i / fade; }
    const buf = new ArrayBuffer(44 + all.length * 2), dv = new DataView(buf);
    const str = (off, s) => { for (let i = 0; i < s.length; i++) dv.setUint8(off + i, s.charCodeAt(i)); };
    str(0, "RIFF"); dv.setUint32(4, 36 + all.length * 2, true); str(8, "WAVE"); str(12, "fmt "); dv.setUint32(16, 16, true); dv.setUint16(20, 1, true); dv.setUint16(22, 1, true);
    dv.setUint32(24, rate, true); dv.setUint32(28, rate * 2, true); dv.setUint16(32, 2, true); dv.setUint16(34, 16, true); str(36, "data"); dv.setUint32(40, all.length * 2, true);
    for (let i = 0; i < all.length; i++) dv.setInt16(44 + i * 2, Math.max(-1, Math.min(1, all[i] / peak * 0.89)) * 32767, true);
    await this._toneUploadBytes(new Uint8Array(buf), "wav");
  }

  // ── messages ──────────────────────────────────────────────────────────────
  _corpusStatus(r) { return r.corpus_cases ? this.t("messages.all_passed", { count: r.corpus_cases }) : this.t("messages.active"); }
  _problems(r) { return r.problems ? " – " + r.problems.slice(0, 2).join("; ") : ""; }
  _regressions(r) { return r.regressions ? " " + this.t("messages.regressions", { count: r.regressions.length }) : ""; }
  _notSaved(r, extra = "") { return this.t("messages.not_saved", { reason: r.message || r.code, details: extra }); }

  // ── events ────────────────────────────────────────────────────────────────
  _closeTips() { this.shadowRoot.querySelectorAll(".bubble").forEach((b) => b.remove()); }

  async _click(e) {
    const info = e.target.closest(".info");
    if (info) {
      e.preventDefault(); e.stopPropagation();
      const had = info.parentElement.querySelector(".bubble"); this._closeTips(); if (had) return;
      const d = document.createElement("div"); d.className = "bubble"; d.setAttribute("role", "note"); d.textContent = info.dataset.tip; info.parentElement.appendChild(d); return;
    }
    if (!e.target.closest(".bubble")) this._closeTips();
    const el = e.target.closest("[data-act]");
    if (!el || el.tagName === "SELECT") return;
    const a = el.dataset.act;
    const t = (k, v) => this.t(k, v);
    if (el.tagName === "A") e.preventDefault();
    try {
      if (a === "menu") this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true }));
      else if (a === "tab") { this.s.view = el.dataset.v; history.replaceState(null, "", "#" + el.dataset.v); if (el.dataset.v === "monitor" || el.dataset.v === "start") await this._reload(["monitor", "overview"]); else this._render(); }
      else if (a === "pall") this.shadowRoot.querySelectorAll("[data-pid]").forEach((c) => { const it = this.s.proposals.find((i) => i.id === c.dataset.pid); c.checked = !c.disabled && it?.check?.status === "ok"; });
      else if (a === "paccept" || a === "preject") {
        const ids = [...this.shadowRoot.querySelectorAll("[data-pid]:checked")].map((c) => c.dataset.pid);
        if (!ids.length) return this._setMsg("prop", t("messages.nothing_selected"));
        this._setMsg("prop", a === "paccept" ? t("messages.accepting") : t("messages.removing"));
        if (a === "preject") { const r = await this.ws("proposals/reject", { ids }); this._setMsg("prop", t("messages.rejected", { count: r.removed })); }
        else {
          const r = await this.ws("proposals/accept", { ids });
          const refused = Object.keys(r.refused || {}).length;
          if (r.saved) this._setMsg("prop", t("messages.accepted", { count: r.applied.length, status: this._corpusStatus(r), details: refused ? " · " + t("messages.refused", { count: refused }) : "" }), "ok");
          else this._setMsg("prop", t("messages.not_accepted", { reason: r.message || r.code, details: this._regressions(r) + this._problems(r) }), "err");
        }
        await this._reload();
      } else if (a === "gen" && el.dataset.dry) {
        const run = this.s.setRun || 0;
        this.s.dryBusy = true; this.s.dry = null; this._setMsg("prop", t("messages.ai_working"));
        try { const r = await this.ws("proposals/generate", { what: el.dataset.what, dry_run: true }); if (run === (this.s.setRun || 0)) { this.s.dry = r; this.s.msg.prop = null; } }
        finally { if (run === (this.s.setRun || 0)) { this.s.dryBusy = false; this._render(); } }
      } else if (a === "gen") {
        this._setMsg("prop", t("messages.ai_working"));
        const r = await this.ws("proposals/generate", { what: el.dataset.what });
        this.s.msg.prop = { text: t("messages.generated", { count: r.added, details: r.errors.length ? " · " + t("messages.generate_notes", { count: r.errors.length, notes: r.errors.slice(0, 2).join("; ") }) : "" }), cls: "ok" };
        await this._reload(["proposals"]);
      } else if (a === "open") { this.s.selected = el.dataset.name; this.s.lang = null; this.s.confirmDelete = null; this._render(); this.shadowRoot.querySelector("main").scrollIntoView(); }
      else if (a === "close") { this.s.selected = null; this._render(); }
      else if (a === "elang") { this.s.lang = el.dataset.l; this._render(); }
      else if (a === "esave") await this._saveIntent(el.dataset.name, el.dataset.lang);
      else if (a === "etrans") {
        this._setMsg("edit", t("messages.translating"));
        const r = await this.ws("translate", { name: el.dataset.name, languages: el.dataset.langs.split(","), to_queue: true });
        this._setMsg("edit", t("messages.queued", { count: r.queued }), "ok");
        await this._reload(["proposals"]);
      } else if (a === "edel") { this.s.confirmDelete = el.dataset.name; this._render(); }
      else if (a === "edelno") { this.s.confirmDelete = null; this._render(); }
      else if (a === "edelyes") {
        const r = await this.ws("delete_intent", { name: el.dataset.name, base_rev: this.s.data.rev });
        if (r.saved) { this.s.selected = null; this.s.confirmDelete = null; this.s.msg.cmd = { text: t("messages.deleted", { name: el.dataset.name }), cls: "ok" }; await this._reload(); }
        else this._setMsg("edit", t("messages.not_deleted", { reason: r.message || r.code }), "err");
      } else if (a === "cats") { this.s.catsOpen = !this.s.catsOpen; this.s.catEdit = null; this._render(); }
      else if (a === "catdel") { this.s.catEdit.splice(+el.dataset.i, 1); this._render(); }
      else if (a === "catadd") { const v = this.shadowRoot.getElementById("newcat").value.trim(); if (v) { this.s.catEdit.push({ from: v, to: v }); this._render(); } }
      else if (a === "catsave") {
        const renames = {}; this.s.catEdit.forEach((c) => { if (c.from !== c.to) renames[c.from] = c.to; });
        const r = await this.ws("categories", { categories: this.s.catEdit.map((c) => c.to), renames, base_rev: this.s.data.rev });
        this.s.catsOpen = false; this.s.catEdit = null;
        this.s.msg.cmd = r.saved ? { text: t("messages.categories_saved"), cls: "ok" } : { text: this._notSaved(r), cls: "err" };
        await this._reload();
      } else if (a === "test") await this._test();
      else if (a === "corpus") {
        try { const r = await this._hass.callService("polyglot_assist", "run_tests", {}, undefined, false, true); this.s.corpus = Object.values(r.response || {})[0]; }
        catch (err) { this.s.corpus = { error: err.message || String(err) }; }
        this._render();
      } else if (a === "nadd") {
        const cur = ((this.s.data.data.names || {})[el.dataset.t] || {})[el.dataset.l] || [];
        await this._setNames(el.dataset.t, el.dataset.l, [...cur, el.dataset.n]);
      } else if (a === "ntarget") {
        const x = this.shadowRoot.getElementById("newtarget").value.trim();
        if (!/^[a-z_]+\.[a-z0-9_]+$/.test(x)) return this._setMsg("names", t("messages.entity_id"), "err");
        this.s.data.data.names = { ...(this.s.data.data.names || {}), [x]: {} }; this._render();
      } else if (a === "nsugg") {
        const names = this.s.data.data.names || {};
        const langs = this.s.overview.languages.filter((l) => l !== (this._hass.language || "de").slice(0, 2));
        const ids = Object.values(this._hass.areas || {}).map((x) => `area.${x.area_id}`).filter((id) => langs.some((l) => !((names[id] || {})[l] || []).length)).slice(0, Math.max(1, Math.floor(20 / Math.max(1, langs.length))));
        if (!ids.length) return this._setMsg("names", t("messages.no_gaps"));
        this._setMsg("names", t("messages.suggesting_names"));
        const r = await this.ws("suggest_names", { ids, languages: langs });
        this.s.nameSugg = r.proposals; this._setMsg("names", t("messages.names_suggested"), "ok");
      } else if (a === "tonepick") {
        const d = this._toneDraft(); const k = el.dataset.k;
        d[k] = k === "count" ? Number(el.dataset.v) : el.dataset.v; this._render();
      } else if (a === "tonepreview") {
        this.s.toneBusy = true; this._render();
        try { await this._tonePlayLocal(); this._setMsg("tone", ""); } catch (err) { this._toneErr(err); } finally { this.s.toneBusy = false; this._render(); }
      } else if (a === "tonesave") {
        const tone = { ...this._toneDraft() }; delete tone.custom_hash;
        this.s.toneBusy = true; this._render();
        try { await this.ws("options", { chime_tone: tone }); await this._reload(["overview"]); this._setMsg("tone", t("tone.saved"), "ok"); } catch (err) { this._toneErr(err); } finally { this.s.toneBusy = false; this._render(); }
      } else if (a === "toneplay") {
        const sat = this.s.toneSat || Object.keys(this._hass.states).find((x) => x.startsWith("assist_satellite."));
        this.s.toneBusy = true; this._setMsg("tone", t("tone.playing"));
        try { await this.ws("tone/play", { satellite: sat }); this._setMsg("tone", t("tone.played"), "ok"); } catch (err) { this._toneErr(err); } finally { this.s.toneBusy = false; this._render(); }
      } else if (a === "tonerec") {
        this.s.toneBusy = true; this._render();
        try { await this._toneRecord(); } catch (err) { this._toneErr(err); } finally { this.s.toneBusy = false; this.s.toneRec = 0; this._render(); }
      } else if (a === "tonedel") {
        this.s.toneBusy = true; this._render();
        try { const r = await this.ws("tone/delete"); this.s.toneDraft = { ...r.tone }; await this._reload(["overview"]); this._setMsg("tone", t("tone.deleted"), "ok"); } catch (err) { this._toneErr(err); } finally { this.s.toneBusy = false; this._render(); }
      } else if (a === "opt") {
        const k = el.dataset.k; const v = el.dataset.v !== undefined ? el.dataset.v : el.getAttribute("aria-checked") !== "true";
        const extra = k === "monitor" ? { monitor_time: (this.shadowRoot.getElementById("mtime")?.value || "03:00").slice(0, 5) } : {};
        await this.ws("options", { [k]: v, ...extra });
        setTimeout(() => this._reload(["overview", "monitor"]), k === "monitor" || k === "chat_log_tools" || k === "builtin_commands" || k === "voice_learn" ? 1500 : 0);
        this._setMsg(k === "monitor" ? "mon" : "set", t("messages.saved"), "ok");
      } else if (a === "langask") {
        const run = this.s.langRun = (this.s.langRun || 0) + 1;
        this.s.langBusy = true; this.s.langSteps = [{ k: "langs.step_ai_list", done: false }]; this._setMsg("lang", "");
        try {
          let offered = [];
          try { const r = await this.ws("suggest_languages"); offered = (r.languages || []).map((l) => String(l).toLowerCase()).filter(beLang); } catch (err) { /* no AI task: check what is known */ }
          if (run !== this.s.langRun) return;
          this.s.langSteps = [{ k: "langs.step_ai_list", done: true }, { k: "langs.step_stt_tts", done: true }, { k: "langs.step_ai_test", done: false }];
          this.s.langOffered = offered.filter((l) => !this._enabledLangs().includes(l)); this._render();
          const r = await this.ws("languages/check", { languages: this._setLangs(), ai: true });
          if (run !== this.s.langRun) return;
          this.s.langCheck = r;
          this.s.msg.lang = { text: r.ai_task ? this.t("langs.done") : this.t("langs.done_no_ai"), cls: "ok" };
        } catch (err) {
          if (run !== this.s.langRun) return; // a stopped run must not overwrite "stopped"
          this.s.msg.lang = { text: this.t("messages.error", { error: err.message || err.code || err }), cls: "err" };
        } finally {
          if (run === this.s.langRun) { this.s.langBusy = false; this.s.langSteps = null; this._render(); }
        }
      } else if (a === "langcancel") {
        this.s.langRun = (this.s.langRun || 0) + 1; this.s.langBusy = false; this.s.langSteps = null; this._setMsg("lang", this.t("langs.stopped"));
      } else if (a === "langapply") {
        const langs = [...new Set(this.s.langChosen || [])];
        if (!langs.length) return this._setMsg("lang", this.t("langs.min_one"), "err");
        const run = this.s.setRun || 0;
        this.s.langApplying = true; this._setMsg("lang", this.t("langs.applying"));
        try {
          await this.ws("options", { languages_enabled: langs });
          const same = (a1, b1) => a1.length === b1.length && a1.every((x) => b1.includes(x));
          let done = false;
          for (let i = 0; i < 20 && !done; i++) {
            await new Promise((res) => setTimeout(res, 500));
            if (run !== (this.s.setRun || 0)) return;
            try { const ov = await this.ws("overview"); if (same((ov.options || {}).languages_enabled || [], langs) && (ov.builtin_languages || []).length >= 0) { this.s.overview = ov; done = true; } } catch (err) { /* reloading */ }
          }
          this.s.langChosen = null;
          this.s.msg.lang = done ? { text: this.t("langs.applied", { languages: langs.map((l) => this._ln(l)).join(", ") }), cls: "ok" } : { text: this.t("langs.apply_slow"), cls: "" };
        } finally {
          if (run === (this.s.setRun || 0)) { this.s.langApplying = false; this._render(); }
        }
      } else if (a === "stttest") {
        const run = this.s.setRun || 0;
        this.s.sttBusy = true; this._setMsg("stt", this.t("langs.stt_running"));
        try { const r = await this.ws("languages/stt_test", { languages: this._setLangs() }); if (run === (this.s.setRun || 0)) { this.s.sttTest = r; this.s.msg.stt = null; } }
        finally { if (run === (this.s.setRun || 0)) { this.s.sttBusy = false; this._render(); } }
      } else if (a === "openpipes") {
        history.pushState(null, "", "/config/voice-assistants/assistants");
        window.dispatchEvent(new CustomEvent("location-changed"));
      } else if (a === "uitrans" || a === "uiload" || a === "uiexport") {
        const lang = el.dataset.l;
        if (!this._enTexts) this._enTexts = await fetchTexts("en");
        if (a === "uitrans") {
          this.s.uiBusy = true; this._setMsg("ui", this.t("uitexts.running"));
          try { const r = await this.ws("ui_texts/translate", { lang }); this.s.msg.ui = { text: this.t("uitexts.translated", { count: r.translated, total: r.total, refused: Object.keys(r.refused || {}).length }), cls: "ok" }; }
          finally { this.s.uiBusy = false; this._render(); }
          await this._loadUiLangs();
        }
        if (a === "uiexport") {
          const r = await this._hass.callWS({ type: "polyglot_assist/ui_texts/export", lang });
          const blob = new Blob([JSON.stringify(r.content, null, 2) + "\n"], { type: "application/json" });
          const link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = r.file; link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 5000);
          this._setMsg("ui", r.missing.length ? this.t("uitexts.exported_missing", { file: r.file, count: r.missing.length }) : this.t("uitexts.exported", { file: r.file }), r.missing.length ? "" : "ok");
        }
        const own = await this._hass.callWS({ type: "polyglot_assist/ui_texts/get", lang });
        this.s.uiOwn = { lang, texts: flatten(own.texts), machine: own.machine || [] };
        if (lang === this._activeUi()) await this._loadTexts();
        this._render();
      }
      else if (a === "export") { const r = await this._hass.callService("polyglot_assist", "export_yaml", {}, undefined, false, true); this._setMsg("backup", t("messages.exported", { file: Object.values(r.response || {})[0]?.file || "?" }), "ok"); }
      else if (a === "monrun") { this._setMsg("mon", t("messages.analysing")); await this.ws("monitor/run"); await this._reload(["monitor", "proposals"]); this._setMsg("mon", t("messages.analysed"), "ok"); }
    } catch (err) {
      const key = { gen: "prop", paccept: "prop", preject: "prop", etrans: "edit", esave: "edit", edelyes: "edit", nsugg: "names", nadd: "names", langask: "lang", langapply: "lang", stttest: "stt", uitrans: "ui", uiexport: "ui", uiload: "ui", opt: "set", export: "backup", monrun: "mon", catsave: "cmd" }[a] || "cmd";
      this._setMsg(key, t("messages.error", { error: err.message || err.code || err }), "err");
    }
  }

  async _change(e) {
    const el = e.target;
    if (el.dataset.bind) { this.s[el.dataset.bind] = el.value; this._render(); return; }
    if (el.dataset.langOpt) {
      const set = new Set(this.s.langChosen || this._enabledLangs());
      if (el.checked) set.add(el.dataset.langOpt); else set.delete(el.dataset.langOpt);
      this.s.langChosen = [...set]; this._render(); return;
    }
    if (el.dataset.vsat) {
      const cur = new Set(((this.s.overview || {}).options || {}).voice_learn_satellites || []);
      if (el.checked) cur.add(el.dataset.vsat); else cur.delete(el.dataset.vsat);
      try { await this.ws("options", { voice_learn_satellites: [...cur] }); this._setMsg("set", this.t("messages.saved"), "ok"); setTimeout(() => this._reload(["overview"]), 1500); }
      catch (err) { this._setMsg("set", this.t("messages.error", { error: err.message || err.code }), "err"); }
      return;
    }
    if (el.dataset.tonevol !== undefined) { this._toneDraft().volume = Number(el.value); this._render(); return; }
    if (el.dataset.act === "toneupload" && el.files && el.files[0]) {
      const f = el.files[0]; const ext = (f.name.split(".").pop() || "").toLowerCase();
      if (!["wav", "mp3", "ogg", "flac"].includes(ext)) { this._setMsg("tone", this.t("tone.err_type"), "err"); return; }
      if (f.size > 512 * 1024) { this._setMsg("tone", this.t("tone.err_too_big"), "err"); return; }
      this.s.toneBusy = true; this._setMsg("tone", this.t("tone.uploading"));
      try { await this._toneUploadBytes(new Uint8Array(await f.arrayBuffer()), ext); } catch (err) { this._toneErr(err); } finally { this.s.toneBusy = false; el.value = ""; this._render(); }
      return;
    }
    if (el.dataset.act === "chimetts") {
      try { await this.ws("options", { chime_tts: el.value || null }); this._setMsg("set", this.t("messages.saved"), "ok"); setTimeout(() => this._reload(["overview"]), 1500); }
      catch (err) { this._setMsg("set", err.code === "chime_tts_is_polyglot" ? this.t("tone.is_polyglot") : this.t("messages.error", { error: err.message || err.code }), "err"); }
      return;
    }
    if (el.dataset.act === "fallback") {
      this.s.fbBusy = true; this._setMsg("fb", this.t("fallback.saving"));
      try { await this.ws("fallback/set", { agent_id: el.value }); await this._reload(["overview"]); this.s.fbBusy = false; this._setMsg("fb", this.t("fallback.saved"), "ok"); }
      catch (err) { this.s.fbBusy = false; await this._reload(["overview"]); this._setMsg("fb", err.code === "fallback_is_polyglot" ? this.t("fallback.not_possible_long") : this.t("messages.error", { error: err.message || err.code }), "err"); }
      return;
    }
    if (el.dataset.uikey) {
      const draftKey = el.dataset.l + "|" + el.dataset.uikey, value = el.value;
      try { await this._hass.callWS({ type: "polyglot_assist/ui_texts/set", lang: el.dataset.l, key: el.dataset.uikey, text: value.trim() ? value : null });
        if (this.s.uiDraft && this.s.uiDraft[draftKey] === value) { const d = { ...this.s.uiDraft }; delete d[draftKey]; this.s.uiDraft = d; }
        const own = await this._hass.callWS({ type: "polyglot_assist/ui_texts/get", lang: el.dataset.l });
        this.s.uiOwn = { lang: el.dataset.l, texts: flatten(own.texts), machine: own.machine || [] };
        if (el.dataset.l === this._activeUi()) await this._loadTexts();
        this._setMsg("ui", this.t("messages.saved"), "ok"); }
      catch (err) { this._setMsg("ui", this.t("messages.error", { error: err.message || err.code }), "err"); }
      return;
    }
    if (el.dataset.act === "uilang") {
      // only a deliberate choice is remembered; "automatic" follows HA's language
      this._uiPref = okLang(el.value) ? el.value : "";
      try { if (this._uiPref) localStorage.setItem(LS_UI, this._uiPref); else localStorage.removeItem(LS_UI); } catch (err) { /* per-viewer convenience only */ }
      await this._loadTexts(); this._render(); return;
    }
    if (el.dataset.act === "entry") {
      // nothing of entry A may leak into entry B (editors, suggestions, results)
      Object.assign(this.s, { entryId: el.value, selected: null, monitor: null, catsOpen: false, catEdit: null,
        nameSugg: null, langSugg: null, testResult: undefined, corpus: null, confirmDelete: null, lang: null, msg: {},
        setLoaded: false, setRun: (this.s.setRun || 0) + 1, pipes: null, agents: null, langCheck: null, langChosen: null, langOffered: null,
        sttTest: null, sttBusy: false, uiOwn: null, uiBusy: false, uiNew: null, uiFilter: "", uiDraft: {}, fbBusy: false, langApplying: false,
        langRun: (this.s.langRun || 0) + 1, langBusy: false, langSteps: null, dry: null, dryBusy: false });
      try { localStorage.setItem("polyglot-entry", el.value); } catch (e) { /* per-viewer convenience only */ }
      await this._reload(["overview", "data", "proposals", "monitor"]); return;
    }
    if (el.id === "mtime") {
      try { await this.ws("options", { monitor_time: (el.value || "03:00").slice(0, 5) }); this._setMsg("mon", this.t("messages.time_saved"), "ok"); setTimeout(() => this._reload(["overview", "monitor"]), 1500); }
      catch (err) { this._setMsg("mon", this.t("messages.error", { error: err.message || err }), "err"); }
      return;
    }
    if (el.dataset.cat !== undefined) { this.s.catEdit[+el.dataset.cat].to = el.value.trim() || this.s.catEdit[+el.dataset.cat].to; return; }
    if (el.dataset.act === "safety") { await this._patchIntent(el.dataset.name, { safety: el.value === "normal" ? undefined : el.value }, "cmd"); return; }
    if (el.dataset.act === "aitask") {
      try { await this.ws("options", { translation_ai_task: el.value || null }); this._setMsg("set", this.t("messages.saved"), "ok"); await this._reload(["overview"]); }
      catch (err) { this._setMsg("set", err.code === "ai_task_has_tools" ? this.t("messages.ai_task_has_tools") : this.t("messages.error", { error: err.message }), "err"); await this._reload(["overview"]); }
      return;
    }
    if (el.dataset.nameT) {
      const names = el.value.split(",").map((x) => x.trim()).filter(Boolean);
      await this._setNames(el.dataset.nameT, el.dataset.nameL, names);
    }
  }

  _input(e) {
    if (e.target.dataset.tonevol !== undefined) { this._toneDraft().volume = Number(e.target.value); return; }
    if (e.target.dataset.uikey) { // keep what is typed: a re-render while another row saves must not lose it
      this.s.uiDraft = { ...(this.s.uiDraft || {}), [e.target.dataset.l + "|" + e.target.dataset.uikey]: e.target.value };
      return;
    }
    if (e.target.dataset.bind === "query") { this.s.query = e.target.value; clearTimeout(this._qt); this._qt = setTimeout(() => { const pos = e.target.selectionStart; this._render(); const i = this.shadowRoot.querySelector('[data-bind="query"]'); if (i) { i.focus(); i.setSelectionRange(pos, pos); } }, 250); } }

  async _setNames(target, lang, names) {
    const r = await this.ws("names/set", { target, lang, names, base_rev: this.s.data.rev });
    if (r.saved) { this.s.msg.names = { text: this.t("messages.saved"), cls: "ok" }; await this._reload(["data"]); }
    else this._setMsg("names", this._notSaved(r, this._problems(r)), "err");
  }

  async _patchIntent(name, patch, msgKey) {
    if (this.s.overview.source !== "catalog") { this._setMsg(msgKey, this.t("messages.need_catalog"), "err"); return; }
    const intent = JSON.parse(JSON.stringify((this.s.data.data.intents || {})[name] || {}));
    Object.entries(patch).forEach(([k, v]) => { if (v === undefined || v === "") delete intent[k]; else intent[k] = v; });
    const r = await this.ws("save_intent", { name, intent, base_rev: this.s.data.rev });
    if (r.saved) { this.s.msg[msgKey] = { text: this.t("messages.saved"), cls: "ok" }; await this._reload(); }
    else this._setMsg(msgKey, this._notSaved(r, this._regressions(r) + this._problems(r)), "err");
  }

  async _saveIntent(name, lang) {
    const g = (id) => this.shadowRoot.getElementById(id);
    const intent = JSON.parse(JSON.stringify((this.s.data.data.intents || {})[name] || {}));
    const lines = g("esents").value.split("\n").map((x) => x.trim()).filter(Boolean);
    intent.sentences = { ...(intent.sentences || {}) };
    if (lines.length) intent.sentences[lang] = lines; else delete intent.sentences[lang];
    const cat = g("ecat").value; if (cat && cat !== OTHER) intent.category = cat; else delete intent.category;
    const set = (k, v) => { if (v) intent[k] = v; else delete intent[k]; };
    set("kind", g("ekind").value); set("response_mode", g("eresp").value);
    set("safety", g("esafety").value === "normal" ? "" : g("esafety").value);
    const r = await this.ws("save_intent", { name, intent, base_rev: this.s.data.rev });
    if (r.saved) { this.s.msg.edit = { text: this.t("messages.saved_status", { status: this._corpusStatus(r) }), cls: "ok" }; await this._reload(); }
    else {
      const regs = r.regressions ? " – " + this.t("messages.regressions_list", { sentences: r.regressions.slice(0, 3).map((x) => this._q(x.text)).join(", ") }) : "";
      this._setMsg("edit", this._notSaved(r, regs + this._problems(r)), "err");
    }
  }

  async _test() {
    const text = this.shadowRoot.getElementById("tin").value.trim();
    if (!text) return;
    this.s.testText = text;
    try { this.s.testResult = await this.ws("test", { text, language: this.shadowRoot.getElementById("tlang").value }); }
    catch (err) { this.s.testResult = null; }
    this._render();
  }
}

// a newer version loaded into a page that still has the old one registered: keep the page working
// (the old element stays until the page is reloaded — the panel says so instead of throwing)
if (!customElements.get("polyglot-assist-panel")) customElements.define("polyglot-assist-panel", PolyglotAssistPanel);
else console.warn("Polyglot Assist: a newer panel version was loaded — reload the page to use it");
