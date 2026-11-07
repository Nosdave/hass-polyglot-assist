// Polyglot Assist panel — plain web component (no build step).
// Talks to the admin WebSocket API of the integration. All text that comes
// from the configuration or from an AI task is escaped before rendering.

const T = {
  de: {
    title: "Polyglot Assist",
    tabs: { start: "Start", proposals: "Vorschläge", commands: "Befehle", test: "Ausprobieren", names: "Namen", monitor: "Überwachung", settings: "Einstellungen" },
  },
};
const L = T.de;
const LANG_NAMES = { de: "Deutsch", en: "English", fr: "Français", nl: "Nederlands", it: "Italiano", es: "Español", pt: "Português", pl: "Polski" };
const OP_KIND = {
  add_intent: "neu", add_sentences: "neu", add_names: "neu", set_list_value: "ändern",
  set_field: "ändern", remove_sentences: "entfernen", remove_names: "entfernen", remove_list_value: "entfernen",
};
const ORIGIN = { new_devices: "Neue Geräte", starter: "Grundstock", monitor: "Überwachung", translation: "Übersetzung", manual: "Von Hand" };
const SAFETY = { normal: "normal", sensitive: "sicherheitsrelevant", disabled: "aus" };
const TIPS = {
  how: "Polyglot hört bei jedem Sprachbefehl zuerst hin. Kennt es den Satz, führt es ihn sofort aus, ohne KI und in der Sprache, in der du gesprochen hast. Sonst gibt es ihn an deinen KI-Assistenten weiter.",
  builtin: "Die Befehle, die Home Assistant mitbringt (Licht, Schalter, Rollläden, Timer, Uhrzeit, Wetter, Einkaufsliste …), funktionieren in allen gewählten Sprachen. Schlösser, Alarm, Garagen- und Haustüren werden so nie geschaltet.",
  proposals: "Hier sammelt Polyglot neue, geänderte und zu entfernende Befehle, Sätze und Namen. Nichts davon ist aktiv, bevor du es übernimmst. Jeder Vorschlag wurde geprüft: Er darf keinen anderen Befehl stören, und alles, was schon funktioniert, muss weiter funktionieren.",
  starter: "Die KI schaut sich deine Skripte, Szenen und Geräte an, die Polyglot noch nicht versteht, und schlägt Befehle und Namen in allen gewählten Sprachen vor. Dazu Arten für deine Befehle. Ideal beim ersten Einrichten.",
  newdev: "Nach einer neuen Integration oder einem neuen Gerät: Polyglot findet, was neu dazugekommen ist, und schlägt Namen und Befehle vor.",
  coverage: "Wie viele Beispielsätze es pro Sprache gibt. Orange: Eine Sprache hat deutlich weniger Varianten als die anderen. Mit „Übersetzen“ schlägt die KI fehlende vor.",
  safety: "Normal: Der Befehl wird sofort ausgeführt. Sicherheitsrelevant (z. B. Haustür, Garage, Auto): wird ausgeführt, ist aber markiert; Vorschläge dafür übernimmt Polyglot nie automatisch, und beim Ändern erscheint eine Warnung. Aus: Polyglot ignoriert den Befehl, der Satz geht an deinen KI-Assistenten.",
  groups: "Art: Licht, Energie, Wetter … (bei jedem Befehl einstellbar). Raum und Gerät ermittelt Polyglot aus den Geräten, die ein Befehl schaltet.",
  cats: "Arten ordnen deine Befehle. Du kannst eigene anlegen, umbenennen oder löschen. Beim Löschen kommen die Befehle in „Sonstiges“.",
  syntax: "[eckige Klammern] = darf fehlen · (a|b) = eins davon · {Platzhalter} = ein Raum, Gerät oder eine Zahl, die Polyglot einsetzt.",
  answer: "Sprechen: „Licht Couch an.“ Ton: ein kurzes Signal (braucht Polyglot TTS). Gilt nur für Schaltbefehle; Fragen werden immer gesprochen beantwortet.",
  kind: "Schaltbefehl: tut etwas (Licht an …). Frage: liefert eine Auskunft (Wetter, Status …) und wird immer gesprochen beantwortet.",
  test: "Schreib einen Satz, wie du ihn sagen würdest. Polyglot zeigt, ob und wie es ihn versteht. Es wird nichts geschaltet.",
  corpus: "Echte Sätze aus deinem Alltag, die immer gleich verstanden werden müssen. Bei jeder Änderung prüft Polyglot sie vorher; würde etwas kaputtgehen, wird nicht gespeichert.",
  names: "Wie du Räume und Geräte in jeder Sprache nennst, z. B. Wohnzimmer = living room = salon. Diese Namen ändern nichts an Home Assistant selbst und machen deinen KI-Assistenten nicht langsamer.",
  monitor: "Polyglot merkt sich die letzten 500 Sprachbefehle (nur Text, kein Ton, bleibt in deinem Home Assistant). Einmal am Tag wertet deine eigene KI aus, was oft nicht direkt verstanden wurde, und macht daraus Vorschläge.",
  languages: "Die Sprachen, die in deinem Zuhause gesprochen werden. Polyglot erkennt sie automatisch; abgewählte Sprachen werden nicht mehr beachtet.",
  aitask: "Die KI-Aufgabe, die Übersetzungen, Namen und Befehle vorschlägt. Sie muss ohne Werkzeuge sein: Sie liest fremde Sätze und darf dabei nichts schalten können.",
  followup: "Damit dein KI-Assistent „und jetzt auch das Piano“ versteht, erfährt er, was Polyglot gerade geschaltet hat.",
  fallback: "Dein KI-Assistent, der alles beantwortet, was kein fester Befehl ist.",
};

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const tip = (key) => `<span class="tip"><button class="info" type="button" aria-label="Erklärung" data-tip="${esc(TIPS[key])}">i</button></span>`;

const CSS = `
:host{display:block;background:var(--primary-background-color);color:var(--primary-text-color);font-family:var(--paper-font-body1_-_font-family,Roboto,system-ui,sans-serif);font-size:14px;min-height:100%}
*{box-sizing:border-box}
.top{position:sticky;top:0;z-index:5;background:var(--app-header-background-color,var(--primary-color));color:var(--app-header-text-color,#fff)}
.wrap{max-width:1180px;margin:0 auto;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
.appbar{display:flex;align-items:center;gap:12px;padding-block:14px}
.appbar h1{font-size:20px;font-weight:400;margin:0}.appbar .src{margin-left:auto;font-size:12px;opacity:.85}
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
.banner{border-radius:8px;padding:10px 12px;font-size:13px}
dl{display:grid;grid-template-columns:auto minmax(0,1fr);gap:4px 12px;margin:10px 0 0}dt{color:var(--secondary-text-color)}dd{margin:0;font-family:var(--code-font-family,monospace);font-size:12.5px;overflow-wrap:anywhere}
.langs{display:flex;flex-wrap:wrap;gap:8px}.langs label{display:inline-flex;gap:6px;align-items:center;border:1px solid var(--divider-color);border-radius:999px;padding:4px 12px;cursor:pointer}
.loading{padding:40px;text-align:center;color:var(--secondary-text-color)}
`;

class PolyglotAssistPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.s = { view: "start", group: "category", filter: "", query: "", selected: null, proposals: null, monitor: null, data: null, overview: null, msg: {}, propFilter: "", kindFilter: "", lang: null };
  }
  set hass(h) { this._hass = h; if (!this._started) { this._started = true; this._boot(); } }
  get hass() { return this._hass; }
  set narrow(n) { this._narrow = n; }

  ws(type, data = {}) {
    const extra = this.s.entryId ? { entry_id: this.s.entryId } : {};
    return this._hass.callWS({ type: `polyglot_assist/${type}`, ...extra, ...data });
  }

  async _boot() {
    this.shadowRoot.innerHTML = `<style>${CSS}</style><div class="loading">Polyglot wird geladen …</div>`;
    this.shadowRoot.addEventListener("click", (e) => this._click(e));
    this.shadowRoot.addEventListener("change", (e) => this._change(e));
    this.shadowRoot.addEventListener("input", (e) => this._input(e));
    this.shadowRoot.addEventListener("keydown", (e) => { if (e.key === "Escape") this._closeTips(); if (e.key === "Enter" && e.target.id === "tin") this._test(); });
    const hash = location.hash.slice(1);
    if (L.tabs[hash]) this.s.view = hash;
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
  _render() {
    const o = this.s.overview;
    const nProps = (this.s.proposals || []).length;
    const tabs = Object.entries(L.tabs).map(([k, v]) => `<button data-act="tab" data-v="${k}" ${this.s.view === k ? 'aria-current="page"' : ""}>${v}${k === "proposals" && nProps ? ` <span class="chip pri">${nProps}</span>` : ""}</button>`).join("");
    const langs = o ? o.languages.map((l) => esc(LANG_NAMES[l] || l)).join(" · ") : "";
    let body = `<div class="loading">Lädt …</div>`;
    if (this.s.error) body = `<div class="card"><div class="banner err">Polyglot antwortet nicht: ${esc(this.s.error)}</div><p class="hint">Ist die Integration geladen? Einstellungen → Geräte &amp; Dienste → Polyglot Assist.</p></div>`;
    else if (o) body = this[`_v_${this.s.view}`]();
    this.shadowRoot.innerHTML = `<style>${CSS}</style>
      <div class="top"><div class="wrap"><div class="appbar">${this._narrow ? '<button class="menu" data-act="menu" aria-label="Menü">☰</button>' : ""}<h1>${L.title}</h1>${(this.s.entries || []).length > 1 ? `<select data-act="entry" aria-label="Polyglot-Eintrag" style="margin-left:12px">${this.s.entries.map((x) => `<option value="${esc(x.entry_id)}" ${x.entry_id === this.s.entryId ? "selected" : ""}>${esc(x.title)}</option>`).join("")}</select>` : ""}<span class="src">${esc(langs)}</span></div>
      <nav class="tabs" aria-label="Bereiche">${tabs}</nav></div></div>
      <main class="wrap">${body}</main>`;
  }

  _v_start() {
    const o = this.s.overview, sum = o.summary;
    const total = Object.values(sum.sentences || {}).reduce((a, b) => a + b, 0);
    const props = (this.s.proposals || []).length;
    const hits = Object.values(o.intents).reduce((a, m) => a + (m.hits || 0), 0);
    const today = this.s.monitor && this.s.monitor.today;
    return `<section class="grid">
      <div class="card"><h2>So versteht dein Zuhause mehrere Sprachen${tip("how")}</h2>
        <div class="step"><span class="dot ok">✓</span><div><b>Sprachen</b><div class="hint">${esc(o.languages.map((l) => LANG_NAMES[l] || l).join(", "))} · <a href="#settings" data-act="tab" data-v="settings">ändern</a></div></div></div>
        <div class="step"><span class="dot ${o.builtin_languages.length ? "ok" : "warn"}">${o.builtin_languages.length ? "✓" : "!"}</span><div><b>Grundbefehle von Home Assistant</b>${tip("builtin")}<div class="hint">${o.builtin_languages.length ? "aktiv in " + esc(o.builtin_languages.map((l) => LANG_NAMES[l] || l).join(", ")) : "nicht verfügbar"}</div></div></div>
        <div class="step"><span class="dot ${props ? "warn" : "ok"}">${props ? "!" : "✓"}</span><div><b>${props ? props + " Vorschläge warten" : "Keine offenen Vorschläge"}</b><div class="hint"><a href="#proposals" data-act="tab" data-v="proposals">ansehen</a> · Grundstock und neue Geräte findest du dort</div></div></div>
        <div class="step"><span class="dot pri">${sum.active}</span><div><b>Eigene Befehle</b><div class="hint">${sum.active} aktiv mit ${total} Beispielsätzen · <a href="#commands" data-act="tab" data-v="commands">bearbeiten</a></div></div></div>
        ${o.problems.length ? `<div class="step"><span class="dot err">!</span><div><b>${o.problems.length} Hinweise in deinen Befehlen</b><div class="hint mono">${o.problems.slice(0, 3).map(esc).join("<br>")}</div></div></div>` : ""}
      </div>
      <div class="grid kpis">
        <div class="card kpi"><div class="l">Seit Start verstanden</div><div class="v">${hits}</div><div class="sub">sofort, ohne KI</div></div>
        ${today ? `<div class="card kpi"><div class="l">Heute an die KI</div><div class="v">${today.assistant}</div><div class="sub">von ${today.total} Anfragen</div></div>
        <div class="card kpi"><div class="l">Antwortzeit</div><div class="v">${today.hit_ms_p50 != null ? (today.hit_ms_p50 / 1000).toFixed(1) + " s" : "–"}</div><div class="sub">eigener Befehl · KI ${today.assistant_ms_p50 != null ? (today.assistant_ms_p50 / 1000).toFixed(1) + " s" : "–"}</div></div>` : `<div class="card kpi"><div class="l">Überwachung</div><div class="v">aus</div><div class="sub"><a href="#monitor" data-act="tab" data-v="monitor">einschalten</a></div></div>`}
        <div class="card kpi"><div class="l">Quelle</div><div class="v">${o.source === "catalog" ? "Katalog" : "Datei"}</div><div class="sub">${o.source === "catalog" ? "in Home Assistant gespeichert" : "intents.yaml – Panel-Änderungen brauchen den Katalog"}</div></div>
      </div></section>`;
  }

  _v_proposals() {
    const items = (this.s.proposals || []).filter((i) => (!this.s.propFilter || i.origin?.source === this.s.propFilter) && (!this.s.kindFilter || OP_KIND[i.op] === this.s.kindFilter));
    const readonly = this.s.overview.source !== "catalog";
    const rows = items.map((i) => {
      const st = i.check?.status || "ok";
      const stLabel = st === "ok" ? "geprüft" : st === "warn" ? "Hinweis" : "Fehler";
      const kind = OP_KIND[i.op] || "ändern";
      const p = i.payload || {};
      const ex = p.sentences ? p.sentences.join(" · ") : p.names ? p.names.join(" · ") : p.intent?.sentences ? Object.values(p.intent.sentences).flat().slice(0, 3).join(" · ") : p.field ? `${p.field} = ${JSON.stringify(p.value)}` : p.in ? `${p.in} → ${p.out ?? "–"}` : "";
      return `<div class="qrow"><input type="checkbox" data-pid="${esc(i.id)}" ${st === "err" || st === "error" ? "disabled" : ""} aria-label="Auswählen">
        <div><span class="chip ${kind === "neu" ? "ok" : kind === "entfernen" ? "warn" : "pri"}">${kind}</span> <b>${esc(i.title || i.op)}</b> <span class="chip ${st === "ok" ? "ok" : st === "warn" ? "warn" : "err"}">${stLabel}</span>
        <div class="hint"><b>Woher:</b> ${esc(i.origin?.detail || ORIGIN[i.origin?.source] || "")}</div>
        ${(i.check?.notes || []).length ? `<div class="hint">${esc(i.check.notes.join("; "))}</div>` : ""}
        ${ex ? `<div class="ex">${esc(ex)}</div>` : ""}</div><span class="chip pri">${esc(ORIGIN[i.origin?.source] || "")}</span></div>`;
    }).join("");
    return `<section class="grid"><div class="card">
      <div class="row" style="justify-content:space-between"><h2 style="margin:0">Vorschläge${tip("proposals")}</h2>
        <div class="row"><button class="b" data-act="pall">Alle geprüften auswählen</button><button class="b p" data-act="paccept" ${readonly ? "disabled" : ""}>Ausgewählte übernehmen</button><button class="b" data-act="preject">Ablehnen</button></div></div>
      ${readonly ? `<div class="banner warn" style="margin-top:10px">Deine Befehle liegen noch in der Datei intents.yaml. Zum Übernehmen von Vorschlägen stelle unter Einstellungen die Quelle auf „Katalog“ (die Datei bleibt als Sicherung).</div>` : ""}
      <div class="row" style="margin-top:12px">
        <button class="b" data-act="gen" data-what="starter">Grundstock anlegen</button>${tip("starter")}
        <button class="b" data-act="gen" data-what="new_devices">Neue Geräte prüfen</button>${tip("newdev")}
        <select data-bind="propFilter" aria-label="Herkunft"><option value="">Alle Herkünfte</option>${Object.entries(ORIGIN).map(([k, v]) => `<option value="${k}" ${this.s.propFilter === k ? "selected" : ""}>${v}</option>`).join("")}</select>
        <select data-bind="kindFilter" aria-label="Art"><option value="">Alle Arten</option>${["neu", "ändern", "entfernen"].map((k) => `<option ${this.s.kindFilter === k ? "selected" : ""}>${k}</option>`).join("")}</select>
      </div>${this._msg("prop")}
      ${rows || `<p class="hint" style="margin-top:16px">Keine Vorschläge. Starte mit „Grundstock anlegen“ oder „Neue Geräte prüfen“.</p>`}
    </div></section>`;
  }

  _groupKey(meta) {
    if (this.s.group === "category") return meta.category || "Sonstiges";
    if (this.s.group === "room") return meta.rooms.length === 1 ? meta.rooms[0] : meta.rooms.length ? "Mehrere Räume" : "Ohne Raum";
    if (this.s.group === "device") return meta.devices.length === 1 ? meta.devices[0] : meta.devices.length ? "Mehrere Geräte" : "Ohne Gerät";
    return "";
  }

  _v_commands() {
    const o = this.s.overview, q = this.s.query.toLowerCase();
    const langs = Object.keys(o.summary.sentences || {});
    const entries = Object.entries(o.intents).filter(([n, m]) => (!q || n.toLowerCase().includes(q) || (m.description || "").toLowerCase().includes(q)) &&
      (!this.s.filter || (this.s.filter === "sensitive" && m.safety === "sensitive") || (this.s.filter === "off" && m.safety === "disabled") ||
       (this.s.filter === "gap" && Math.min(...langs.map((l) => m.sentences[l] || 0)) < Math.max(...langs.map((l) => m.sentences[l] || 0)) * 0.5)));
    const groups = {};
    entries.forEach(([n, m]) => (groups[this._groupKey(m)] ||= []).push([n, m]));
    const cats = [...new Set([...(o.categories || []), ...Object.values(o.intents).map((m) => m.category).filter(Boolean), "Sonstiges"])];
    const row = ([n, m]) => {
      const max = Math.max(1, ...langs.map((l) => m.sentences[l] || 0));
      const cov = langs.map((l) => { const c = m.sentences[l] || 0; return `${esc(l.toUpperCase())} <span class="lang ${c === 0 ? "err" : c < Math.max(2, max * 0.5) ? "warn" : "ok"}">${c}</span>`; }).join(" ");
      return `<tr class="click" data-act="open" data-name="${esc(n)}"><td><div class="name">${esc(n)}</div><div class="desc">${esc(m.description)}</div></td><td>${cov}</td>
        <td><select data-act="safety" data-name="${esc(n)}" aria-label="Schutz">${Object.entries(SAFETY).map(([k, v]) => `<option value="${k}" ${m.safety === k ? "selected" : ""}>${v}</option>`).join("")}</select></td>
        <td style="text-align:right">${m.hits || "–"}</td></tr>`;
    };
    const table = Object.keys(groups).sort().map((g) => (g ? `<tr><th class="group" colspan="4">${esc(g)} · ${groups[g].length}</th></tr>` : "") + groups[g].map(row).join("")).join("");
    return `<section class="grid">
      ${this.s.selected ? this._editor(this.s.selected, cats) : ""}
      <div class="card">
      <div class="row">
        <input type="search" data-bind="query" value="${esc(this.s.query)}" placeholder="Befehl suchen" aria-label="Befehl suchen">
        <label class="hint" for="grp">Gruppieren nach</label><select id="grp" data-bind="group">${[["category", "Art"], ["room", "Raum"], ["device", "Gerät"], ["", "keine Gruppen"]].map(([k, v]) => `<option value="${k}" ${this.s.group === k ? "selected" : ""}>${v}</option>`).join("")}</select>${tip("groups")}
        <select data-bind="filter" aria-label="Filter"><option value="">Alle</option><option value="gap" ${this.s.filter === "gap" ? "selected" : ""}>Sprache fehlt</option><option value="sensitive" ${this.s.filter === "sensitive" ? "selected" : ""}>Sicherheitsrelevant</option><option value="off" ${this.s.filter === "off" ? "selected" : ""}>Ausgeschaltet</option></select>
        <button class="b" data-act="cats">Arten verwalten</button>
        <span class="hint">${entries.length} von ${Object.keys(o.intents).length}</span>
      </div>${this._msg("cmd")}
      ${this.s.catsOpen ? this._catsBox(cats) : ""}
      <div class="tbl" style="margin-top:12px"><table><thead><tr><th>Befehl</th><th>Sätze je Sprache${tip("coverage")}</th><th>Schutz${tip("safety")}</th><th style="text-align:right">Treffer</th></tr></thead><tbody>${table}</tbody></table></div>
    </div></section>`;
  }

  _catsBox(cats) {
    const counts = {};
    Object.values(this.s.overview.intents).forEach((m) => { const c = m.category || "Sonstiges"; counts[c] = (counts[c] || 0) + 1; });
    this.s.catEdit ||= cats.filter((c) => c !== "Sonstiges").map((c) => ({ from: c, to: c }));
    return `<div class="card" style="margin-top:12px;background:var(--primary-background-color)"><h2>Arten verwalten${tip("cats")}</h2>
      <div class="grid" style="gap:6px">${this.s.catEdit.map((c, i) => `<div class="row"><input type="text" data-cat="${i}" value="${esc(c.to)}" aria-label="Art umbenennen" style="flex:1;min-width:0"><span class="hint">${counts[c.from] || 0} Befehle</span><button class="b" data-act="catdel" data-i="${i}">Löschen</button></div>`).join("")}</div>
      <div class="row" style="margin-top:8px"><input type="text" id="newcat" placeholder="Neue Art, z. B. Garten" aria-label="Neue Art"><button class="b" data-act="catadd">Hinzufügen</button><button class="b p" data-act="catsave">Arten speichern</button></div></div>`;
  }

  _editor(name, cats) {
    const intent = (this.s.data?.data?.intents || {})[name];
    if (!intent) return "";
    const langs = Object.keys(this.s.overview.summary.sentences || {});
    const missing = langs.filter((l) => !(intent.sentences || {})[l]?.length);
    const lang = this.s.lang && langs.includes(this.s.lang) ? this.s.lang : langs[0];
    const sents = ((intent.sentences || {})[lang] || []).join("\n");
    const ro = this.s.overview.source !== "catalog";
    return `<div class="card"><div class="row" style="justify-content:space-between"><h2 style="margin:0">${esc(name)}</h2><button class="b" data-act="close">Schließen</button></div>
      <p class="hint">${esc(intent.description || "")}</p>
      ${intent.safety === "sensitive" ? `<div class="banner warn">Sicherheitsrelevanter Befehl: Änderungen bitte genau prüfen.</div>` : ""}
      <div class="row" style="margin:10px 0">${langs.map((l) => `<button class="b" data-act="elang" data-l="${esc(l)}" aria-pressed="${l === lang}" style="${l === lang ? "border-color:var(--primary-color)" : ""}">${esc(LANG_NAMES[l] || l)} · ${((intent.sentences || {})[l] || []).length}</button>`).join("")}</div>
      <textarea id="esents" aria-label="Sätze, einer pro Zeile">${esc(sents)}</textarea>
      <p class="hint">${esc(TIPS.syntax)}</p>
      <div class="row">
        <label class="hint">Art <select id="ecat">${cats.map((c) => `<option ${((intent.category || "Sonstiges") === c) ? "selected" : ""}>${esc(c)}</option>`).join("")}</select></label>
        <label class="hint">Typ <select id="ekind"><option value="">–</option><option value="command" ${intent.kind === "command" ? "selected" : ""}>Schaltbefehl</option><option value="question" ${intent.kind === "question" ? "selected" : ""}>Frage</option></select></label>${tip("kind")}
        <label class="hint">Antwort <select id="eresp"><option value="">wie eingestellt</option><option value="text" ${intent.response_mode === "text" ? "selected" : ""}>Sprechen</option><option value="chime" ${intent.response_mode === "chime" ? "selected" : ""}>Ton</option></select></label>${tip("answer")}
        <label class="hint">Schutz <select id="esafety">${Object.entries(SAFETY).map(([k, v]) => `<option value="${k}" ${(intent.safety || "normal") === k ? "selected" : ""}>${v}</option>`).join("")}</select></label>${tip("safety")}
      </div>
      <div class="row" style="margin-top:12px"><button class="b p" data-act="esave" data-name="${esc(name)}" data-lang="${esc(lang)}" ${ro ? "disabled" : ""}>Speichern</button>
        ${missing.length ? `<button class="b" data-act="etrans" data-name="${esc(name)}" data-langs="${esc(missing.join(","))}">Übersetzen nach ${esc(missing.map((l) => LANG_NAMES[l] || l).join(", "))}</button>` : `<button class="b" data-act="etrans" data-name="${esc(name)}" data-langs="${esc(langs.filter((l) => l !== lang).join(","))}">Mehr Varianten vorschlagen</button>`}
        <button class="b" data-act="edel" data-name="${esc(name)}" ${ro ? "disabled" : ""}>Löschen</button>
        ${ro ? `<span class="hint">Zum Bearbeiten im Panel unter Einstellungen die Quelle „Katalog“ wählen.</span>` : ""}</div>
      ${this.s.confirmDelete === name ? `<div class="banner err" style="margin-top:10px">„${esc(name)}“ wirklich löschen? Er ist danach nicht mehr verfügbar. <button class="b" data-act="edelyes" data-name="${esc(name)}">Ja, löschen</button> <button class="b" data-act="edelno">Abbrechen</button></div>` : ""}
      ${this._msg("edit")}</div>`;
  }

  _v_test() {
    const r = this.s.testResult;
    const res = r === undefined ? "" : r ? `<span class="chip ok">Verstanden</span><dl><dt>Befehl</dt><dd>${esc(r.intent)}</dd><dt>Sprache</dt><dd>${esc(LANG_NAMES[r.lang] || r.lang)}</dd><dt>Erkannt</dt><dd>${Object.entries(r.slots || {}).filter(([k]) => k !== "lang").map(([k, v]) => esc(k + " = " + (v.text || v.value))).join("<br>") || "–"}</dd><dt>Satz</dt><dd>${esc(r.sentence)}</dd></dl>`
      : `<span class="chip warn">Nicht als eigener Befehl erkannt</span><p class="hint">Polyglot prüft dann die Grundbefehle von Home Assistant; sonst geht der Satz an deinen KI-Assistenten (${esc(this.s.overview.fallback_agent)}).</p>`;
    const c = this.s.corpus;
    return `<section class="grid two"><div class="card"><h2>Satz ausprobieren${tip("test")}</h2>
      <div class="row"><input type="text" id="tin" value="${esc(this.s.testText || "")}" placeholder="z. B. Schalt die Küchendecke aus" style="flex:1;min-width:0" aria-label="Testsatz">
      <select id="tlang" aria-label="Sprache der Pipeline">${this.s.overview.languages.map((l) => `<option value="${esc(l)}">${esc(LANG_NAMES[l] || l)}</option>`).join("")}</select>
      <button class="b p" data-act="test">Ausprobieren</button></div><div class="msg">${res}</div></div>
      <div class="card"><h2>Prüfsätze${tip("corpus")}</h2>
      ${c ? (c.error ? `<div class="banner err">${esc(c.error)}</div>` : `<div class="banner ${c.failed.length ? "warn" : "ok"}">${c.passed} von ${c.total} Sätzen wie erwartet</div>${c.failed.slice(0, 8).map((f) => `<div class="hint mono">„${esc(f.text)}“: erwartet ${esc(f.expected.intent)}, erkannt ${esc((f.got || {}).intent)}</div>`).join("")}`) : `<p class="hint">Noch nicht geprüft.</p>`}
      <button class="b" data-act="corpus" style="margin-top:8px">Jetzt prüfen</button></div></section>`;
  }

  _v_names() {
    const names = this.s.data?.data?.names || {};
    const langs = this.s.overview.languages;
    const areas = Object.values(this._hass.areas || {});
    const targets = [...areas.map((a) => [`area.${a.area_id}`, a.name, "Raum"]), ...Object.keys(names).filter((t) => !t.startsWith("area.")).map((t) => [t, this._hass.states[t]?.attributes?.friendly_name || t, t.split(".")[0]])];
    const sugg = this.s.nameSugg || {};
    const ro = this.s.overview.source !== "catalog";
    const rows = targets.map(([id, label, kind]) => `<tr><td><span class="name">${esc(label)}</span><div class="desc">${esc(kind)}</div></td>${langs.map((l) => {
      const cur = ((names[id] || {})[l] || []).join(", ");
      const s = (sugg[id] || {})[l];
      const sHtml = Array.isArray(s) ? s.filter((x) => x.status === "ok").map((x) => `<button class="b" data-act="nadd" data-t="${esc(id)}" data-l="${esc(l)}" data-n="${esc(x.name)}" style="padding:2px 8px;margin-top:4px">+ ${esc(x.name)}</button>`).join(" ") : "";
      return `<td><input type="text" data-name-t="${esc(id)}" data-name-l="${esc(l)}" value="${esc(cur)}" aria-label="${esc(label)} ${esc(l)}" ${ro ? "disabled" : ""} style="width:100%">${sHtml}</td>`;
    }).join("")}</tr>`).join("");
    return `<section class="grid"><div class="card"><h2>Namen in jeder Sprache${tip("names")}</h2>
      <div class="row"><input type="text" id="newtarget" placeholder="Gerät hinzufügen, z. B. light.couch" aria-label="Gerät"><button class="b" data-act="ntarget">Hinzufügen</button>
      <button class="b" data-act="nsugg" style="margin-left:auto">Lücken vorschlagen lassen</button></div>${this._msg("names")}
      <p class="hint">Mehrere Namen mit Komma trennen. Gespeichert wird beim Verlassen des Feldes.</p>
      <div class="tbl"><table><thead><tr><th>Raum / Gerät</th>${langs.map((l) => `<th>${esc(LANG_NAMES[l] || l)}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div></div></section>`;
  }

  _v_monitor() {
    const m = this.s.monitor;
    if (!m) { this._reload(["monitor"]); return `<div class="loading">Lädt …</div>`; }
    const opts = this.s.overview.options || {};
    const sw = `<div class="set" style="padding-top:0"><div><div class="t">Sprechfluss überwachen${tip("monitor")}</div><div class="hint">Täglich auswerten um <input type="time" id="mtime" value="${esc(opts.monitor_time || "03:00")}" aria-label="Uhrzeit"></div></div>
      <button class="switch" role="switch" data-act="opt" data-k="monitor" aria-checked="${m.enabled ? "true" : "false"}" aria-label="Überwachung"></button></div>`;
    if (!m.enabled) return `<section class="grid"><div class="card">${sw}${this._msg("mon")}</div></section>`;
    const s = m.summary || {};
    const log = (m.log || []).slice(0, 40).map((e) => `<tr><td class="hint">${esc(new Date(e.ts).toLocaleString())}</td><td>${esc(e.text)}</td><td><span class="chip ${e.error ? "err" : e.result === "hit" ? "ok" : "pri"}">${e.error ? "Fehler" : e.result === "hit" ? (e.builtin ? "Grundbefehl" : "sofort") : "KI"}</span></td><td class="hint">${e.ms != null ? (e.ms / 1000).toFixed(1) + " s" : ""}</td></tr>`).join("");
    return `<section class="grid two"><div class="card">${sw}
      <h2 style="margin-top:12px">Letzte Auswertung</h2>
      ${s.when ? `<p class="hint">${esc(new Date(s.when).toLocaleString())} · ${s.total} Anfragen, ${s.hits} sofort, ${s.assistant} an die KI, ${s.proposals} Vorschläge</p>
      <ul class="hint" style="padding-left:18px">${(s.repeated || []).map((r) => `<li>„${esc(r.text)}“ ging ${r.count}× an die KI</li>`).join("")}${(s.corrections || []).map((c) => `<li>Möglicherweise falsch verstanden: „${esc(c.before)}“ → danach „${esc(c.correction)}“</li>`).join("")}</ul>` : `<p class="hint">Noch keine Auswertung.</p>`}
      <button class="b" data-act="monrun">Jetzt auswerten</button>${this._msg("mon")}</div>
      <div class="card"><h2>Protokoll</h2><div class="tbl"><table><tbody>${log || `<tr><td class="hint">Noch keine Einträge.</td></tr>`}</tbody></table></div></div></section>`;
  }

  _v_settings() {
    const o = this.s.overview, opts = o.options || {};
    const available = Object.keys(o.summary.sentences || {});
    const enabled = opts.languages_enabled && opts.languages_enabled.length ? opts.languages_enabled : available;
    const aiTasks = Object.keys(this._hass.states).filter((e) => e.startsWith("ai_task."));
    const mode = opts.response_mode || "text";
    return `<section class="grid two"><div class="card"><h2>Einstellungen</h2>
      <div class="set"><div><div class="t">Sprachen${tip("languages")}</div>
        <div class="langs" style="margin-top:6px">${available.map((l) => `<label><input type="checkbox" data-lang-opt="${esc(l)}" ${enabled.includes(l) ? "checked" : ""}> ${esc(LANG_NAMES[l] || l)}</label>`).join("")}</div>
        <div class="row" style="margin-top:8px"><button class="b" data-act="langsave">Sprachen speichern</button><button class="b" data-act="langsugg">Welche Sprachen kann meine KI?</button></div>
        ${this.s.langSugg ? `<p class="hint">Deine KI nennt: ${esc(this.s.langSugg.map((l) => LANG_NAMES[l] || l).join(", "))}. Für neue Sprachen schlägt „Übersetzen“ im Befehl Sätze vor.</p>` : ""}</div><span></span></div>
      <div class="set"><div><div class="t">Bestätigung bei Schaltbefehlen${tip("answer")}</div></div>
        <div class="seg" role="group" aria-label="Bestätigung"><button data-act="opt" data-k="response_mode" data-v="text" aria-pressed="${mode === "text"}">Sprechen</button><button data-act="opt" data-k="response_mode" data-v="chime" aria-pressed="${mode === "chime"}">Ton</button></div></div>
      <div class="set"><div><div class="t">KI für Vorschläge${tip("aitask")}</div></div>
        <select data-act="aitask" aria-label="KI-Aufgabe"><option value="">– keine –</option>${aiTasks.map((e) => `<option value="${esc(e)}" ${opts.translation_ai_task === e ? "selected" : ""}>${esc(this._hass.states[e]?.attributes?.friendly_name || e)}</option>`).join("")}</select></div>
      <div class="set"><div><div class="t">Folgebefehle verstehen${tip("followup")}</div></div>
        <button class="switch" role="switch" data-act="opt" data-k="chat_log_tools" aria-checked="${opts.chat_log_tools ? "true" : "false"}" aria-label="Folgebefehle"></button></div>
      <div class="set"><div><div class="t">Wenn Polyglot nichts erkennt${tip("fallback")}</div><div class="hint">${esc(o.fallback_agent)} · ändern in Einstellungen → Geräte &amp; Dienste → Polyglot Assist</div></div><span></span></div>
      ${this._msg("set")}</div>
      <div class="card"><h2>Sicherung</h2>
        <p class="hint">${o.source === "catalog" ? "Deine Befehle liegen in Home Assistant und werden mit seinen Backups gesichert." : "Deine Befehle liegen in der Datei intents.yaml. Für das Bearbeiten im Panel auf „Katalog“ umstellen (die Datei bleibt als Sicherung) – in Einstellungen → Geräte & Dienste → Polyglot Assist → Konfigurieren → Quelle."}</p>
        <div class="grid" style="gap:8px"><button class="b" data-act="export" ${o.source === "catalog" ? "" : "disabled"}>Als Datei exportieren</button></div>${this._msg("backup")}</div></section>`;
  }

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
    if (el.tagName === "A") e.preventDefault();
    try {
      if (a === "menu") this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true }));
      else if (a === "tab") { this.s.view = el.dataset.v; history.replaceState(null, "", "#" + el.dataset.v); if (el.dataset.v === "monitor" || el.dataset.v === "start") await this._reload(["monitor", "overview"]); else this._render(); }
      else if (a === "pall") this.shadowRoot.querySelectorAll("[data-pid]").forEach((c) => { const it = this.s.proposals.find((i) => i.id === c.dataset.pid); c.checked = !c.disabled && it?.check?.status === "ok"; });
      else if (a === "paccept" || a === "preject") {
        const ids = [...this.shadowRoot.querySelectorAll("[data-pid]:checked")].map((c) => c.dataset.pid);
        if (!ids.length) return this._setMsg("prop", "Nichts ausgewählt.");
        this._setMsg("prop", a === "paccept" ? "Wird geprüft und übernommen …" : "Wird entfernt …");
        if (a === "preject") { const r = await this.ws("proposals/reject", { ids }); this._setMsg("prop", `${r.removed} abgelehnt.`); }
        else {
          const r = await this.ws("proposals/accept", { ids });
          if (r.saved) this._setMsg("prop", `${r.applied.length} übernommen · ${r.corpus_cases ? `alle ${r.corpus_cases} Prüfsätze bestanden` : "aktiv"}${Object.keys(r.refused || {}).length ? ` · ${Object.keys(r.refused).length} nicht übernehmbar` : ""}.`, "ok");
          else this._setMsg("prop", `Nicht übernommen: ${r.message || r.code}${r.regressions ? ` (${r.regressions.length} Prüfsätze würden nicht mehr funktionieren)` : ""}${r.problems ? " – " + r.problems.slice(0, 2).join("; ") : ""}`, "err");
        }
        await this._reload();
      } else if (a === "gen") {
        this._setMsg("prop", "Die KI arbeitet – das kann eine bis drei Minuten dauern …");
        const r = await this.ws("proposals/generate", { what: el.dataset.what });
        this.s.msg.prop = { text: `${r.added} neue Vorschläge${r.errors.length ? ` · ${r.errors.length} Hinweise: ${r.errors.slice(0, 2).join("; ")}` : ""}.`, cls: "ok" };
        await this._reload(["proposals"]);
      } else if (a === "open") { this.s.selected = el.dataset.name; this.s.lang = null; this.s.confirmDelete = null; this._render(); this.shadowRoot.querySelector("main").scrollIntoView(); }
      else if (a === "close") { this.s.selected = null; this._render(); }
      else if (a === "elang") { this.s.lang = el.dataset.l; this._render(); }
      else if (a === "esave") await this._saveIntent(el.dataset.name, el.dataset.lang);
      else if (a === "etrans") {
        this._setMsg("edit", "Die KI schlägt Sätze vor und Polyglot prüft sie …");
        const r = await this.ws("translate", { name: el.dataset.name, languages: el.dataset.langs.split(","), to_queue: true });
        this._setMsg("edit", `${r.queued} geprüfte Vorschläge liegen jetzt unter „Vorschläge“.`, "ok");
        await this._reload(["proposals"]);
      } else if (a === "edel") { this.s.confirmDelete = el.dataset.name; this._render(); }
      else if (a === "edelno") { this.s.confirmDelete = null; this._render(); }
      else if (a === "edelyes") {
        const r = await this.ws("delete_intent", { name: el.dataset.name, base_rev: this.s.data.rev });
        if (r.saved) { this.s.selected = null; this.s.confirmDelete = null; this.s.msg.cmd = { text: `${el.dataset.name} gelöscht.`, cls: "ok" }; await this._reload(); }
        else this._setMsg("edit", `Nicht gelöscht: ${r.message || r.code}`, "err");
      } else if (a === "cats") { this.s.catsOpen = !this.s.catsOpen; this.s.catEdit = null; this._render(); }
      else if (a === "catdel") { this.s.catEdit.splice(+el.dataset.i, 1); this._render(); }
      else if (a === "catadd") { const v = this.shadowRoot.getElementById("newcat").value.trim(); if (v) { this.s.catEdit.push({ from: v, to: v }); this._render(); } }
      else if (a === "catsave") {
        const renames = {}; this.s.catEdit.forEach((c) => { if (c.from !== c.to) renames[c.from] = c.to; });
        const r = await this.ws("categories", { categories: this.s.catEdit.map((c) => c.to), renames, base_rev: this.s.data.rev });
        this.s.catsOpen = false; this.s.catEdit = null;
        this.s.msg.cmd = r.saved ? { text: "Arten gespeichert.", cls: "ok" } : { text: `Nicht gespeichert: ${r.message || r.code}`, cls: "err" };
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
        const t = this.shadowRoot.getElementById("newtarget").value.trim();
        if (!/^[a-z_]+\.[a-z0-9_]+$/.test(t)) return this._setMsg("names", "Bitte eine Entitäts-ID wie light.couch eingeben.", "err");
        this.s.data.data.names = { ...(this.s.data.data.names || {}), [t]: {} }; this._render();
      } else if (a === "nsugg") {
        const names = this.s.data.data.names || {};
        const langs = this.s.overview.languages.filter((l) => l !== (this._hass.language || "de").slice(0, 2));
        const ids = Object.values(this._hass.areas || {}).map((x) => `area.${x.area_id}`).filter((id) => langs.some((l) => !((names[id] || {})[l] || []).length)).slice(0, Math.max(1, Math.floor(20 / Math.max(1, langs.length))));
        if (!ids.length) return this._setMsg("names", "Keine Lücken bei den Räumen.");
        this._setMsg("names", "Die KI schlägt Namen vor …");
        const r = await this.ws("suggest_names", { ids, languages: langs });
        this.s.nameSugg = r.proposals; this._setMsg("names", "Vorschläge erscheinen als Knöpfe unter den Feldern. Ein Klick übernimmt den Namen.", "ok");
      } else if (a === "opt") {
        const k = el.dataset.k; const v = el.dataset.v !== undefined ? el.dataset.v : el.getAttribute("aria-checked") !== "true";
        const extra = k === "monitor" ? { monitor_time: (this.shadowRoot.getElementById("mtime")?.value || "03:00").slice(0, 5) } : {};
        await this.ws("options", { [k]: v, ...extra });
        setTimeout(() => this._reload(["overview", "monitor"]), k === "monitor" || k === "chat_log_tools" ? 1500 : 0);
        this._setMsg(k === "monitor" ? "mon" : "set", "Gespeichert.", "ok");
      } else if (a === "langsave") {
        const langs = [...this.shadowRoot.querySelectorAll("[data-lang-opt]:checked")].map((c) => c.dataset.langOpt);
        if (!langs.length) return this._setMsg("set", "Mindestens eine Sprache wählen.", "err");
        await this.ws("options", { languages_enabled: langs }); this._setMsg("set", "Sprachen gespeichert.", "ok"); setTimeout(() => this._reload(["overview"]), 1500);
      } else if (a === "langsugg") { this._setMsg("set", "Frage deine KI …"); const r = await this.ws("suggest_languages"); this.s.langSugg = r.languages; this._setMsg("set", ""); }
      else if (a === "export") { const r = await this._hass.callService("polyglot_assist", "export_yaml", {}, undefined, false, true); this._setMsg("backup", `Gespeichert: ${Object.values(r.response || {})[0]?.file || "?"}`, "ok"); }
      else if (a === "monrun") { this._setMsg("mon", "Wird ausgewertet …"); await this.ws("monitor/run"); await this._reload(["monitor", "proposals"]); this._setMsg("mon", "Auswertung fertig.", "ok"); }
    } catch (err) {
      const key = { gen: "prop", paccept: "prop", preject: "prop", etrans: "edit", esave: "edit", edelyes: "edit", nsugg: "names", nadd: "names", langsugg: "set", opt: "set", langsave: "set", export: "backup", monrun: "mon", catsave: "cmd" }[a] || "cmd";
      this._setMsg(key, `Fehler: ${err.message || err.code || err}`, "err");
    }
  }

  async _change(e) {
    const el = e.target;
    if (el.dataset.bind) { this.s[el.dataset.bind] = el.value; this._render(); return; }
    if (el.dataset.act === "entry") {
      // nothing of entry A may leak into entry B (editors, suggestions, results)
      Object.assign(this.s, { entryId: el.value, selected: null, monitor: null, catsOpen: false, catEdit: null,
        nameSugg: null, langSugg: null, testResult: undefined, corpus: null, confirmDelete: null, lang: null, msg: {} });
      try { localStorage.setItem("polyglot-entry", el.value); } catch (e) { /* per-viewer convenience only */ }
      await this._reload(["overview", "data", "proposals", "monitor"]); return;
    }
    if (el.id === "mtime") {
      try { await this.ws("options", { monitor_time: (el.value || "03:00").slice(0, 5) }); this._setMsg("mon", "Uhrzeit gespeichert.", "ok"); setTimeout(() => this._reload(["overview", "monitor"]), 1500); }
      catch (err) { this._setMsg("mon", `Fehler: ${err.message || err}`, "err"); }
      return;
    }
    if (el.dataset.cat !== undefined) { this.s.catEdit[+el.dataset.cat].to = el.value.trim() || this.s.catEdit[+el.dataset.cat].to; return; }
    if (el.dataset.act === "safety") { await this._patchIntent(el.dataset.name, { safety: el.value === "normal" ? undefined : el.value }, "cmd"); return; }
    if (el.dataset.act === "aitask") {
      try { await this.ws("options", { translation_ai_task: el.value || null }); this._setMsg("set", "Gespeichert.", "ok"); await this._reload(["overview"]); }
      catch (err) { this._setMsg("set", err.code === "ai_task_has_tools" ? "Diese KI-Aufgabe hat Werkzeuge. Bitte eine ohne Werkzeuge wählen." : `Fehler: ${err.message}`, "err"); await this._reload(["overview"]); }
      return;
    }
    if (el.dataset.nameT) {
      const names = el.value.split(",").map((x) => x.trim()).filter(Boolean);
      await this._setNames(el.dataset.nameT, el.dataset.nameL, names);
    }
  }

  _input(e) { if (e.target.dataset.bind === "query") { this.s.query = e.target.value; clearTimeout(this._qt); this._qt = setTimeout(() => { const pos = e.target.selectionStart; this._render(); const i = this.shadowRoot.querySelector('[data-bind="query"]'); if (i) { i.focus(); i.setSelectionRange(pos, pos); } }, 250); } }

  async _setNames(target, lang, names) {
    const r = await this.ws("names/set", { target, lang, names, base_rev: this.s.data.rev });
    if (r.saved) { this.s.msg.names = { text: "Gespeichert.", cls: "ok" }; await this._reload(["data"]); }
    else this._setMsg("names", `Nicht gespeichert: ${r.message || r.code}${r.problems ? " – " + r.problems.slice(0, 2).join("; ") : ""}`, "err");
  }

  async _patchIntent(name, patch, msgKey) {
    if (this.s.overview.source !== "catalog") { this._setMsg(msgKey, "Zum Ändern bitte unter Einstellungen die Quelle „Katalog“ wählen.", "err"); return; }
    const intent = JSON.parse(JSON.stringify((this.s.data.data.intents || {})[name] || {}));
    Object.entries(patch).forEach(([k, v]) => { if (v === undefined || v === "") delete intent[k]; else intent[k] = v; });
    const r = await this.ws("save_intent", { name, intent, base_rev: this.s.data.rev });
    if (r.saved) { this.s.msg[msgKey] = { text: "Gespeichert.", cls: "ok" }; await this._reload(); }
    else this._setMsg(msgKey, `Nicht gespeichert: ${r.message || r.code}${r.regressions ? ` (${r.regressions.length} Prüfsätze würden nicht mehr funktionieren)` : ""}${r.problems ? " – " + r.problems.slice(0, 2).join("; ") : ""}`, "err");
  }

  async _saveIntent(name, lang) {
    const g = (id) => this.shadowRoot.getElementById(id);
    const intent = JSON.parse(JSON.stringify((this.s.data.data.intents || {})[name] || {}));
    const lines = g("esents").value.split("\n").map((x) => x.trim()).filter(Boolean);
    intent.sentences = { ...(intent.sentences || {}) };
    if (lines.length) intent.sentences[lang] = lines; else delete intent.sentences[lang];
    const cat = g("ecat").value; if (cat && cat !== "Sonstiges") intent.category = cat; else delete intent.category;
    const set = (k, v) => { if (v) intent[k] = v; else delete intent[k]; };
    set("kind", g("ekind").value); set("response_mode", g("eresp").value);
    set("safety", g("esafety").value === "normal" ? "" : g("esafety").value);
    const r = await this.ws("save_intent", { name, intent, base_rev: this.s.data.rev });
    if (r.saved) { this.s.msg.edit = { text: `Gespeichert · ${r.corpus_cases ? `alle ${r.corpus_cases} Prüfsätze bestanden` : "aktiv"}.`, cls: "ok" }; await this._reload(); }
    else this._setMsg("edit", `Nicht gespeichert: ${r.message || r.code}${r.regressions ? ` – ${r.regressions.slice(0, 3).map((x) => "„" + x.text + "“").join(", ")} würden nicht mehr funktionieren` : ""}${r.problems ? " – " + r.problems.slice(0, 2).join("; ") : ""}`, "err");
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

customElements.define("polyglot-assist-panel", PolyglotAssistPanel);
