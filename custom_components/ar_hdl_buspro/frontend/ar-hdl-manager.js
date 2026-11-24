/*
 * AR HDL Manager - sidebar page for keypad LED colours.
 * Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).
 * All rights reserved. Proprietary and confidential.
 */
const WS = "ar_hdl_buspro/manager/";
const SWATCHES = ["#ffffff", "#ff0000", "#ff8000", "#ffd000", "#00ff00", "#00ffff", "#0000ff", "#8000ff", "#ff00ff", "#000000"];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const validHex = (v) => /^#[0-9a-f]{6}$/i.test(v || "");

const CSS = `
:host{display:block;height:100%;background:var(--primary-background-color);color:var(--primary-text-color);font-family:var(--paper-font-body1_-_font-family,Roboto,sans-serif);--line:var(--divider-color,rgba(127,127,127,.3))}
*{box-sizing:border-box}
.bar{display:flex;align-items:center;gap:8px;height:56px;padding:0 12px;background:var(--app-header-background-color,var(--primary-color));color:var(--app-header-text-color,#fff);font-size:20px}
.bar select{margin-left:auto;font-size:14px;padding:6px;border-radius:6px;border:0}
.wrap{display:grid;grid-template-columns:280px minmax(0,1fr);gap:16px;padding:16px;max-width:1100px;margin:0 auto}
@media(max-width:800px){.wrap{grid-template-columns:minmax(0,1fr)}}
.card{background:var(--card-background-color,#fff);border-radius:12px;border:1px solid var(--line);padding:14px}
h3{margin:0 0 4px;font-size:16px;font-weight:500}
.sub{font-size:13px;color:var(--secondary-text-color)}
button{font:inherit;font-size:14px;padding:8px 12px;border-radius:8px;border:1px solid var(--line);background:transparent;color:var(--primary-text-color);cursor:pointer}
button:hover{background:rgba(127,127,127,.12)}
button.pri{background:var(--primary-color);border-color:var(--primary-color);color:var(--text-primary-color,#fff)}
button.danger{color:var(--error-color,#db4437)}
button:disabled{opacity:.5;cursor:default}
.kp{display:flex;gap:10px;align-items:center;padding:8px;border-radius:8px;cursor:pointer;border:1px solid transparent}
.kp:hover{background:rgba(127,127,127,.1)}
.kp.sel{border-color:var(--primary-color);background:rgba(127,127,127,.1)}
.mini{width:26px;height:26px;border-radius:5px;background:#17181b;display:grid;grid-template-columns:1fr 1fr;gap:2px;padding:4px;flex:none}
.mini i{background:#4a4d55;border-radius:1px}
.row{display:flex;gap:10px;align-items:center;padding:8px 2px;border-top:1px solid var(--line);font-size:14px}
.row .grow{flex:1;min-width:0}
.addr{font-family:monospace;font-size:13px;color:var(--secondary-text-color);min-width:52px}
.ok{color:var(--success-color,#43a047);font-size:13px;white-space:nowrap}
.cols{display:grid;grid-template-columns:auto minmax(0,1fr);gap:20px;align-items:start;margin-top:14px}
@media(max-width:640px){.cols{grid-template-columns:minmax(0,1fr)}}
.stage{background:rgba(127,127,127,.12);border-radius:12px;padding:18px;display:flex;justify-content:center}
.plate{width:230px;min-height:230px;border-radius:14px;background:#17181b;border:3px solid #2c2e33;padding:12px;display:grid;gap:8px}
.key{min-height:44px;border-radius:8px;background:#22242a;border:1px solid #33363d;cursor:pointer;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:7px;color:#d9dbe0;font-size:12px;position:relative;padding:14px 4px 8px;text-align:center}
.key.act{outline:2px solid #5aa2ff;outline-offset:2px}
.key.dirty::after{content:"";position:absolute;top:5px;right:6px;width:6px;height:6px;border-radius:50%;background:#ffb300}
.led{width:36px;height:5px;border-radius:3px;border:1px solid #3a3d44}
.tag{position:absolute;top:3px;left:6px;font-size:11px;color:#8d9099}
.field{display:flex;align-items:center;gap:10px;margin:10px 0 4px;flex-wrap:wrap}
.field label{width:80px;font-size:14px;color:var(--secondary-text-color)}
input[type=color]{width:48px;height:34px;padding:2px;border:1px solid var(--line);border-radius:6px;background:transparent}
input[type=text]{font:inherit;font-size:14px;padding:7px 8px;border-radius:6px;border:1px solid var(--line);background:transparent;color:inherit}
.sw{display:flex;gap:6px;flex-wrap:wrap;margin-left:90px}
.sw span{width:22px;height:22px;border-radius:50%;cursor:pointer;border:1px solid var(--line)}
.btns{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.msg{font-size:13px;margin-top:10px;min-height:18px}
.msg.err{color:var(--error-color,#db4437)}
.msg.good{color:var(--success-color,#43a047)}
.pages{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px;justify-content:center}
.pages button.on{border-color:var(--primary-color);color:var(--primary-color)}
.empty{padding:24px 8px;text-align:center;color:var(--secondary-text-color);font-size:14px}
`;

class ArHdlManagerPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.s = { hubs: [], entry: null, keypads: [], sel: null, btn: 1, page: 1, view: "main", scan: null, picks: new Set(),
      colors: {}, orig: {}, lit: {}, busy: "", msg: "", msgKind: "", loaded: false };
  }

  set hass(h) {
    this._hass = h;
    const mb = this.shadowRoot.querySelector("ha-menu-button");
    if (mb) mb.hass = h;
    if (!this.s.loaded) { this.s.loaded = true; this.loadHubs(); }
  }
  set narrow(v) { this._narrow = v; const mb = this.shadowRoot.querySelector("ha-menu-button"); if (mb) mb.narrow = v; }

  ws(cmd, data = {}) { return this._hass.callWS({ type: WS + cmd, ...data }); }
  say(msg, kind = "") { this.s.msg = msg; this.s.msgKind = kind; }
  get kp() { return this.s.keypads.find((k) => k.key === this.s.sel) || null; }

  async loadHubs() {
    try {
      const r = await this.ws("hubs");
      this.s.hubs = r.hubs;
      if (!this.s.entry && r.hubs.length) this.s.entry = r.hubs[0].entry_id;
      if (this.s.entry) await this.loadKeypads();
    } catch (e) { this.say(e.message || String(e), "err"); }
    this.draw();
  }

  async loadKeypads() {
    const r = await this.ws("keypads", { entry_id: this.s.entry });
    this.s.keypads = r.keypads;
    this.s.online = r.online;
    if (!this.kp) { this.s.sel = r.keypads.length ? r.keypads[0].key : null; if (this.s.sel) this.readColors(); }
  }

  async readColors() {
    const kp = this.kp;
    if (!kp) return;
    this.s.busy = "read"; this.say("Reading colours from the keypad…"); this.draw();
    try {
      const r = await this.ws("read", { entry_id: this.s.entry, key: kp.key });
      const map = {};
      if (r.keypad) Object.assign(kp, r.keypad);
      r.colors.forEach((c) => (map[c.button] = c));
      this.s.colors[kp.key] = map;
      this.s.orig[kp.key] = JSON.parse(JSON.stringify(map));
      const first = r.colors.length ? r.colors[0].button : 1;
      if (!map[this.s.btn]) { this.s.btn = first; this.s.page = Math.floor((first - 1) / 8) + 1; }
      this.say(r.colors.length ? "" : "The keypad answered but reported no button colours.", r.colors.length ? "" : "err");
    } catch (e) { this.say(e.message || String(e), "err"); }
    this.s.busy = ""; this.draw();
  }

  changed(key) {
    const cur = this.s.colors[key] || {}, orig = this.s.orig[key] || {};
    return Object.values(cur).filter((c) => !orig[c.button] || orig[c.button].on !== c.on || orig[c.button].off !== c.off);
  }

  async save() {
    const kp = this.kp; if (!kp) return;
    const list = this.changed(kp.key);
    if (!list.length) { this.say("Nothing has changed."); this.draw(); return; }
    this.s.busy = "save"; this.say(`Saving ${list.length} button${list.length === 1 ? "" : "s"} to the keypad…`); this.draw();
    try {
      const r = await this.ws("save", { entry_id: this.s.entry, key: kp.key, colors: list });
      this.s.orig[kp.key] = JSON.parse(JSON.stringify(this.s.colors[kp.key]));
      this.say(`Saved ${r.written} button${r.written === 1 ? "" : "s"} to ${kp.name}.`, "good");
    } catch (e) { this.say(e.message || String(e), "err"); }
    this.s.busy = ""; this.draw();
  }

  async scan() {
    this.s.view = "scan"; this.s.scan = null; this.s.picks = new Set(); this.s.busy = "scan"; this.say(""); this.draw();
    try {
      const r = await this.ws("scan", { entry_id: this.s.entry });
      this.s.scan = r;
    } catch (e) { this.s.scan = { found: [], devices: 0 }; this.say(e.message || String(e), "err"); }
    this.s.busy = ""; this.draw();
  }

  async addPicked() {
    if (!this.s.picks.size) { this.say("Tick at least one keypad first.", "err"); this.draw(); return; }
    try {
      const r = await this.ws("add", { entry_id: this.s.entry, keys: [...this.s.picks] });
      this.s.keypads = r.keypads;
      this.s.view = "main";
      this.say(`Added ${r.added} keypad${r.added === 1 ? "" : "s"} to the manager.`, "good");
      if (!this.kp && r.keypads.length) { this.s.sel = r.keypads[0].key; this.readColors(); return; }
    } catch (e) { this.say(e.message || String(e), "err"); }
    this.draw();
  }

  btnName(kp, b) { return (kp.button_names || {})[b] || `Button ${b}`; }

  // ----- rendering -----------------------------------------------------------
  draw() {
    const s = this.s, root = this.shadowRoot;
    const hubSel = s.hubs.length > 1
      ? `<select id="hub">${s.hubs.map((h) => `<option value="${esc(h.entry_id)}" ${h.entry_id === s.entry ? "selected" : ""}>${esc(h.title)}</option>`).join("")}</select>` : "";
    root.innerHTML = `<style>${CSS}</style>
      <div class="bar"><ha-menu-button></ha-menu-button><span>AR HDL Manager</span>${hubSel}</div>
      ${!s.hubs.length ? `<div class="empty">Add an AR HDL BUSPRO hub first.</div>` : `<div class="wrap">${this.listHtml()}${s.view === "scan" ? this.scanHtml() : this.mainHtml()}</div>`}`;
    const mb = root.querySelector("ha-menu-button");
    if (mb) { mb.hass = this._hass; mb.narrow = this._narrow; }
    this.wire();
  }

  mini(n) { return `<span class="mini">${"<i></i>".repeat(Math.min(n, 8))}</span>`; }

  listHtml() {
    const s = this.s;
    return `<div class="card"><h3>Keypads</h3>
      <div class="sub">${s.keypads.length} in the manager</div>
      <div style="margin:10px 0"><button class="pri" id="scan" ${s.busy === "scan" ? "disabled" : ""} style="width:100%">Scan bus for keypads</button></div>
      ${s.keypads.map((k) => `<div class="kp ${k.key === s.sel && s.view === "main" ? "sel" : ""}" data-kp="${esc(k.key)}">${this.mini(k.scheme === "dlp" ? 8 : k.buttons)}<span><div>${esc(k.name)}</div><div class="sub">${esc(k.address)} · ${k.buttons} buttons</div></span></div>`).join("")
        || `<div class="empty">No keypads yet. Scan the bus to find the ones with adjustable colours.</div>`}</div>`;
  }

  scanHtml() {
    const s = this.s;
    if (s.busy === "scan" || !s.scan) return `<div class="card"><h3>Scanning the bus for keypads…</h3><div class="sub">This takes about 20 seconds. Nothing is added to Home Assistant.</div><div class="msg ${s.msgKind}">${esc(s.msg)}</div></div>`;
    const can = s.scan.found.filter((k) => k.scheme), cannot = s.scan.found.filter((k) => !k.scheme);
    const fresh = can.filter((k) => !k.added);
    const line = (k, off) => `<label class="row"><input type="checkbox" data-pick="${esc(k.key)}" ${k.added ? "checked disabled" : off ? "disabled" : s.picks.has(k.key) ? "checked" : ""}>
      <span class="addr">${esc(k.address)}</span><span class="grow">${esc(k.name)}<div class="sub">${esc(k.model)} · ${esc(k.type_code)}${off ? "" : ` · ${k.buttons} buttons`}</div></span>${k.added ? `<span class="ok">✓ in manager</span>` : ""}</label>`;
    return `<div class="card"><h3>Found ${can.length} keypad${can.length === 1 ? "" : "s"} with adjustable colours</h3>
      <div class="sub">${s.scan.devices} devices answered on the bus. Only keypads are listed; relays, dimmers and sensors are left out.</div>
      ${fresh.length ? `<label class="row" style="border-top:0;margin-top:8px"><input type="checkbox" id="all" ${fresh.every((k) => s.picks.has(k.key)) ? "checked" : ""}><b style="font-weight:500">Select all</b></label>` : ""}
      ${can.map((k) => line(k, false)).join("") || `<div class="empty">No keypads with a colour setting answered.</div>`}
      ${cannot.length ? `<details style="margin-top:14px"><summary class="sub" style="cursor:pointer">${cannot.length} keypad${cannot.length === 1 ? "" : "s"} without a colour setting</summary>${cannot.map((k) => line(k, true)).join("")}</details>` : ""}
      <div class="btns" style="justify-content:flex-end"><button id="back">Cancel</button><button id="rescan">Scan again</button><button class="pri" id="add">Add to manager</button></div>
      <div class="msg ${s.msgKind}">${esc(s.msg)}</div></div>`;
  }

  mainHtml() {
    const s = this.s, kp = this.kp;
    if (!kp) return `<div class="card"><div class="empty">Press “Scan bus for keypads”, tick the keypads you want, and add them to the manager.</div><div class="msg ${s.msgKind}">${esc(s.msg)}</div></div>`;
    const colors = s.colors[kp.key] || {}, orig = s.orig[kp.key] || {};
    const dlp = kp.scheme === "dlp";
    const all = Object.keys(colors).map(Number).sort((a, b) => a - b);
    const shown = dlp ? all.filter((b) => Math.floor((b - 1) / 8) + 1 === s.page) : all;
    const pages = dlp ? [...new Set(all.map((b) => Math.floor((b - 1) / 8) + 1))] : [];
    const n = shown.length, cols = n <= 3 ? 1 : 2;
    const cur = colors[s.btn];
    const dirty = (b) => orig[b] && (orig[b].on !== colors[b].on || orig[b].off !== colors[b].off);
    const nChanged = this.changed(kp.key).length;
    const plate = n ? `<div class="plate" style="grid-template-columns:repeat(${cols},1fr)">${shown.map((b) => `<div class="key ${b === s.btn ? "act" : ""} ${dirty(b) ? "dirty" : ""}" data-b="${b}"><span class="tag">${b}</span>${esc(this.btnName(kp, b))}<span class="led" style="background:${esc(s.lit[kp.key + "/" + b] ? colors[b].on : colors[b].off)}"></span></div>`).join("")}</div>`
      : `<div class="plate" style="align-items:center;justify-items:center;color:#8d9099;font-size:13px;text-align:center">${s.busy === "read" ? "Reading…" : "No colours read yet"}</div>`;
    const sw = (w) => `<div class="sw">${SWATCHES.map((c) => `<span style="background:${c}" data-sw="${w}" data-c="${c}"></span>`).join("")}</div>`;
    const editor = cur ? `<h3>Button ${s.btn}</h3>
      <div class="field"><label>Name</label><input type="text" id="bname" maxlength="24" placeholder="Button ${s.btn}" value="${esc((kp.button_names || {})[s.btn] || "")}"></div>
      <div class="field"><label>On colour</label><input type="color" id="on" value="${esc(cur.on)}"><input type="text" id="onhex" size="8" maxlength="7" value="${esc(cur.on)}"></div>${sw("on")}
      <div class="field" style="margin-top:16px"><label>Off colour</label><input type="color" id="off" value="${esc(cur.off)}"><input type="text" id="offhex" size="8" maxlength="7" value="${esc(cur.off)}"></div>${sw("off")}
      <div class="btns"><button id="ledon">Show on, on the keypad</button><button id="ledoff">Show off, on the keypad</button></div>
      <div class="btns"><button id="copy">Copy to all buttons${dlp ? " on this page" : ""}</button></div>` : `<div class="sub">Read the colours from the keypad to start.</div>`;
    return `<div class="card">
      <div style="display:flex;gap:10px;align-items:flex-start;flex-wrap:wrap"><div class="grow" style="flex:1"><h3>${esc(kp.name)}</h3><div class="sub">${esc(kp.address)} · ${esc(kp.model)} · ${esc(kp.type_code)}</div></div>
        <button id="rename">Rename</button><button class="danger" id="remove">Remove</button></div>
      <div class="cols"><div><div class="stage">${plate}</div>
        ${pages.length > 1 ? `<div class="pages">${pages.map((p) => `<button data-page="${p}" class="${p === s.page ? "on" : ""}">Page ${p}</button>`).join("")}</div>` : ""}
        <div class="btns" style="justify-content:center"><button id="allon">Preview all on</button><button id="alloff">Preview all off</button></div></div>
        <div>${editor}</div></div>
      <div class="btns" style="border-top:1px solid var(--line);padding-top:12px;margin-top:16px">
        <button class="pri" id="save" ${s.busy || !nChanged ? "disabled" : ""}>Save to keypad${nChanged ? ` (${nChanged})` : ""}</button>
        <button id="read" ${s.busy ? "disabled" : ""}>Read from keypad</button>
        <button id="undo" ${nChanged ? "" : "disabled"}>Undo changes</button></div>
      <div class="msg ${s.msgKind}">${esc(s.msg)}</div></div>`;
  }

  setColor(which, value, redraw = true) {
    const kp = this.kp; if (!kp || !validHex(value)) return;
    const c = (this.s.colors[kp.key] || {})[this.s.btn]; if (!c) return;
    c[which] = value.toLowerCase();
    this.s.lit[kp.key + "/" + this.s.btn] = which === "on";
    if (redraw) this.draw();
  }

  wire() {
    const s = this.s, root = this.shadowRoot, $ = (id) => root.getElementById(id), on = (id, ev, fn) => { const el = $(id); if (el) el.addEventListener(ev, fn); };
    on("hub", "change", async (e) => { s.entry = e.target.value; s.sel = null; s.view = "main"; this.say(""); await this.loadKeypads().catch((err) => this.say(err.message, "err")); this.draw(); });
    on("scan", "click", () => this.scan());
    on("rescan", "click", () => this.scan());
    on("back", "click", () => { s.view = "main"; this.say(""); this.draw(); });
    on("add", "click", () => this.addPicked());
    on("all", "change", (e) => { s.scan.found.filter((k) => k.scheme && !k.added).forEach((k) => (e.target.checked ? s.picks.add(k.key) : s.picks.delete(k.key))); this.say(""); this.draw(); });
    root.querySelectorAll("[data-pick]").forEach((el) => el.addEventListener("change", () => { el.checked ? s.picks.add(el.dataset.pick) : s.picks.delete(el.dataset.pick); this.say(""); this.draw(); }));
    root.querySelectorAll("[data-kp]").forEach((el) => el.addEventListener("click", () => {
      const same = s.sel === el.dataset.kp && s.view === "main";
      s.sel = el.dataset.kp; s.view = "main"; s.btn = 1; s.page = 1; this.say("");
      if (!same && !s.colors[s.sel]) this.readColors(); else this.draw();
    }));
    root.querySelectorAll("[data-b]").forEach((el) => el.addEventListener("click", () => { s.btn = Number(el.dataset.b); this.draw(); }));
    root.querySelectorAll("[data-page]").forEach((el) => el.addEventListener("click", () => { s.page = Number(el.dataset.page); s.btn = (s.page - 1) * 8 + 1; this.draw(); }));
    root.querySelectorAll("[data-sw]").forEach((el) => el.addEventListener("click", () => this.setColor(el.dataset.sw, el.dataset.c)));
    ["on", "off"].forEach((w) => {
      on(w, "change", (e) => this.setColor(w, e.target.value));
      on(w + "hex", "change", (e) => { const v = e.target.value.trim(); if (validHex(v)) this.setColor(w, v); else { this.say("Enter a colour like #ff8000.", "err"); this.draw(); } });
    });
    const kp = this.kp;
    if (!kp) return;
    const led = async (state) => {
      s.lit[kp.key + "/" + s.btn] = state;
      try { await this.ws("led", { entry_id: s.entry, key: kp.key, button: s.btn, on: state }); this.say(`Button ${s.btn} is showing its ${state ? "on" : "off"} state on the keypad. Saved colours only; nothing was switched.`); }
      catch (e) { this.say(e.message || String(e), "err"); }
      this.draw();
    };
    on("ledon", "click", () => led(true));
    on("ledoff", "click", () => led(false));
    on("copy", "click", () => {
      const colors = s.colors[kp.key], src = colors[s.btn];
      Object.values(colors).forEach((c) => { if (kp.scheme !== "dlp" || Math.floor((c.button - 1) / 8) + 1 === s.page) { c.on = src.on; c.off = src.off; } });
      this.say("Copied. Press “Save to keypad” to write it."); this.draw();
    });
    const allLit = (state) => { Object.keys(s.colors[kp.key] || {}).forEach((b) => (s.lit[kp.key + "/" + b] = state)); this.draw(); };
    on("allon", "click", () => allLit(true));
    on("alloff", "click", () => allLit(false));
    on("save", "click", () => this.save());
    on("read", "click", () => this.readColors());
    on("undo", "click", () => { s.colors[kp.key] = JSON.parse(JSON.stringify(s.orig[kp.key] || {})); this.say("Changes undone."); this.draw(); });
    on("bname", "change", async (e) => {
      try { const r = await this.ws("rename", { entry_id: s.entry, key: kp.key, button_names: { [String(s.btn)]: e.target.value } }); Object.assign(kp, r.keypad); }
      catch (err) { this.say(err.message || String(err), "err"); }
      this.draw();
    });
    on("rename", "click", async () => {
      const name = window.prompt("Name for this keypad (kept in Home Assistant only)", kp.name);
      if (!name || !name.trim()) return;
      try { const r = await this.ws("rename", { entry_id: s.entry, key: kp.key, name: name.trim() }); Object.assign(kp, r.keypad); }
      catch (err) { this.say(err.message || String(err), "err"); }
      this.draw();
    });
    on("remove", "click", async () => {
      if (!window.confirm(`Remove ${kp.name} from the manager? The keypad itself is not changed.`)) return;
      try { const r = await this.ws("remove", { entry_id: s.entry, key: kp.key }); s.keypads = r.keypads; s.sel = r.keypads.length ? r.keypads[0].key : null; this.say("Removed from the manager."); if (s.sel && !s.colors[s.sel]) { this.readColors(); return; } }
      catch (err) { this.say(err.message || String(err), "err"); }
      this.draw();
    });
  }
}

if (!customElements.get("ar-hdl-manager-panel")) customElements.define("ar-hdl-manager-panel", ArHdlManagerPanel);
