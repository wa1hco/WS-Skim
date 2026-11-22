/* WS Skim roster page.
 * Look, filters, badges, sort, and Ctrl+/- column refit follow WS Hub's
 * Operate roster (https://github.com/wa1hco/WS-Hub, wims.js).
 *
 * Copyright (C) 2026 Jeff Millar, WA1HCO
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
"use strict";

function $(id) { return document.getElementById(id); }
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => (
    {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]
  ));
}
function escAttr(s) { return esc(s); }
function sgn(n) { return (n > 0 ? "+" : "") + n; }
function fmtBand(b) {
  if (!b) return "-";
  const s = String(b);
  if (/cm$/i.test(s)) return s;
  if (/m$/i.test(s)) return s.replace(/m$/i, "M");
  return s;
}
function age(sec) {
  if (sec == null || !isFinite(sec)) return "-";
  const s = Math.max(0, Math.round(sec));
  if (s < 60) return s + "s";
  if (s < 3600) return Math.floor(s / 60) + "m";
  return Math.floor(s / 3600) + "h";
}

const ROS_COLS = [
  {key:"call",        label:"DX",      cls:"",          locked:1, cell:rosCall},
  {key:"country",     label:"Country", cls:"country",
   hint:"DXCC entity for this call. Hover for the full name, and continent and CQ zone when known.",
   cell:rosCountry},
  {key:"to_call",     label:"Calling", cls:"",          cell:rosCalling},
  {key:"msg",         label:"Message", cls:"",
   hint:"Report, grid, 73, or other text after the callsigns. Hover a cell for the full decode.",
   cell:rosMessage},
  {key:"band",        label:"Band",    cls:"",          cell:c => fmtBand(c.band || "-")},
  {key:"mode",        label:"Mode",    cls:"",          cell:c => c.mode || "-"},
  {key:"grid",        label:"Grid",    cls:"",          cell:rosGrid},
  {key:"snr",         label:"dB",      cls:"num", num:1, cell:c => c.snr == null ? "-" : sgn(c.snr)},
  {key:"freq_hz",     label:"Freq",    cls:"num", num:1, cell:c => c.freq_hz ? (c.freq_hz / 1e6).toFixed(4) : "-"},
  {key:"az",          label:"Az DX",   cls:"num", num:1, cell:rosAzDx},
  {key:"distance_km", label:"km",      cls:"num", num:1, cell:c => c.distance_km == null ? "-" : c.distance_km},
  {key:"age",         label:"Age",     cls:"num", num:1, cell:c => age(c.age)},
];
const ROS_COLS_DEFAULT = ["call","country","to_call","msg","band","mode","grid","snr","age"];
const ROS_MSG_SHOW = 14;
const ROS_COLS_KEY = "wsskim.ops.cols";
const ROS_BANDS_KEY = "wsskim.ops.bands";
const ROS_MODES_KEY = "wsskim.ops.modes";
const ROS_AGE_KEY = "wsskim.ops.maxAgeSec";
const ROS_NEED_KEY = "wsskim.ops.needed";
const ROS_AGE_DEFAULT = 120;
const ROS_MODES_DEFAULT = ["FT8","FT4","MSK144"];

let _rosData = null;
let _rosSort = {key:"age", dir:1};
let _rosWired = false;
let _rosColsBuilt = false;
let _state = null;

function rosCall(c) {
  const labeled = String((c && c.need_label) || "").trim() !== "";
  const badges =
    (c.is_calling_us ? '<span class="badge callus">us</span>' : "") +
    rosChase(c);
  return `${esc(c.call)}${badges}`;
}
const CONTINENT_NAME = {
  NA:"North America", SA:"South America", EU:"Europe",
  AF:"Africa", AS:"Asia", OC:"Oceania", AN:"Antarctica",
};
function rosChase(c) {
  if (!c || (c.chase !== "unconfirmed" && c.chase !== "needed")) return "";
  const open = c.chase === "unconfirmed";
  const tip = c.need_reason || (open ? "Worked but not confirmed." : "Not in the log yet.");
  const word = open ? "unconfirmed" : (String(c.need_label || "").trim() || "needed");
  const cls = open ? "unconfirmed" : "needed";
  return '<span class="badge ' + cls + '" title="' + escAttr(tip) + '">' + esc(word) + "</span>";
}
function rosCountryTip(c) {
  const name = String(c.country || "").trim();
  if (!name) return "";
  const bits = [name];
  const raw = String(c.continent || "").trim();
  const cont = CONTINENT_NAME[raw.toUpperCase()] || raw;
  if (cont && cont.toUpperCase() !== name.toUpperCase()) bits.push(cont);
  if (c.cq_zone != null && c.cq_zone !== "") bits.push("CQ " + c.cq_zone);
  return bits.join(" · ");
}
function rosCountry(c) {
  const name = String(c.country || "").trim();
  if (!name) return "";
  return `<span class="ros-country" title="${escAttr(rosCountryTip(c))}">${esc(name)}</span>`;
}
function rosCalling(c) {
  if (c.is_cq) return '<span class="cq">CQ</span>';
  return esc(c.to_call || "-");
}
function rosGrid(c) {
  const tip = c.grid_tip || "";
  const title = tip ? ` title="${escAttr(tip)}"` : "";
  if (!c.grid) return "-";
  if (c.grid_remembered) {
    return `<span class="grid-remembered"${title}>${esc(c.grid)}</span>`;
  }
  return `<span${title}>${esc(c.grid)}</span>`;
}
function rosMessage(c) {
  const full = c.message || c.msg || "";
  const tail = String(c.msg || c.message_tail || "");
  const short = tail.length > ROS_MSG_SHOW ? tail.slice(0, ROS_MSG_SHOW - 1) + "…" : tail;
  return `<span title="${escAttr(full)}">${esc(short || "-")}</span>`;
}
function rosAzDx(c) {
  if (c.az == null) return "-";
  const src = c.bearing_source === "dxcc" ? " (entity centre)" : "";
  return `<span title="${escAttr((c.az + "°") + src)}">${c.az}°</span>`;
}

function rosLoadColPref() {
  try {
    const raw = localStorage.getItem(ROS_COLS_KEY);
    const pref = raw ? JSON.parse(raw) : null;
    if (Array.isArray(pref) && pref.length) return pref;
  } catch (_) {}
  return null;
}
function rosSaveColPref(keys) {
  try { localStorage.setItem(ROS_COLS_KEY, JSON.stringify([...keys])); } catch (_) {}
}
function rosVisibleCols() {
  const wrap = $("ros-cols");
  if (!wrap) return ROS_COLS;
  const boxes = [...wrap.querySelectorAll("input[type=checkbox]")];
  if (!boxes.length) {
    const want = new Set(rosLoadColPref() || ROS_COLS_DEFAULT);
    return ROS_COLS.filter(c => c.locked || want.has(c.key));
  }
  const on = new Set(boxes.filter(b => b.checked).map(b => b.value));
  return ROS_COLS.filter(c => c.locked || on.has(c.key));
}
function rosSyncColChecks() {
  const wrap = $("ros-cols");
  if (!wrap || _rosColsBuilt) return;
  _rosColsBuilt = true;
  const want = new Set(rosLoadColPref() || ROS_COLS_DEFAULT);
  wrap.innerHTML = ROS_COLS.map(c =>
    `<label><input type="checkbox" value="${c.key}" ${c.locked ? "checked disabled" : (want.has(c.key) ? "checked" : "")}> ${esc(c.label)}</label>`
  ).join("");
  wrap.addEventListener("change", () => {
    const keys = [...wrap.querySelectorAll("input[type=checkbox]")].filter(b => b.checked).map(b => b.value);
    rosSaveColPref(keys);
    rosDraw();
  });
}
function rosBuildHead() {
  const tr = $("ros-head");
  if (!tr) return;
  const vis = rosVisibleCols();
  tr.innerHTML = vis.map(c => {
    const arrow = _rosSort.key === c.key ? (_rosSort.dir < 0 ? " ▼" : " ▲") : "";
    const tip = (c.hint ? c.hint + " " : "") + "Click to sort.";
    return `<th data-key="${c.key}" class="${c.cls || ""}" title="${escAttr(tip)}">${esc(c.label)}${arrow}</th>`;
  }).join("");
}

function rosLoadNeed() {
  try { return rosNormalizeNeed(localStorage.getItem(ROS_NEED_KEY)); }
  catch (_) { return "needed"; }
}
function rosSaveNeed(mode) {
  try { localStorage.setItem(ROS_NEED_KEY, rosNormalizeNeed(mode)); } catch (_) {}
}
function rosNormalizeNeed(saved) {
  const t = saved == null ? "" : String(saved).toLowerCase();
  if (t === "all" || t === "needed" || t === "needed-cq" || t === "needed-73") return t;
  if (t === "true" || t === "1") return "needed";
  if (t === "false" || t === "0") return "all";
  return "needed";
}
function rosNeedButtons() { return [...($("ros-needed")?.querySelectorAll("[data-value]") || [])]; }
function rosSetNeed(mode, save) {
  const menu = $("ros-needed");
  const btn = $("ros-needed-btn");
  if (!menu) return;
  const m = rosNormalizeNeed(mode);
  let pick = rosNeedButtons().find(o => o.dataset.value === m)
    || rosNeedButtons().find(o => o.dataset.value === "needed");
  if (!pick) return;
  menu.dataset.value = pick.dataset.value;
  for (const o of rosNeedButtons()) o.setAttribute("aria-selected", o === pick ? "true" : "false");
  if (btn) { btn.textContent = pick.textContent; btn.title = pick.title || ""; }
  if (save) rosSaveNeed(menu.dataset.value);
}
function rosNeedMode() {
  const menu = $("ros-needed");
  return rosNormalizeNeed(menu ? (menu.dataset.value || "needed") : "needed");
}
function rosSyncNeedControl() {
  const menu = $("ros-needed");
  const btn = $("ros-needed-btn");
  if (!menu || !btn || menu.dataset.wired) return;
  menu.dataset.wired = "1";
  rosSetNeed(rosLoadNeed(), false);
  btn.addEventListener("click", () => { menu.hidden = !menu.hidden; btn.setAttribute("aria-expanded", String(!menu.hidden)); });
  for (const o of rosNeedButtons()) {
    o.addEventListener("click", () => {
      rosSetNeed(o.dataset.value, true);
      menu.hidden = true;
      btn.setAttribute("aria-expanded", "false");
      rosDraw();
    });
  }
  document.addEventListener("click", (e) => {
    if (!menu.hidden && $("ros-needed-wrap") && !$("ros-needed-wrap").contains(e.target)) {
      menu.hidden = true;
      btn.setAttribute("aria-expanded", "false");
    }
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { menu.hidden = true; btn.setAttribute("aria-expanded", "false"); } });
}

function rosLoadMaxAge() {
  try {
    const raw = localStorage.getItem(ROS_AGE_KEY);
    if (raw == null || raw === "") return ROS_AGE_DEFAULT;
    const n = Number(raw);
    return Number.isFinite(n) && n >= 0 ? n : ROS_AGE_DEFAULT;
  } catch (_) { return ROS_AGE_DEFAULT; }
}
function rosSaveMaxAge(sec) {
  try { localStorage.setItem(ROS_AGE_KEY, String(sec)); } catch (_) {}
}
function rosMaxAgeSec() {
  const sel = $("ros-max-age");
  if (!sel) return rosLoadMaxAge();
  const n = Number(sel.value);
  return Number.isFinite(n) && n >= 0 ? n : ROS_AGE_DEFAULT;
}
function rosSyncMaxAgeControl() {
  const sel = $("ros-max-age");
  if (!sel || sel.dataset.wired) return;
  sel.dataset.wired = "1";
  const want = rosLoadMaxAge();
  if ([...sel.options].some(o => o.value === String(want))) sel.value = String(want);
  sel.addEventListener("change", () => { rosSaveMaxAge(Number(sel.value)); rosDraw(); });
}

function rosLoadBandPref() {
  try {
    const raw = localStorage.getItem(ROS_BANDS_KEY);
    const pref = raw ? JSON.parse(raw) : null;
    if (Array.isArray(pref)) return new Set(pref);
  } catch (_) {}
  return null;
}
function rosSaveBandPref(bands) {
  try { localStorage.setItem(ROS_BANDS_KEY, JSON.stringify([...bands])); } catch (_) {}
}
function rosSelectedBands() {
  const wrap = $("ros-bands");
  if (!wrap) return null;
  const boxes = [...wrap.querySelectorAll("input[type=checkbox]")];
  if (!boxes.length) return null;
  return new Set(boxes.filter(b => b.checked).map(b => b.value));
}
function rosSyncBandChecks(bands) {
  const wrap = $("ros-bands");
  if (!wrap) return;
  const pref = rosLoadBandPref();
  const have = [...wrap.querySelectorAll("input")].map(b => b.value).join(",");
  if (have === bands.join(",") && wrap.dataset.wired) return;
  wrap.innerHTML = bands.map(b => {
    const on = !pref || pref.has(b);
    return `<label><input type="checkbox" value="${escAttr(b)}" ${on ? "checked" : ""}> ${esc(fmtBand(b))}</label>`;
  }).join("");
  wrap.dataset.wired = "1";
  wrap.onchange = () => {
    rosSaveBandPref(rosSelectedBands() || []);
    rosDraw();
  };
}

function rosSelectedModes() {
  const wrap = $("ros-modes");
  if (!wrap) return null;
  const boxes = [...wrap.querySelectorAll("input[type=checkbox]")];
  if (!boxes.length) return null;
  return new Set(boxes.filter(b => b.checked).map(b => b.getAttribute("data-mode")));
}
function rosSyncModeChecks() {
  const wrap = $("ros-modes");
  if (!wrap || wrap.dataset.wired) return;
  wrap.dataset.wired = "1";
  let pref = null;
  try {
    const raw = localStorage.getItem(ROS_MODES_KEY);
    if (raw) pref = new Set(JSON.parse(raw));
  } catch (_) {}
  for (const box of wrap.querySelectorAll("input[type=checkbox]")) {
    const mode = box.getAttribute("data-mode");
    if (pref) box.checked = pref.has(mode);
  }
  wrap.addEventListener("change", () => {
    try { localStorage.setItem(ROS_MODES_KEY, JSON.stringify([...rosSelectedModes()])); } catch (_) {}
    rosDraw();
  });
}

function rosTailIsFreeSignoff(tail) {
  return String(tail || "").split(/\s+/).some(t => t.toUpperCase() === "73" || t.toUpperCase() === "RR73");
}
function rosKeepCandidate(c, mode) {
  const m = rosNormalizeNeed(mode);
  if (m === "all") return true;
  if (!c.is_needed) return false;
  if (m === "needed") return true;
  if (m === "needed-cq") return !!c.is_cq;
  if (m === "needed-73") return !!c.is_cq || rosTailIsFreeSignoff(c.message_tail || c.msg);
  return true;
}
function rosPassesFilters(c, selectedBands, selectedModes, maxAge) {
  if (selectedModes && selectedModes.size) {
    if (c.mode && !selectedModes.has(c.mode)) return false;
  }
  if (selectedBands && selectedBands.size) {
    const hit = c.band && c.band !== "?" && selectedBands.has(c.band);
    const unknown = !c.band || c.band === "?";
    if (!hit && !unknown) return false;
  }
  if (maxAge > 0 && c.age != null && c.age > maxAge) return false;
  return true;
}

function rosWire() {
  if (_rosWired) return;
  _rosWired = true;
  $("ros-head").addEventListener("click", (e) => {
    const th = e.target.closest("th"); if (!th) return;
    const key = th.dataset.key;
    if (_rosSort.key === key) _rosSort.dir *= -1;
    else _rosSort = {key, dir: (ROS_COLS.find(c => c.key === key) || {}).num ? -1 : 1};
    rosDraw();
  });
  rosSyncNeedControl();
  rosSyncMaxAgeControl();
  rosSyncModeChecks();
  window.addEventListener("resize", rosFitColumnsSoon);
  if (window.visualViewport) window.visualViewport.addEventListener("resize", rosFitColumnsSoon);
  document.addEventListener("click", (e) => {
    for (const menu of document.querySelectorAll("details.ros-cols-menu")) {
      if (!menu.open || menu.contains(e.target)) continue;
      menu.open = false;
    }
  });
}

let _fitTimer = 0;
function rosFitColumnsSoon() {
  clearTimeout(_fitTimer);
  _fitTimer = setTimeout(rosFitColumns, 50);
}
function rosFitColumns() {
  const table = $("ros");
  if (!table) return;
  const old = table.querySelector("colgroup.ros-fit");
  if (old) old.remove();
  const first = table.querySelector("tr");
  if (!first) return;
  const cells = [...first.children];
  if (!cells.length) return;
  const widths = cells.map(td => Math.ceil(td.getBoundingClientRect().width));
  const cg = document.createElement("colgroup");
  cg.className = "ros-fit";
  for (const w of widths) {
    const col = document.createElement("col");
    col.style.width = w + "px";
    cg.appendChild(col);
  }
  table.insertBefore(cg, table.firstChild);
  table.style.tableLayout = "fixed";
}

function rosDraw() {
  const r = _rosData; if (!r) return;
  rosSyncMaxAgeControl();
  rosSyncNeedControl();
  rosSyncModeChecks();
  const bands = Array.isArray(r.live_bands) && r.live_bands.length
    ? r.live_bands.filter(Boolean)
    : [...new Set((r.candidates || []).map(c => c.band).filter(Boolean))].sort();
  rosSyncBandChecks(bands);
  const selected = rosSelectedBands();
  const modes = rosSelectedModes();
  const needMode = rosNeedMode();
  const maxAge = rosMaxAgeSec();
  let rows = (r.candidates || []).filter(c =>
    rosKeepCandidate(c, needMode) && rosPassesFilters(c, selected, modes, maxAge)
  );
  const vis = rosVisibleCols();
  const col = ROS_COLS.find(c => c.key === _rosSort.key) || ROS_COLS[0];
  const val = (c) => {
    const v = c[col.key];
    if (col.num) return v == null ? -Infinity : v;
    return (v || "").toString().toUpperCase();
  };
  rows.sort((a, b) => {
    const x = val(a), y = val(b);
    return (x < y ? -1 : x > y ? 1 : 0) * _rosSort.dir;
  });
  rosBuildHead();
  const bandNote = selected
    ? (selected.size === bands.length ? "all bands"
       : selected.size ? [...selected].sort().join(",") : "no bands")
    : "all bands";
  const unconf = Number(r.unconfirmed) || 0;
  const needNote = unconf ? `${r.needed} needed (${unconf} unconfirmed)` : `${r.needed} needed`;
  if ($("ros-meta")) {
    $("ros-meta").textContent =
      `${needNote} · ${r.not_needed} worked · ${r.count} heard · showing ${rows.length} · ${bandNote}`;
  }
  const body = $("ros-body"); body.innerHTML = "";
  const empty = $("ros-empty");
  if (empty) {
    if (rows.length) empty.style.display = "none";
    else {
      empty.style.display = "block";
      empty.textContent = (r.candidates || []).length ? "none match Show" : "no decodes yet…";
    }
  }
  const table = $("ros");
  const oldFit = table && table.querySelector("colgroup.ros-fit");
  if (oldFit) oldFit.remove();
  if (table) table.style.tableLayout = "auto";
  for (const c of rows) {
    const tr = document.createElement("tr");
    const classes = [];
    if (c.is_calling_us) classes.push("calling-us");
    else if (c.is_new_mult) classes.push("mult");
    if (c.chase === "unconfirmed") classes.push("unconfirmed");
    if (!c.is_needed) classes.push("dupe");
    tr.className = classes.join(" ");
    if (c.id) tr.dataset.row = c.id;
    if (c.need_reason) tr.title = c.need_reason;
    tr.innerHTML = vis.map(col => `<td class="${col.cls}">${col.cell(c)}</td>`).join("");
    body.appendChild(tr);
  }
  requestAnimationFrame(() => rosFitColumns());
}

function renderLog(s) {
  const el = $("log-line");
  if (!el) return;
  const log = (s && s.log) || {};
  const why = log.why || "No log found yet. Stations will look needed until a log is loaded.";
  const opts = (s.logs || []).map(l =>
    `<option value="${escAttr(l.path)}" ${l.path === log.path ? "selected" : ""}>${esc(l.title)}</option>`
  ).join("");
  el.innerHTML =
    `Log: ${esc(why)} ` +
    (opts ? `<select id="log-pick">${opts}</select> ` : "") +
    `<button type="button" id="log-choose">Choose log…</button>`;
  const pick = $("log-pick");
  if (pick) pick.onchange = () => postJSON("/api/log", {path: pick.value});
  const btn = $("log-choose");
  if (btn) btn.onclick = () => postJSON("/api/log", {action: "choose"});
}

function renderChecklist(s) {
  const el = $("checklist-line");
  if (!el) return;
  const ch = (s && s.checklist) || {};
  const items = ch.items || [];
  el.innerHTML = '<span class="band-label" title="' + escAttr(ch.summary || "") + '">worth working</span> ' +
    items.map(it => {
      const dis = it.available === false ? "disabled" : "";
      const on = it.enabled ? "checked" : "";
      return `<label title="${escAttr(it.tip || "")}"><input type="checkbox" data-kind="${escAttr(it.kind)}" data-scope="${escAttr(it.scope || "overall")}" ${on} ${dis}> ${esc(it.label)}</label>`;
    }).join(" ");
  el.onchange = () => {
    const body = {};
    for (const box of el.querySelectorAll("input[data-kind]")) {
      body[box.getAttribute("data-kind")] = {
        enabled: box.checked,
        scope: box.getAttribute("data-scope") || "overall",
      };
    }
    postJSON("/api/checklist", {items: body});
  };
}

function postJSON(url, body) {
  fetch(url, {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(body)})
    .then(r => r.json())
    .then(() => fetch("/api/state").then(r => r.json()).then(renderState))
    .catch(() => {});
}

function renderState(s) {
  _state = s;
  if (s && s.roster) {
    _rosData = s.roster;
    rosSyncColChecks();
    rosWire();
    rosDraw();
  }
  renderLog(s);
  renderChecklist(s);
  const clock = $("clock");
  if (clock && s && s.now) {
    const d = new Date(s.now * 1000);
    clock.textContent = d.toISOString().slice(11, 19) + " UTC";
  }
}

function connect() {
  rosSyncNeedControl();
  rosSyncMaxAgeControl();
  rosSyncModeChecks();
  const es = new EventSource("/events");
  let dropped = false;
  es.onopen = () => {
    // Back after WS Skim restarted (e.g. an upgrade): reload so this window
    // shows the new version's page instead of WS Skim opening a second one.
    if (dropped) { location.reload(); return; }
    $("conn").textContent = "● live"; $("conn").className = "up";
  };
  es.onmessage = (e) => {
    try { renderState(JSON.parse(e.data)); } catch (_) {}
  };
  es.onerror = () => { dropped = true; $("conn").textContent = "● disconnected"; $("conn").className = "down"; };
}
connect();
