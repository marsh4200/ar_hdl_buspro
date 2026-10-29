// AR HDL BUSPRO - "HDL Devices" sidebar page (device manager).
// Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za). All rights reserved.
// Licensed software - see LICENSE in the repository root.
//
// Talks to device_manager.py over the websocket (ar_hdl_buspro/dm/*).
// Controls (open a curtain, switch a channel) are ordinary Home Assistant
// service calls on the entities, so licence, availability and the curtain
// "Reverse direction" option all apply exactly as on a dashboard.

const WS = "ar_hdl_buspro/dm";
const DIAG_PANEL = "hdl-diagnostic";

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const FILTERS = [
  ["all", "All"],
  ["lights", "Lights & relays"],
  ["curtain", "Curtains"],
  ["sensor", "Sensors"],
  ["panel", "Panels"],
  ["new", "Not in HA"],
];

function ago(iso) {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
}

function isoDay(x) {
  return `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, "0")}-${String(x.getDate()).padStart(2, "0")}`;
}

const ICON = {
  refresh: '<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/></svg>',
  search: '<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>',
  warn: '<svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18h.01"/></svg>',
};

const STYLE = `
:host { display:block; min-height:100%; background:var(--primary-background-color); color:var(--primary-text-color); font-family:var(--paper-font-body1_-_font-family, Roboto, sans-serif); }
* { box-sizing:border-box; }
button, input, select { font:inherit; }
.bar { display:flex; align-items:center; gap:8px; height:56px; padding:0 12px; background:var(--app-header-background-color, var(--primary-color)); color:var(--app-header-text-color, #fff); }
.bar h1 { font-size:20px; font-weight:400; margin:0 0 0 4px; flex:1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.bar select { background:transparent; color:inherit; border:1px solid currentColor; border-radius:8px; padding:6px 8px; max-width:50%; }
.bar select option { color:#000; }
.wrap { max-width:1280px; margin:0 auto; padding:20px 16px 40px; display:flex; flex-direction:column; gap:16px; }
.head { display:flex; flex-wrap:wrap; gap:12px; align-items:center; justify-content:space-between; }
.sub { font-size:14px; color:var(--secondary-text-color); display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
.dot { width:8px; height:8px; border-radius:50%; display:inline-block; margin-right:6px; vertical-align:middle; }
.btn { height:40px; padding:0 16px; border-radius:20px; border:0; background:var(--primary-color); color:var(--text-primary-color, #fff); font-size:14px; font-weight:500; cursor:pointer; display:inline-flex; align-items:center; gap:8px; white-space:nowrap; }
.btn.ghost { background:none; color:var(--primary-color); border:1px solid var(--primary-color); }
.btn.danger { background:none; color:var(--error-color, #db4437); border:1px solid var(--error-color, #db4437); }
.btn.small { height:36px; padding:0 12px; font-size:13px; }
.btn[disabled] { opacity:.55; cursor:default; }
.btns { display:flex; flex-wrap:wrap; gap:8px; }
.stats { display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:12px; }
.stat { background:var(--card-background-color); border:1px solid var(--divider-color); border-radius:12px; padding:12px 16px; }
.stat span { font-size:13px; color:var(--secondary-text-color); }
.stat b { display:block; font-size:26px; font-weight:500; font-variant-numeric:tabular-nums; }
.stat.warn b { color:var(--warning-color, #c77700); }
.cols { display:flex; flex-wrap:wrap; gap:16px; align-items:flex-start; }
.card { background:var(--card-background-color); border:1px solid var(--divider-color); border-radius:var(--ha-card-border-radius, 12px); }
.list { flex:1 1 400px; min-width:0; overflow:hidden; }
.detail { flex:1 1 460px; min-width:0; padding:18px; display:flex; flex-direction:column; gap:18px; }
.tools { padding:12px 14px; border-bottom:1px solid var(--divider-color); display:flex; flex-direction:column; gap:10px; }
.searchbox { display:flex; align-items:center; gap:8px; height:42px; padding:0 12px; border:1px solid var(--divider-color); border-radius:10px; color:var(--secondary-text-color); }
.searchbox input { flex:1; min-width:0; border:0; outline:none; background:transparent; color:var(--primary-text-color); font-size:14px; }
.chips { display:flex; flex-wrap:wrap; gap:6px; }
.chip { height:34px; padding:0 12px; border-radius:17px; border:1px solid var(--divider-color); background:none; color:var(--primary-text-color); font-size:13px; font-weight:500; cursor:pointer; }
.chip.on { background:var(--primary-color); border-color:var(--primary-color); color:var(--text-primary-color, #fff); }
ul { list-style:none; margin:0; padding:0; max-height:70vh; overflow-y:auto; }
li + li { border-top:1px solid var(--divider-color); }
.row { width:100%; display:flex; align-items:center; gap:12px; padding:10px 14px; min-height:58px; border:0; background:none; color:inherit; text-align:left; cursor:pointer; }
.row:hover { background:var(--secondary-background-color); }
.row.on { background:rgba(var(--rgb-primary-color, 3,169,244), .12); box-shadow:inset 3px 0 0 var(--primary-color); }
.addr { font-family:var(--code-font-family, monospace); font-size:13px; color:var(--secondary-text-color); width:62px; flex-shrink:0; }
.rname { display:flex; flex-direction:column; gap:2px; min-width:0; flex:1; }
.rname b { font-weight:500; font-size:15px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.rname small { font-size:12px; color:var(--secondary-text-color); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.badge { flex-shrink:0; font-size:12px; font-weight:500; padding:3px 10px; border-radius:12px; }
.b-ha { background:rgba(67,160,71,.16); color:var(--success-color, #2e7d32); }
.b-new { background:rgba(var(--rgb-primary-color, 3,169,244), .16); color:var(--primary-color); }
.b-warn { background:rgba(255,152,0,.18); color:var(--warning-color, #c77700); }
.b-muted { background:var(--secondary-background-color); color:var(--secondary-text-color); }
.empty { padding:24px 16px; color:var(--secondary-text-color); font-size:14px; line-height:1.5; }
.dh { display:flex; flex-wrap:wrap; gap:12px; justify-content:space-between; align-items:flex-start; }
.dh .mono { font-family:var(--code-font-family, monospace); font-size:13px; color:var(--secondary-text-color); }
.dh h2 { margin:2px 0; font-size:22px; font-weight:500; }
.muted { color:var(--secondary-text-color); font-size:14px; }
h3 { margin:0; font-size:15px; font-weight:500; color:var(--secondary-text-color); }
.note { display:flex; gap:10px; padding:12px 14px; border-radius:10px; background:rgba(255,152,0,.14); font-size:14px; line-height:1.45; }
.note.info { background:var(--secondary-background-color); }
.ent { border:1px solid var(--divider-color); border-radius:12px; padding:12px 14px; display:flex; flex-direction:column; gap:10px; }
.ent-top { display:flex; flex-wrap:wrap; gap:10px; align-items:center; justify-content:space-between; }
.ent-name { display:flex; flex-direction:column; gap:2px; min-width:0; }
.ent-name b { font-weight:500; }
.link { background:none; border:0; padding:0; color:var(--primary-color); cursor:pointer; font-family:var(--code-font-family, monospace); font-size:12px; text-align:left; }
.state { font-weight:500; font-size:14px; }
.bar-o { height:10px; border-radius:5px; background:var(--divider-color); overflow:hidden; }
.bar-i { height:10px; background:var(--primary-color); }
.setting { display:flex; align-items:center; justify-content:space-between; gap:14px; min-height:44px; border-top:1px solid var(--divider-color); padding-top:10px; }
.setting label { display:flex; flex-direction:column; gap:2px; }
.setting small { color:var(--secondary-text-color); font-size:12px; }
.setting input[type=checkbox] { width:22px; height:22px; accent-color:var(--primary-color); }
.setting input[type=number], .edit input { height:38px; border:1px solid var(--divider-color); border-radius:8px; padding:0 10px; background:var(--card-background-color); color:var(--primary-text-color); }
.setting input[type=number] { width:90px; }
.edit { display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
.edit input { flex:1 1 200px; min-width:0; }
.act { display:flex; gap:12px; padding:9px 0; border-top:1px solid var(--divider-color); font-size:14px; line-height:1.4; }
.act time { font-family:var(--code-font-family, monospace); font-size:12px; color:var(--secondary-text-color); width:64px; flex-shrink:0; padding-top:2px; }
.act small { display:block; color:var(--secondary-text-color); font-size:13px; }
.err { padding:12px 14px; border-radius:10px; background:rgba(219,68,55,.12); color:var(--error-color, #db4437); font-size:14px; }
.toast { padding:12px 14px; border-radius:10px; background:rgba(67,160,71,.14); font-size:14px; }
a { color:var(--primary-color); }
.sr { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); }
`;

class ArHdlDeviceManager extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._entries = [];
    this._entryId = "";
    this._state = null;
    this._sel = "";
    this._filter = "all";
    this._query = "";
    this._error = "";
    this._toast = "";
    this._busy = "";
    this._editing = "";
    this._confirm = "";
    this._activity = null;
    this._lastRender = 0;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (this._menu) this._menu.hass = hass;
    if (first) {
      this._shell();
      this._loadEntries();
      return;
    }
    // Entity states change constantly; refresh the view at most once a
    // second, and never while someone is typing in it.
    const now = Date.now();
    if (now - this._lastRender > 1000 && !this._typing()) this._renderBody();
  }

  set narrow(n) {
    this._narrow = n;
    if (this._menu) this._menu.narrow = n;
  }

  disconnectedCallback() {
    clearTimeout(this._poll);
  }

  _typing() {
    const a = this.shadowRoot.activeElement;
    return !!a && a.tagName === "INPUT" && a.type !== "checkbox";
  }

  // ------------------------------------------------------------ data
  async _ws(type, extra = {}) {
    return this._hass.callWS({ type: `${WS}/${type}`, ...extra });
  }

  async _loadEntries() {
    try {
      const res = await this._ws("entries");
      this._entries = res.entries || [];
      if (!this._entries.find((e) => e.entry_id === this._entryId)) this._entryId = this._entries[0]?.entry_id || "";
      this._shell();
      if (this._entryId) await this._loadState();
      else this._renderBody();
    } catch (err) {
      this._error = err.message || String(err);
      this._renderBody();
    }
  }

  async _loadState(quiet = false) {
    clearTimeout(this._poll);
    if (!this._entryId) return;
    try {
      this._state = await this._ws("state", { entry_id: this._entryId });
      if (!quiet) this._error = "";
      const mods = this._state.modules;
      if (!mods.find((m) => m.address === this._sel)) this._sel = mods[0]?.address || "";
    } catch (err) {
      // An entry reloading after a change answers "unknown" for a moment.
      if (!quiet) this._error = err.message || String(err);
    }
    this._renderBody();
    if (this._state?.scan?.running) this._poll = setTimeout(() => this._loadState(true), 1000);
    if (this._activity === null) this._loadActivity();
  }

  _reloadSoon() {
    // Saving options reloads the entry; give it a moment, then refresh.
    setTimeout(() => this._loadState(true), 1500);
    setTimeout(() => this._loadState(true), 4000);
  }

  async _loadActivity() {
    // The HDL Bus Diagnostic tool's report for today, if it is installed.
    this._activity = [];
    const host = this._state?.host;
    if (!host || !this._hass.panels?.[DIAG_PANEL]) return;
    try {
      const prefix = host.replace(/[^A-Za-z0-9]+/g, "-");
      const rep = await this._hass.callApi("GET", `hdl_buspro_diagnostic/report?gateway=${encodeURIComponent(prefix)}&date=${isoDay(new Date())}`);
      this._activity = (rep.timeline || []).filter((r) => ["CHANGE", "STATE", "COMMAND", "MOTION"].includes(r.kind));
      this._renderBody();
    } catch (_err) {
      this._activity = [];
    }
  }

  // ------------------------------------------------------------ actions
  async _run(label, fn) {
    this._busy = label;
    this._error = "";
    this._toast = "";
    this._renderBody();
    try {
      await fn();
    } catch (err) {
      this._error = err.message || String(err);
    }
    this._busy = "";
    this._renderBody();
  }

  _scan() {
    return this._run("scan", async () => {
      await this._ws("scan", { entry_id: this._entryId, duration: 15 });
      await this._loadState();
    });
  }

  _add(addresses) {
    return this._run("add", async () => {
      const res = await this._ws("add", { entry_id: this._entryId, addresses });
      this._toast = res.added > 0
        ? `Added ${res.added} entit${res.added === 1 ? "y" : "ies"} to Home Assistant. The integration is reloading.`
        : "Nothing new to add: those channels are already in Home Assistant.";
      this._reloadSoon();
    });
  }

  _update(id, changes, message) {
    // Show the new value straight away; the entry takes a moment to reload.
    for (const m of this._state?.modules || []) {
      const e = m.entities.find((x) => x.id === id);
      if (e) Object.assign(e, changes);
    }
    return this._run("update", async () => {
      await this._ws("update", { entry_id: this._entryId, id, changes });
      this._toast = message;
      this._editing = "";
      this._reloadSoon();
    });
  }

  _remove(id) {
    return this._run("remove", async () => {
      await this._ws("remove", { entry_id: this._entryId, ids: [id] });
      this._confirm = "";
      this._toast = "Removed from Home Assistant. The integration is reloading.";
      this._reloadSoon();
    });
  }

  _service(domain, service, entity) {
    this._hass.callService(domain, service, { entity_id: entity }).catch((err) => {
      this._error = err.message || String(err);
      this._renderBody();
    });
  }

  _moreInfo(entityId) {
    this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true }));
  }

  // ------------------------------------------------------------ view helpers
  _st(id) {
    return this._hass.states[id];
  }

  _unavailable(m) {
    const ids = m.entities.flatMap((e) => e.entity_ids);
    return ids.length > 0 && ids.every((id) => !this._st(id) || this._st(id).state === "unavailable");
  }

  _badge(m) {
    if (m.in_ha && this._unavailable(m)) return ["Unavailable", "b-warn"];
    if (m.can_add && m.in_ha) return [`+${m.to_add} new`, "b-new"];
    if (m.can_add) return ["New", "b-new"];
    if (m.in_ha) return ["In HA", "b-ha"];
    if (m.no_entities) return [m.role_label || "No entities", "b-muted"];
    return ["Heard", "b-muted"];
  }

  _title(m) {
    if (m.name) return m.name;
    const named = m.entities.find((e) => e.name);
    if (named) return named.name.replace(/\s*(ch|channel)\s*\d+$/i, "") || named.name;
    return `HDL ${m.address}`;
  }

  _match(m) {
    const f = this._filter;
    if (f === "new" && !m.can_add) return false;
    if (f !== "all" && f !== "new" && m.group !== f) return false;
    const q = this._query.trim().toLowerCase();
    if (q && !`${m.address} ${this._title(m)} ${m.model} ${m.role_label}`.toLowerCase().includes(q)) return false;
    return true;
  }

  // ------------------------------------------------------------ render
  _shell() {
    const root = this.shadowRoot;
    const multi = this._entries.length > 1;
    root.innerHTML = `<style>${STYLE}</style>
      <div class="bar"><span id="menu"></span><h1>HDL Devices</h1>
        ${multi ? `<label class="sr" for="gw">Gateway</label><select id="gw">${this._entries.map((e) => `<option value="${esc(e.entry_id)}" ${e.entry_id === this._entryId ? "selected" : ""}>${esc(e.title || e.host)}</option>`).join("")}</select>` : ""}
      </div>
      <div class="wrap" id="body"></div>`;
    if (customElements.get("ha-menu-button")) {
      this._menu = document.createElement("ha-menu-button");
      this._menu.hass = this._hass;
      this._menu.narrow = this._narrow;
      root.getElementById("menu").appendChild(this._menu);
    }
    const gw = root.getElementById("gw");
    if (gw) gw.onchange = (e) => { this._entryId = e.target.value; this._state = null; this._sel = ""; this._activity = null; this._loadState(); };
    // The frame is rebuilt when the gateway list arrives; the shadow root
    // itself is not, so its listeners must only be added once.
    if (this._listening) return;
    this._listening = true;
    root.addEventListener("click", (e) => this._onClick(e));
    root.addEventListener("change", (e) => this._onChange(e));
    root.addEventListener("input", (e) => {
      if (e.target.id === "q") { this._query = e.target.value; this._renderList(); }
    });
    root.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && e.target.dataset?.enter) { e.preventDefault(); this._saveName(e.target.dataset.enter); }
      if (e.key === "Escape" && this._editing) { this._editing = ""; this._renderBody(); }
    });
  }

  _renderBody() {
    this._lastRender = Date.now();
    const body = this.shadowRoot.getElementById("body");
    if (!body) return;
    if (!this._entryId && !this._error) {
      body.innerHTML = `<div class="card empty">No AR HDL BUSPRO gateway is set up yet. Add one under Settings, Devices &amp; services.</div>`;
      return;
    }
    const s = this._state;
    if (!s) {
      body.innerHTML = this._error ? `<div class="err">${esc(this._error)}</div>` : `<div class="card empty">Loading…</div>`;
      return;
    }
    const mods = s.modules;
    const inHa = mods.filter((m) => m.in_ha).length;
    const addable = mods.filter((m) => m.can_add);
    const unavailable = mods.filter((m) => m.in_ha && this._unavailable(m)).length;
    const scan = s.scan;
    let scanText = scan.last ? `Last bus scan ${ago(scan.last)} · ${scan.found} device${scan.found === 1 ? "" : "s"} answered` : "The bus hasn't been scanned from this page yet";
    if (scan.running) {
      const left = Math.max(0, Math.round(scan.duration - (Date.now() - new Date(scan.started).getTime()) / 1000));
      scanText = left > 0 ? `Scanning the bus… ${left} s` : "Scanning the bus… confirming channels";
    }
    const searchFocused = this.shadowRoot.activeElement?.id === "q";
    body.innerHTML = `
      <div class="head">
        <div class="sub">
          <span><span class="dot" style="background:${s.available ? "var(--success-color, #2e7d32)" : "var(--error-color, #db4437)"}"></span>Gateway ${esc(s.host)} ${s.available ? "online" : "offline"}</span>
          <span aria-hidden="true">·</span><span>${esc(scanText)}</span>
        </div>
        <div class="btns">
          <button class="btn ghost" data-act="scan" ${scan.running || this._busy || !s.available ? "disabled" : ""}>${ICON.refresh}${scan.running ? "Scanning…" : "Scan bus"}</button>
          ${addable.length ? `<button class="btn" data-act="addall" ${this._busy || scan.running ? "disabled" : ""}>Add ${addable.length} new to Home Assistant</button>` : ""}
        </div>
      </div>
      ${this._error ? `<div class="err" role="alert">${esc(this._error)}</div>` : ""}
      ${scan.error ? `<div class="err" role="alert">${esc(scan.error)}</div>` : ""}
      ${this._toast ? `<div class="toast" role="status">${esc(this._toast)}</div>` : ""}
      <div class="stats">
        <div class="stat"><span>Known modules</span><b>${mods.length}</b></div>
        <div class="stat"><span>In Home Assistant</span><b>${inHa}</b></div>
        <div class="stat ${addable.length ? "warn" : ""}"><span>Found, not added</span><b>${addable.length}</b></div>
        <div class="stat ${unavailable ? "warn" : ""}"><span>Unavailable</span><b>${unavailable}</b></div>
      </div>
      <div class="cols">
        <section class="card list" aria-label="Devices">
          <div class="tools">
            <label class="searchbox">${ICON.search}<span class="sr">Search devices</span>
              <input id="q" type="search" placeholder="Search by name, address or model" value="${esc(this._query)}"></label>
            <div class="chips" role="group" aria-label="Filter">${FILTERS.map(([id, label]) => `<button class="chip ${this._filter === id ? "on" : ""}" aria-pressed="${this._filter === id}" data-act="filter" data-v="${id}">${label}</button>`).join("")}</div>
          </div>
          <ul id="list"></ul>
        </section>
        <section class="card detail" id="detail" aria-label="Device details"></section>
      </div>`;
    if (searchFocused) {
      const q = this.shadowRoot.getElementById("q");
      q.focus();
      q.setSelectionRange(q.value.length, q.value.length);
    }
    this._renderList();
    this._renderDetail();
  }

  _renderList() {
    const ul = this.shadowRoot.getElementById("list");
    if (!ul || !this._state) return;
    const rows = this._state.modules.filter((m) => this._match(m));
    if (!this._state.modules.length) {
      ul.innerHTML = `<li class="empty">No modules known yet. Press <b>Scan bus</b> to find what's on the HDL bus.</li>`;
      return;
    }
    if (!rows.length) {
      ul.innerHTML = `<li class="empty">No devices match.</li>`;
      return;
    }
    ul.innerHTML = rows.map((m) => {
      const [label, cls] = this._badge(m);
      const kind = [m.role_label, m.model].filter(Boolean).join(" · ");
      return `<li><button class="row ${m.address === this._sel ? "on" : ""}" aria-pressed="${m.address === this._sel}" data-act="sel" data-v="${esc(m.address)}">
        <span class="addr">${esc(m.address)}</span>
        <span class="rname"><b>${esc(this._title(m))}</b><small>${esc(kind || "Not identified yet")}</small></span>
        <span class="badge ${cls}">${esc(label)}</span></button></li>`;
    }).join("");
  }

  _renderDetail() {
    const box = this.shadowRoot.getElementById("detail");
    if (!box || !this._state) return;
    const m = this._state.modules.find((x) => x.address === this._sel);
    if (!m) {
      box.innerHTML = `<div class="muted">Pick a device on the left.</div>`;
      return;
    }
    const busy = !!this._busy;
    const head = `<div class="dh">
        <div><div class="mono">${esc(m.address)}${m.type_code ? ` · type ${esc(m.type_code)}` : ""}</div>
          <h2>${esc(this._title(m))}</h2>
          <div class="muted">${esc([m.model, m.role_label].filter(Boolean).join(" · ") || "Not identified yet")}</div></div>
        ${m.can_add ? `<button class="btn" data-act="add" data-v="${esc(m.address)}" ${busy ? "disabled" : ""}>${m.in_ha ? `Add ${m.to_add} missing` : "Add to Home Assistant"}</button>`
          : m.in_ha ? `<span class="badge b-ha">In Home Assistant</span>` : ""}
      </div>`;

    const notes = [];
    if (m.in_ha && this._unavailable(m)) notes.push(`<div class="note" role="note">${ICON.warn}<span>Its entities are unavailable: the gateway is offline, the licence isn't active, or this module has stopped answering.</span></div>`);
    if (m.no_entities) notes.push(`<div class="note info" role="note"><span>${m.role === "keypad" ? "Panels and keypads send commands but have no channels of their own. Panel features like temperature, AC or button LEDs are added under Configure, then Scan bus." : "Nothing on this device can be controlled from Home Assistant."}</span></div>`);
    if (!m.in_scan && !m.in_ha) notes.push(`<div class="note info" role="note"><span>This module answers status reads but wasn't in the last scan. Scan the bus to identify it before adding it.</span></div>`);

    const ents = m.entities.length
      ? `<div style="display:flex;flex-direction:column;gap:10px"><h3>In Home Assistant</h3>${m.entities.map((e) => this._entHtml(e)).join("")}</div>`
      : "";

    box.innerHTML = head + notes.join("") + ents + this._activityHtml(m);
  }

  _entHtml(e) {
    const id = e.entity_ids[0];
    const st = id ? this._st(id) : null;
    const state = st ? st.state : "not created";
    const busy = !!this._busy;
    const where = e.curtain_number ? `curtain ${e.curtain_number}` : e.channel ? `channel ${e.channel}` : e.sub_number ? `no. ${e.sub_number}` : "";
    let stateText = state;
    let controls = "";
    let extra = "";
    const unavail = !st || state === "unavailable";

    if (e.domain === "cover") {
      const pos = st?.attributes?.current_position;
      stateText = pos != null ? `${state} · ${pos}%` : state;
      controls = `<div class="btns">
        <button class="btn ghost small" data-act="svc" data-d="cover" data-s="open_cover" data-e="${esc(id)}" ${unavail ? "disabled" : ""}>Open</button>
        <button class="btn ghost small" data-act="svc" data-d="cover" data-s="stop_cover" data-e="${esc(id)}" ${unavail ? "disabled" : ""}>Stop</button>
        <button class="btn ghost small" data-act="svc" data-d="cover" data-s="close_cover" data-e="${esc(id)}" ${unavail ? "disabled" : ""}>Close</button></div>`;
      if (pos != null) extra += `<div class="bar-o" aria-hidden="true"><div class="bar-i" style="width:${Math.max(0, Math.min(100, pos))}%"></div></div>`;
      extra += `<div class="setting"><label for="rev-${esc(e.id)}"><span>Reverse direction</span><small>Swap open and close in Home Assistant. Wall panels still use the motor's own setting.</small></label>
          <input type="checkbox" id="rev-${esc(e.id)}" data-act="rev" data-id="${esc(e.id)}" ${e.reverse_direction ? "checked" : ""} ${busy ? "disabled" : ""}></div>
        <div class="setting"><label for="tt-${esc(e.id)}"><span>Full travel time (s)</span><small>Used to estimate the position</small></label>
          <input type="number" min="1" max="600" id="tt-${esc(e.id)}" data-act="travel" data-id="${esc(e.id)}" value="${esc(e.travel_time)}" ${busy ? "disabled" : ""}></div>`;
    } else if (e.domain === "light" || e.domain === "switch") {
      const bri = st?.attributes?.brightness;
      stateText = state === "on" && bri != null ? `on · ${Math.round((bri / 255) * 100)}%` : state;
      controls = `<div class="btns"><button class="btn ghost small" data-act="svc" data-d="${e.domain}" data-s="toggle" data-e="${esc(id)}" ${unavail ? "disabled" : ""}>${state === "on" ? "Turn off" : "Turn on"}</button></div>`;
    } else if (st) {
      const unit = st.attributes?.unit_of_measurement;
      stateText = unit ? `${state} ${unit}` : state;
      if (e.domain === "climate" && st.attributes?.current_temperature != null) stateText = `${state} · ${st.attributes.current_temperature} °C`;
    }

    const editing = this._editing === e.id;
    const nameBlock = editing
      ? `<div class="edit"><label class="sr" for="nm-${esc(e.id)}">Name</label><input id="nm-${esc(e.id)}" value="${esc(e.name)}" data-enter="${esc(e.id)}">
          <button class="btn small" data-act="savename" data-id="${esc(e.id)}">Save</button><button class="btn ghost small" data-act="cancel">Cancel</button></div>`
      : `<div class="ent-name"><b>${esc(e.name || e.type_label)}</b>
          <span class="muted" style="font-size:12px">${esc([e.type_label, where].filter(Boolean).join(" · "))}</span>
          ${e.entity_ids.map((x) => `<button class="link" data-act="more" data-e="${esc(x)}">${esc(x)}</button>`).join("")}</div>`;

    const confirming = this._confirm === e.id;
    const manage = editing ? "" : `<div class="btns">
        <button class="btn ghost small" data-act="rename" data-id="${esc(e.id)}" ${busy ? "disabled" : ""}>Rename</button>
        ${confirming
          ? `<button class="btn danger small" data-act="remove" data-id="${esc(e.id)}" ${busy ? "disabled" : ""}>Remove from HA</button><button class="btn ghost small" data-act="cancel">Keep it</button>`
          : `<button class="btn ghost small" data-act="askremove" data-id="${esc(e.id)}" ${busy ? "disabled" : ""}>Remove…</button>`}</div>`;

    return `<div class="ent">
      <div class="ent-top">${nameBlock}${editing ? "" : `<span class="state">${esc(stateText)}</span>`}</div>
      ${controls}${extra}${manage}</div>`;
  }

  _activityHtml(m) {
    const diag = !!this._hass.panels?.[DIAG_PANEL];
    if (!diag) return "";
    const rows = (this._activity || []).filter((r) => (r.device || "").startsWith(`${m.address} `) || (r.device || "") === m.address || (r.cause_src || "").startsWith(`${m.address} `));
    const last = rows.slice(-6).reverse();
    const list = last.length
      ? last.map((r) => {
          const what = r.kind === "COMMAND" ? `${r.what || ""}` : `${r.what || ""}${r.from || r.to ? `: ${r.from || "?"} → ${r.to || "?"}` : ""}`;
          const why = r.kind === "COMMAND" ? `sent to ${(r.device || "").split(" ")[0]}` : r.cause_src ? `by ${r.cause_src}` : "";
          return `<div class="act"><time>${esc((r.time || "").slice(11, 19))}</time><span><b>${esc(what)}</b><small>${esc(why)}</small></span></div>`;
        }).join("")
      : `<div class="muted">Nothing recorded for this device today.</div>`;
    return `<div style="display:flex;flex-direction:column;gap:6px">
      <div style="display:flex;justify-content:space-between;gap:12px;align-items:baseline"><h3>Today on the bus</h3><a href="/${DIAG_PANEL}">Open Bus Diagnostic</a></div>${list}</div>`;
  }

  // ------------------------------------------------------------ events
  _saveName(id) {
    const input = this.shadowRoot.getElementById(`nm-${id}`);
    const name = (input?.value || "").trim();
    if (!name) { this._error = "The name can't be empty."; this._renderBody(); return; }
    this._update(id, { name }, `Renamed to "${name}". The integration is reloading.`);
  }

  _onClick(ev) {
    const el = ev.target.closest("[data-act]");
    if (!el || el.disabled) return;
    const act = el.dataset.act;
    if (act === "rev" || act === "travel") return; // handled on change
    if (act === "sel") {
      this._sel = el.dataset.v;
      this._editing = "";
      this._confirm = "";
      this._renderList();
      this._renderDetail();
      if (this._narrow) this.shadowRoot.getElementById("detail")?.scrollIntoView({ behavior: "smooth", block: "start" });
    } else if (act === "filter") {
      this._filter = el.dataset.v;
      this._renderBody();
    } else if (act === "scan") this._scan();
    else if (act === "addall") this._add(this._state.modules.filter((m) => m.can_add).map((m) => m.address));
    else if (act === "add") this._add([el.dataset.v]);
    else if (act === "svc") this._service(el.dataset.d, el.dataset.s, el.dataset.e);
    else if (act === "more") this._moreInfo(el.dataset.e);
    else if (act === "rename") { this._editing = el.dataset.id; this._confirm = ""; this._renderDetail(); this.shadowRoot.getElementById(`nm-${el.dataset.id}`)?.focus(); }
    else if (act === "savename") this._saveName(el.dataset.id);
    else if (act === "cancel") { this._editing = ""; this._confirm = ""; this._renderDetail(); }
    else if (act === "askremove") { this._confirm = el.dataset.id; this._renderDetail(); }
    else if (act === "remove") this._remove(el.dataset.id);
  }

  _onChange(ev) {
    const el = ev.target;
    if (el.dataset?.act === "rev") {
      this._update(el.dataset.id, { reverse_direction: el.checked },
        el.checked ? "Reverse direction is on: open and close are swapped for this curtain." : "Reverse direction is off.");
    } else if (el.dataset?.act === "travel") {
      const v = parseInt(el.value, 10);
      if (!(v >= 1 && v <= 600)) { this._error = "Travel time must be 1 to 600 seconds."; this._renderBody(); return; }
      this._update(el.dataset.id, { travel_time: v }, `Travel time set to ${v} s.`);
    }
  }
}

if (!customElements.get("ar-hdl-device-manager")) customElements.define("ar-hdl-device-manager", ArHdlDeviceManager);
