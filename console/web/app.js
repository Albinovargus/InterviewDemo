"use strict";
// Blast Radius console. One state object; every panel renders from it, so panels cannot disagree.

const BEATS = [
  { n: 1, title: "The stack", lede: "A real Kubernetes estate: an 18-service shop, full telemetry, and faults we can switch on.", view: "arch" },
  { n: 2, title: "The map", lede: "Code, not AI, builds the dependency map from traces, eBPF network flows and the Kubernetes inventory.", view: "map" },
  { n: 3, title: "The unknown caller", lede: "A pod with no owner label and no traces is talking to the database. The map flags it instead of hiding it.", view: "map" },
  { n: 4, title: "Break something", lede: "Pick a fault. Alerts flood in from the symptoms, not only from the cause.", view: "map" },
  { n: 5, title: "The diagnosis", lede: "Claude investigates with read-only tools. Code shrinks the data; the model decides what to ask next.", view: "map" },
  { n: 6, title: "The guardrail", lede: "The agent proposes a fix. It cannot apply one: a person approves it through normal change control.", view: "doc" },
  { n: 7, title: "The honest answer", lede: "Two faults at once, or none at all. A trustworthy agent hedges, or says the estate is healthy.", view: "doc" },
  { n: 8, title: "The postmortem", lede: "The same evidence, drafted into a timeline, root cause and action items for people to edit.", view: "doc" },
  { n: 9, title: "The scorecard", lede: "Measured, not claimed: rehearsal runs per fault, with pass rate, median time and cost.", view: "doc" },
];

// Grafana Explore: live requests per second by service, from the span metrics Tempo derives from traces.
const GRAFANA_TRAFFIC = "http://localhost:3000/explore?orgId=1&schemaVersion=1&panes=" + encodeURIComponent(JSON.stringify({
  a: { datasource: "prometheus", range: { from: "now-30m", to: "now" }, queries: [{ refId: "A", datasource: { type: "prometheus", uid: "prometheus" },
    expr: 'sum by (service) (rate(traces_spanmetrics_calls_total{span_kind="SPAN_KIND_SERVER"}[1m]))', legendFormat: "{{service}}" }] },
}));

const S = {
  beat: 2, mode: "live", map: null, alerts: { alerts: [], services: [] }, health: null,
  faults: [], job: null, replays: [], scorecard: null,
  run: null,           // {id, kind: live|replay, meta, steps: Map, order: [], counter, verdict, error, cost, start, end, tools, finished, prefetch}
  es: null, assembled: false, selectedEdge: null, focus: null, speed: 2,
};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n) => (n ?? 0).toLocaleString("en-US");
const clock = (ts) => ts ? new Date(ts * 1000).toISOString().slice(11, 19) + " UTC" : "–";
const mmss = (s) => s == null ? "–" : `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const arr = (x) => (Array.isArray(x) ? x : x == null ? [] : [x]);
const pct = (x) => x == null ? "–" : `${(x * 100).toFixed(x < 0.1 ? 1 : 0)}%`;

async function api(path, opts = {}) {
  const r = await fetch(path, { cache: "no-store", ...opts, headers: { "Content-Type": "application/json", ...(opts.headers || {}) } });
  let body = null;
  try { body = await r.json(); } catch { /* empty body */ }
  if (!r.ok) throw new Error((body && body.error) || `${path} returned ${r.status}`);
  return body;
}

function toast(msg, err = false) {
  const t = $("toast");
  t.textContent = msg; t.className = "toast" + (err ? " err" : ""); t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), err ? 7000 : 3500);
}

// ---------------------------------------------------------------- polling (live data)

async function pollAlerts() {
  if (S.mode === "replay" && S.run?.prefetch?.alerts) return;
  try { S.alerts = await api("/api/alerts"); renderAlerts(); renderMapState(); }
  catch (e) { $("alerts-sub").textContent = "alerts unavailable"; }
}
async function pollMap(force = false) {
  if (S.mode === "replay" && S.run?.prefetch?.map_view && !force) return;
  try {
    const m = await api("/api/map");
    const first = !S.map;
    S.map = m;
    if (first || topologyChanged(m)) layoutMap(); else renderMapState();
  } catch (e) { if (!S.map) $("map-callout").innerHTML = `<div class="callout info"><h3>Map unavailable</h3>${esc(e.message)}</div>`; }
}
async function pollHealth() {
  try {
    S.health = await api("/api/health");
    const b = S.health.backends, ok = Object.values(b).every(Boolean);
    $("backend-dot").className = "dot " + (ok ? "ok" : "firing");
    $("stat-backends").textContent = ok ? "backends up" : "down: " + Object.keys(b).filter((k) => !b[k]).join(", ");
  } catch { $("backend-dot").className = "dot firing"; $("stat-backends").textContent = "console offline"; }
}
async function pollFaults() {
  try {
    const f = await api("/api/faults");
    S.faults = f.faults; const prev = S.job?.state; S.job = f.job;
    if (prev === "running" && f.job.state !== "running") {
      if (f.job.state === "error") toast(f.job.message || "Fault action failed", true);
      else toast(f.job.action === "reset" ? "Lab reset: no faults remain." : `Injected ${f.job.fault}. Alerts usually fire within 1–3 minutes.`);
    }
    renderFaultBar();
  } catch { /* console offline: health shows it */ }
}

// ---------------------------------------------------------------- beats + views

function renderBeats() {
  $("beats").innerHTML = BEATS.map((b) =>
    `<button class="beat" data-n="${b.n}" aria-current="${b.n === S.beat}"><span class="n">${b.n}</span>${esc(b.title)}</button>`).join("");
  $("beats").querySelectorAll(".beat").forEach((el) => (el.onclick = () => setBeat(+el.dataset.n)));
}

function setBeat(n) {
  const prev = S.beat; S.beat = n;
  const b = BEATS[n - 1];
  renderBeats();
  $("beat-title").textContent = `${b.n}. ${b.title}`;
  $("beat-lede").textContent = b.lede;
  $("view-map").hidden = b.view !== "map";
  $("view-arch").hidden = b.view !== "arch";
  $("view-doc").hidden = b.view !== "doc";
  $("fault-bar").hidden = n !== 4;
  $("counter").hidden = n !== 5;
  S.focus = n === 3 ? "unknown" : null;
  S.selectedEdge = null;
  renderStageTools();
  if (b.view === "arch") renderArch();
  if (b.view === "map") {
    if (n === 2 && prev !== 2) assemble(); else { layoutMap(); }
    renderCallout();
  }
  if (b.view === "doc") renderDoc();
  if (n === 5 || n === 6 || n === 8) ensureRunForBeat();
}

function renderStageTools() {
  const running = !!(S.run && !S.run.finished);
  const key = JSON.stringify([S.beat, S.mode, running, S.run?.kind, S.starting, S.replays.length, S.speed]);
  const t = $("stage-tools");
  if (t.dataset.key === key) return;
  t.dataset.key = key; t.innerHTML = "";
  const add = (label, cls, fn, disabled = false, title = "") => {
    const el = document.createElement("button");
    el.className = "btn " + cls; el.textContent = label; el.disabled = disabled; if (title) el.title = title;
    el.onclick = fn; t.appendChild(el); return el;
  };
  if (S.beat === 1) {
    add("Open Grafana", "", () => window.open(GRAFANA_TRAFFIC, "_blank"));
    add("Open the shop", "", () => window.open("http://localhost:8080/", "_blank"));
  }
  if (S.beat === 2) add("Replay assembly", "", assemble);
  if (S.beat === 5) {
    if (S.mode === "live") {
      add(S.starting ? "Starting…" : running && S.run.kind === "live" ? "Investigating…" : "Investigate", "primary", startLive,
          S.starting || (running && S.run.kind === "live"));
      if (running && S.run.kind === "live") add("Stop", "", stopLive);
    } else {
      const reps = replaysFor(5);
      if (!reps.length) { const s = document.createElement("span"); s.className = "status-line"; s.textContent = "No replays recorded yet."; t.appendChild(s); }
      reps.forEach((r) => add(r.title || r.id, "", () => playReplay(r.id)));
      speedControl(t);
    }
  }
  if (S.beat === 7) speedControl(t);
}

function speedControl(t) {
  const seg = document.createElement("div"); seg.className = "seg";
  [1, 2, 4].forEach((x) => {
    const b = document.createElement("button"); b.textContent = `${x}×`; b.setAttribute("aria-pressed", String(S.speed === x));
    b.onclick = () => { S.speed = x; renderStageTools(); }; seg.appendChild(b);
  });
  t.appendChild(seg);
}

function setMode(m) {
  if (S.mode === m) return;
  if (S.run && !S.run.finished && S.run.kind === "live" && m === "replay") {
    toast("The live investigation keeps running; switch back to Live to watch it.");
  }
  S.mode = m;
  $("mode-live").setAttribute("aria-pressed", String(m === "live"));
  $("mode-replay").setAttribute("aria-pressed", String(m === "replay"));
  closeStream(); S.run = null; S.diagnosis = null; renderRun(); pollAlerts(); pollMap(true);
  if (m === "live") reattachLive();
  setBeat(S.beat);
}

// ---------------------------------------------------------------- architecture (beat 1)

function renderArch() {
  const L = [
    ["Platform", ["kind (Kubernetes in Docker)", "Terraform · 3 stages", "Helm charts"]],
    ["The shop", ["OpenTelemetry Demo · 18 services", "load generator · 25 shoppers", "PostgreSQL · Valkey · Kafka"]],
    ["Telemetry", ["OpenTelemetry SDKs", "OTel Collector", "OBI eBPF · no code changes", "kubelet stats"]],
    ["Storage + UI", ["Prometheus · metrics", "Loki · logs", "Tempo · traces + service graph", "Grafana · alerts"]],
    ["Faults", ["flagd feature flags", "Chaos Mesh · network + DNS", "git change log · bad canary"]],
    ["AI", ["Claude · headless Claude Code", "MCP: reduction tools (6, read-only)", "MCP: Grafana (Viewer role)", "no shell · no file · no write tools"]],
  ];
  $("arch").innerHTML = L.map(([name, boxes], i) =>
    `<div class="layer" style="animation-delay:${i * 90}ms"><div class="name">${esc(name)}</div><div class="boxes">${
      boxes.map((b) => `<div class="box ${name === "AI" && b.startsWith("Claude") ? "claude" : ""} ${b.startsWith("no ") ? "hidden-f" : ""}">${esc(b)}</div>`).join("")}</div></div>`).join("") +
    `<div class="flow-note">telemetry → storage → code reduces it → Claude reads a few thousand tokens → a person decides</div>`;
}

// ---------------------------------------------------------------- map

const POS = {}; // node -> {x, y} in px within #map
let topoKey = "";

function mapData() {
  if (S.mode === "replay" && S.run?.prefetch?.map_view) return S.run.prefetch.map_view;
  return S.map;
}
function alertData() {
  if (S.mode === "replay" && S.run?.prefetch?.alerts) return S.run.prefetch.alerts;
  return S.alerts;
}
function topologyChanged(m) {
  const k = m.edges.map((e) => e.caller + ">" + e.callee).sort().join("|");
  return k !== topoKey;
}

function layoutMap() {
  const m = mapData(); if (!m) return;
  topoKey = m.edges.map((e) => e.caller + ">" + e.callee).sort().join("|");
  const names = new Set();
  m.edges.forEach((e) => { names.add(e.caller); names.add(e.callee); });
  const out = {}, inc = {};
  names.forEach((n) => { out[n] = []; inc[n] = []; });
  m.edges.forEach((e) => { out[e.caller].push(e.callee); inc[e.callee].push(e.caller); });
  // depth = longest path from a root (bounded, so a cycle cannot loop forever)
  const depth = {}; names.forEach((n) => (depth[n] = 0));
  for (let i = 0; i < names.size; i++) {
    let moved = false;
    m.edges.forEach((e) => { if (depth[e.callee] < depth[e.caller] + 1 && depth[e.caller] + 1 < names.size) { depth[e.callee] = depth[e.caller] + 1; moved = true; } });
    if (!moved) break;
  }
  // Pull pure callers (kafka consumers, the unknown pod) next to what they call, instead of the far left.
  names.forEach((n) => { if (!inc[n].length && out[n].length) depth[n] = Math.max(0, Math.min(...out[n].map((c) => depth[c])) - 1); });
  const cols = [];
  names.forEach((n) => { (cols[depth[n]] ||= []).push(n); });
  const order = {};
  cols.forEach((col, d) => {
    if (d === 0) col.sort((a, b) => (m.nodes[a]?.labelled === false) - (m.nodes[b]?.labelled === false) || a.localeCompare(b));
    else col.sort((a, b) => bary(a) - bary(b) || a.localeCompare(b));
    col.forEach((n, i) => (order[n] = i));
  });
  function bary(n) { const p = inc[n]; return p.length ? p.reduce((s, x) => s + (order[x] ?? 0), 0) / p.length : 0; }
  const box = $("map").getBoundingClientRect();
  const W = box.width, H = box.height, padX = 90, padTop = 54, padBot = 30;
  const D = Math.max(cols.length - 1, 1);
  lastSize = W + "x" + H;
  cols.forEach((col, d) => col.forEach((n, i) => {
    POS[n] = { x: padX + (W - 2 * padX) * (cols.length === 1 ? 0.5 : d / D),
               y: padTop + (H - padTop - padBot) * ((i + 0.5) / col.length) };
  }));
  renderNodes(m);
  renderMapState();
}

function renderNodes(m) {
  const wrap = $("map-nodes");
  wrap.innerHTML = "";
  Object.entries(POS).forEach(([n, p]) => {
    if (!m.edges.some((e) => e.caller === n || e.callee === n)) return;
    const el = document.createElement("div");
    el.className = "node"; el.dataset.n = n; el.textContent = n;
    el.style.left = p.x + "px"; el.style.top = p.y + "px";
    el.onclick = () => { S.selectedEdge = null; S.focusNode = S.focusNode === n ? null : n; renderMapState(); renderCallout(); };
    wrap.appendChild(el);
  });
  // measure for edge anchors
  wrap.querySelectorAll(".node").forEach((el) => { POS[el.dataset.n].w = el.offsetWidth; });
}

function edgeClass(e, alerting, cause) {
  if (e.silent) return "silent";
  const failing = e.now && e.now.error_rate >= 0.05 && (!e.baseline || e.now.error_rate - (e.baseline.error_rate || 0) >= 0.05);
  if (cause && e.callee === cause && (alerting.has(e.caller) || failing)) return "cause-path";
  if (failing) return "failing";
  return e.grade;
}

function renderMapState() {
  const m = mapData(); if (!m) return;
  const svg = $("map-svg");
  const alerting = new Set(alertData()?.services || []);
  const cause = S.run?.verdict?.status === "diagnosed" ? S.run.verdict.primary_cause?.service : null;
  const unknown = new Set(Object.entries(m.nodes || {}).filter(([, v]) => v.labelled === false && v.kind !== "virtual").map(([k]) => k));
  const focusSet = S.focus === "unknown"
    ? new Set(m.edges.filter((e) => unknown.has(e.caller)).flatMap((e) => [e.caller, e.callee]))
    : S.focusNode ? new Set(m.edges.filter((e) => e.caller === S.focusNode || e.callee === S.focusNode).flatMap((e) => [e.caller, e.callee])) : null;
  const colors = { blue: "#5AB0FF", amber: "#F5A524", coral: "#FF6B5B", grey: "#4A5567" };
  let html = `<defs>${Object.entries(colors).map(([k, c]) =>
    `<marker id="ar-${k}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,1 L9,5 L0,9 z" fill="${c}"/></marker>`).join("")}</defs>`;
  m.edges.forEach((e, i) => {
    const a = POS[e.caller], b = POS[e.callee]; if (!a || !b) return;
    const x1 = a.x + (a.w || 80) / 2, x2 = b.x - (b.w || 80) / 2 - 4;
    const back = x2 < x1 + 10;
    const d = back
      ? `M${a.x},${a.y + 12} C${a.x},${a.y + 60} ${b.x},${b.y + 60} ${b.x},${b.y + 14}`
      : `M${x1},${a.y} C${(x1 + x2) / 2},${a.y} ${(x1 + x2) / 2},${b.y} ${x2},${b.y}`;
    const cls = edgeClass(e, alerting, cause);
    const col = cls === "silent" ? "grey" : cls === "failing" ? "coral" : cls === "unexplained" || cls === "cause-path" ? "amber" : "blue";
    const dim = focusSet && !(focusSet.has(e.caller) && focusSet.has(e.callee));
    const dash = cls === "likely" ? ' stroke-dasharray="6 5"' : cls === "unexplained" ? ' stroke-dasharray="2 4"' : cls === "silent" ? ' stroke-dasharray="5 5"' : "";
    const sel = S.selectedEdge === i ? " selected" : "";
    html += `<path class="edge ${cls}${sel}" d="${d}"${dash} marker-end="url(#ar-${col})" style="opacity:${dim ? 0.12 : ""}" data-i="${i}"/>`;
    html += `<path class="edge-hit" d="${d}" data-i="${i}"><title>${esc(e.sentence)}</title></path>`;
  });
  svg.innerHTML = html;
  svg.querySelectorAll(".edge-hit").forEach((p) => (p.onclick = () => { S.selectedEdge = +p.dataset.i; S.focusNode = null; renderMapState(); renderCallout(); }));
  $("map-nodes").querySelectorAll(".node").forEach((el) => {
    const n = el.dataset.n;
    el.classList.toggle("unknown", unknown.has(n));
    el.classList.toggle("alerting", alerting.has(n) && n !== cause);
    el.classList.toggle("cause", n === cause);
    el.classList.toggle("dim", !!focusSet && !focusSet.has(n));
    el.classList.toggle("focus", S.focusNode === n);
  });
}

function assemble() {
  const m = mapData(); if (!m) { pollMap(); return; }
  layoutMap();
  const nodes = [...$("map-nodes").querySelectorAll(".node")].sort((a, b) => POS[a.dataset.n].x - POS[b.dataset.n].x);
  nodes.forEach((el, i) => { el.style.animationDelay = `${120 + i * 55}ms`; el.classList.remove("enter"); void el.offsetWidth; el.classList.add("enter"); });
  $("map-svg").querySelectorAll(".edge").forEach((p, i) => {
    const L = p.getTotalLength(); const dash = p.getAttribute("stroke-dasharray");
    p.style.strokeDasharray = `${L}`; p.style.strokeDashoffset = `${L}`;
    p.style.animation = `draw .8s cubic-bezier(.3,.7,.2,1) ${500 + i * 45}ms forwards`;
    p.addEventListener("animationend", () => { p.style.strokeDasharray = dash || ""; p.style.strokeDashoffset = ""; p.style.animation = ""; }, { once: true });
  });
  renderCallout();
}

function renderCallout() {
  const m = mapData(), c = $("map-callout");
  if (!m || $("view-map").hidden) { c.innerHTML = ""; return; }
  const close = `<button class="x" aria-label="Close" onclick="S.selectedEdge=null;S.focusNode=null;renderMapState();renderCallout()">×</button>`;
  if (S.selectedEdge != null && m.edges[S.selectedEdge]) {
    const e = m.edges[S.selectedEdge];
    const row = (label, v) => v ? `${label}: ${v.rate?.toFixed(2)} req/s · ${pct(v.error_rate)} errors${v.p95_ms != null ? ` · p95 ${Math.round(v.p95_ms)} ms` : ""}` : `${label}: no traffic`;
    c.innerHTML = `<div class="callout ${e.grade === "unexplained" ? "amber" : "info"}">${close}<h3>${esc(e.sentence)}</h3>
      Grade: <b>${esc(e.silent ? "silent (stopped since baseline)" : e.grade)}</b><br>${esc(row("Baseline", e.baseline))}<br>${esc(row("Now", e.now))}
      <div class="src">${e.evidence.map(esc).join("<br>")}</div><div class="src">sources: ${esc(e.sources.join(" + ") || "none")}</div></div>`;
    return;
  }
  if (S.focus === "unknown") {
    const e = m.edges.find((x) => x.grade === "unexplained");
    c.innerHTML = e ? `<div class="callout amber"><h3>${esc(e.sentence)} — unexplained</h3>
      Seen only in eBPF network flows. ${esc(e.evidence.join("; "))}.<br>Who owns it? The map cannot say, and says so.
      <div class="src">source: OBI network flows · Kubernetes inventory</div></div>` : `<div class="callout info"><h3>No unexplained callers</h3>Every caller has an owner label or traces.</div>`;
    return;
  }
  if (S.beat === 2) {
    const g = (k) => m.edges.filter((e) => !e.silent && e.grade === k).length;
    const rep = m.report;
    c.innerHTML = `<div class="callout info"><h3>${m.edges.length} dependencies, ${Object.keys(m.nodes).length} workloads</h3>
      ${g("confirmed")} confirmed by traces and eBPF · ${g("likely")} likely · ${g("unexplained")} unexplained.<br>
      Built by code in under a second. No AI involved.${rep ? `<br><br><b>Claude's review of the map:</b> ${esc(rep.summary || "")}` : ""}
      <div class="src">baseline captured ${esc(clock(m.baseline_captured_at))}</div></div>`;
    return;
  }
  if (S.focusNode) {
    const n = S.focusNode, v = m.nodes[n] || {};
    const deps = m.edges.filter((e) => e.caller === n).map((e) => e.callee), callers = m.edges.filter((e) => e.callee === n).map((e) => e.caller);
    c.innerHTML = `<div class="callout info">${close}<h3>${esc(n)}</h3>${esc(v.kind || "")} · ${esc(v.namespace || "")}${v.instrumented === false ? " · no traces" : ""}${v.labelled === false ? " · no owner label" : ""}<br>
      calls: ${esc(deps.join(", ") || "nothing")}<br>called by: ${esc(callers.join(", ") || "nobody")}</div>`;
    return;
  }
  c.innerHTML = "";
}

// Re-lay out whenever the map box changes size (window resize, callout or counter appearing).
let lastSize = "";
new ResizeObserver(() => {
  const b = $("map").getBoundingClientRect(); if (!b.width || b.width + "x" + b.height === lastSize) return;
  clearTimeout(layoutMap._t); layoutMap._t = setTimeout(layoutMap, 80);
}).observe($("map"));

// ---------------------------------------------------------------- alerts

function renderAlerts() {
  const a = alertData()?.alerts || [];
  $("stat-alerts").textContent = a.length;
  $("stat-alerts-word").textContent = a.length === 1 ? "alert" : "alerts";
  $("alert-dot").className = "dot " + (a.length ? "firing" : "ok");
  $("alerts-sub").textContent = S.mode === "replay" && S.run?.prefetch?.alerts ? `as recorded · ${clock(S.run.prefetch.alerts.at)}` : "Grafana alerting · live";
  const box = $("alerts");
  if (!a.length) { box.innerHTML = `<div class="empty">No alerts firing.</div>`; delete box.dataset.key; return; }
  const key = a.map((x) => x.alert + x.service).join("|");
  if (box.dataset.key === key) return; // unchanged: keep the DOM so nothing re-animates
  box.dataset.key = key;
  box.innerHTML = a.slice().reverse().map((x) =>
    `<div class="alert ${x.severity === "warning" ? "warning" : ""}"><span class="dot"></span><span>${esc(x.service)} · ${esc(x.alert)}</span><span class="t">${esc(clock(x.started).slice(0, 8))}</span></div>`).join("");
}

// ---------------------------------------------------------------- faults (beat 4)

function renderFaultBar() {
  const bar = $("fault-bar");
  if (bar.hidden) return;
  const busy = S.job?.state === "running";
  const replay = S.mode === "replay";
  bar.innerHTML = `<div class="faults" style="flex:1 1 100%">${S.faults.map((f) =>
    `<button class="fault ${f.excluded ? "excluded" : ""}" data-id="${esc(f.id)}" ${busy || replay || f.excluded ? "disabled" : ""} title="${f.excluded ? esc(f.excluded) : ""}">${esc(f.title)}<small>${esc(f.id)}</small></button>`).join("")}</div>
    <span class="status-line ${S.job?.state === "error" ? "err" : ""}">${esc(jobLine())}</span>`;
  bar.querySelectorAll(".fault").forEach((el) => (el.onclick = () => inject(el.dataset.id)));
}
function jobLine() {
  const j = S.job || {};
  if (S.mode === "replay") return "Replay mode: faults are injected in Live mode only.";
  if (j.state === "running") return j.action === "reset" ? "Resetting the lab…" : `Injecting ${j.fault}…`;
  if (j.state === "error") return `Failed: ${j.message || "unknown error"}`;
  if (j.state === "done" && j.action === "inject") return `${j.fault} injected at ${clock(j.at)}. Alerts usually fire within 1–3 minutes.`;
  if (j.state === "done" && j.action === "reset") return `Lab reset at ${clock(j.at)}.`;
  return "Pick a fault. The agent is never told which one.";
}
async function inject(id) {
  try { S.job = await api(`/api/faults/${encodeURIComponent(id)}`, { method: "POST" }); renderFaultBar(); }
  catch (e) { toast(e.message, true); }
}
async function resetLab() {
  if (S.mode === "replay") { toast("Reset works in Live mode."); return; }
  try { S.job = await api("/api/faults/reset", { method: "POST" }); renderFaultBar(); toast("Resetting the lab…"); }
  catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------- runs (live + replay share one path)

function newRun(id, kind, speed = null) {
  return { id, kind, speed, meta: null, steps: new Map(), order: [], notes: [], counter: null, verdict: null, error: null,
           cost: null, start: null, last: null, tools: [], finished: false, prefetch: null, rateLimited: false };
}
function closeStream() { if (S.es) { S.es.close(); S.es = null; } }

function openStream(url, run, isDiagnosis) {
  closeStream();
  S.run = run;
  if (isDiagnosis) S.diagnosis = run;
  renderRun();
  const es = new EventSource(url); S.es = es;
  es.onmessage = (msg) => {
    if (S.run !== run) return;
    const ev = JSON.parse(msg.data);
    if (ev.type === "meta") { Object.assign(run, newRun(run.id, run.kind, run.speed)); run.meta = ev.data; run.start = ev.ts; }
    apply(run, ev);
    if (ev.type === "stream_end") { es.close(); if (S.es === es) S.es = null; }
    renderRun();
  };
  es.onerror = () => {
    if (run.finished) { es.close(); return; }
    if (es.readyState === EventSource.CLOSED) { run.error = run.error || "Lost the connection to the console."; run.finished = true; renderRun(); }
  };
}

function apply(run, ev) {
  const d = ev.data || {};
  if (ev.ts) { run.last = ev.ts; run.start ??= ev.ts; }
  switch (ev.type) {
    case "prefetch":
      run.prefetch = d;
      if (S.mode === "replay") { if (d.map_view) layoutMap(); renderAlerts(); }
      break;
    case "run_started": run.tools = d.tools || []; run.model = d.model; break;
    case "narration": {
      const text = d.text.replace(/```json[\s\S]*?```/g, "").trim();
      if (text) { const id = "n" + run.order.length; run.steps.set(id, { note: true, text, ts: ev.ts }); run.order.push(id); }
      break;
    }
    case "tool_call":
      run.steps.set(d.id, { step: d.step, tool: d.tool, server: d.server, input: d.input, ts: ev.ts, pending: true });
      run.order.push(d.id); break;
    case "tool_result": {
      const s = run.steps.get(d.id);
      if (s) Object.assign(s, { pending: false, error: d.is_error, dur: d.duration_s, chars: d.text.length, result: d.text });
      break;
    }
    case "counter": run.counter = d; break;
    case "verdict": run.verdict = d; break;
    case "cost": run.cost = d; break;
    case "rate_limit":
      run.rateLimited = true;
      run.error = `Claude usage limit reached (${d.window || "limit"}${d.resets_at ? `, resets ${new Date(d.resets_at * 1000).toLocaleTimeString()}` : ""}). Switch to Replay.`;
      break;
    case "error": if (!run.rateLimited) run.error = d.message + (d.stderr ? ` (${d.stderr.slice(-160)})` : ""); break;
    case "run_finished": run.finished = true; run.ok = d.ok; run.duration = d.duration_s; break;
    case "stream_end": run.finished = true; break;
  }
}

function describeInput(s) {
  const i = s.input || {};
  const bits = Object.entries(i).filter(([, v]) => v !== null && v !== undefined && v !== "").map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`);
  return bits.join(" · ").slice(0, 160);
}
const TOOL_WORDS = {
  incident_summary: "Group the firing alerts into incidents", service_map: "Compare the map now with the healthy baseline",
  what_changed: "List changes before the first alert", metric_movers: "Find metrics that moved first",
  log_pattern_diff: "Diff log patterns against the baseline", trace_examples: "Pull example failing traces",
};

function renderRun() {
  const r = S.run;
  const steps = $("steps");
  $("replay-badge").hidden = !(r && r.kind === "replay");
  if (r && r.kind === "replay") {
    const m = r.meta || {};
    $("replay-badge").textContent = `Recorded real run · ${(m.started_utc || "").replace("T", " ").replace("Z", " UTC")} · ${m.model || ""}${m.effort ? " " + m.effort : ""} · ${r.speed === 0 ? "shown instantly" : `${r.speed}× speed`}`;
  }
  if (!r) {
    steps.innerHTML = `<li class="empty">${S.mode === "live" ? "No investigation yet. Beat 5 starts one." : "Pick a replay."}</li>`;
    $("verdict").innerHTML = ""; $("steps-sub").textContent = "idle"; $("stat-time").textContent = "–"; $("stat-cost").textContent = "–";
    renderCounter(null); return;
  }
  const elapsed = r.start && r.last ? r.last - r.start : 0;
  $("stat-time").textContent = mmss(elapsed);
  $("stat-cost").textContent = r.cost?.usd != null ? `$${r.cost.usd.toFixed(2)}` : "–";
  $("steps-sub").textContent = r.finished ? (r.ok ? "finished" : "stopped") : `${r.model || r.meta?.model || ""} · working`;
  const items = r.order.map((id) => {
    const s = r.steps.get(id);
    const t = s.ts && r.start ? mmss(s.ts - r.start) : "";
    if (s.note) return `<li class="step note"><div class="txt">${esc(s.text.length > 320 ? s.text.slice(0, 320) + "…" : s.text)}</div></li>`;
    const what = TOOL_WORDS[s.tool] || s.tool.replace(/_/g, " ");
    const size = s.pending ? "" : ` · ${fmt(s.chars)} chars back`;
    return `<li class="step ${s.pending ? "pending" : ""} ${s.error ? "error" : ""}"><div class="row"><span class="tool">${s.step}. ${esc(s.server)}.${esc(s.tool)}</span><span class="t">${t}${s.dur != null ? ` · ${s.dur.toFixed(1)}s` : ""}</span></div>
      <div class="txt">${esc(what)}${describeInput(s) ? ` <span style="color:var(--muted)">(${esc(describeInput(s))})</span>` : ""}${esc(size)}</div></li>`;
  });
  if (!r.finished && !r.order.length) items.push(`<li class="step pending note"><div class="txt">Claude is reading the incident summary and the map…</div></li>`);
  const key = items.join("");
  if (steps.dataset.key !== key) { steps.innerHTML = key || `<li class="empty">No steps.</li>`; steps.dataset.key = key; steps.scrollTop = steps.scrollHeight; }
  renderCounter(r.counter);
  renderVerdict(r);
  renderMapState();
  if (S.beat === 5) renderStageTools();
  if (!$("view-doc").hidden && r.finished && S.beat !== 9) renderDoc();
}

function renderCounter(c) {
  const v = c || { scanned: 0, found: 0, anomalous: 0, tokens_to_model: 0 };
  const map = { scanned: v.scanned, found: v.found, anomalous: v.anomalous, tokens: v.tokens_to_model };
  const max = Math.max(1, v.scanned, v.tokens_to_model);
  Object.entries(map).forEach(([k, n]) => {
    animateNumber($("c-" + k), n);
    $("b-" + k).style.width = `${n ? Math.max(2, Math.log10(n + 1) / Math.log10(max + 1) * 100) : 0}%`;
  });
}
function animateNumber(el, to) {
  const from = +el.dataset.v || 0; if (from === to) return;
  el.dataset.v = to; const t0 = performance.now(), D = 700;
  const step = (t) => { const p = Math.min(1, (t - t0) / D), e = 1 - Math.pow(1 - p, 3); el.textContent = fmt(Math.round(from + (to - from) * e)); if (p < 1) requestAnimationFrame(step); };
  requestAnimationFrame(step);
}

function renderVerdict(r) {
  const box = $("verdict");
  if (r.error && !r.verdict) {
    delete box.dataset.key;
    box.innerHTML = `<div class="verdict failed"><div class="k">Run did not finish</div><h3>${esc(r.error)}</h3>
      <p>Every beat has a recorded replay behind it.</p><div class="actions"><button class="btn" onclick="setMode('replay')">Switch to Replay</button></div></div>`;
    return;
  }
  const v = r.verdict;
  if (!v) { box.innerHTML = ""; delete box.dataset.key; return; }
  const status = ["diagnosed", "uncertain", "healthy"].includes(v.status) ? v.status : "uncertain";
  const pc = v.primary_cause || {};
  const cands = arr(v.candidates).map((c) => c || {}).map((c) => c.service).filter(Boolean);
  const head = status === "healthy" ? "No incident: the estate looks healthy"
    : status === "uncertain" ? (cands.length ? `Not certain: ${cands.join(" or ")}` : pc.service ? `Probably ${pc.service}, not certain` : "Not certain")
    : `${pc.service || "?"}`;
  const body = status === "uncertain"
    ? arr(v.candidates).map((c) => c || {}).map((c) => `<p><b>${esc(c.service)}:</b> ${esc(c.why || "")}</p>`).join("") + (v.deciding_query ? `<p><b>Query that would decide:</b> ${esc(v.deciding_query)}</p>` : "")
    : status === "healthy" ? (v.why ? `<p>${esc(v.why)}</p>` : "") : (pc.mechanism ? `<p>${esc(pc.mechanism)}</p>` : "");
  const key = JSON.stringify([r.id, status, head, v.confidence, r.cost?.usd]);
  if (box.dataset.key !== key) {
  box.dataset.key = key;
  box.innerHTML = `<div class="verdict ${status}"><div class="k">${esc(status === "diagnosed" ? "Primary cause" : status)}<span>${v.confidence ? `confidence ${esc(v.confidence)}` : ""}</span></div>
    <h3>${esc(head)}</h3>${body}
    ${arr(v.symptoms).length ? `<p><b>Symptoms, not causes:</b> ${esc(arr(v.symptoms).join(", "))}</p>` : ""}
    <div class="actions"><button class="btn" id="v-evidence">Evidence (${arr(v.evidence).length})</button>
    ${v.proposed_fix ? `<button class="btn" onclick="setBeat(6)">Proposed fix</button>` : ""}${v.postmortem ? `<button class="btn" onclick="setBeat(8)">Postmortem</button>` : ""}</div></div>`;
  }
  const btn = $("v-evidence"); if (btn) btn.onclick = () => showEvidence(r);
}

function showEvidence(r) {
  const v = r.verdict || {};
  const answer = (v.text || "").replace(/```json[\s\S]*?```/g, "").trim();
  const ck = v.checklist || {};
  modal(`<h2>Evidence behind the answer</h2>
    ${answer ? `<p style="color:var(--text-2);line-height:1.5">${esc(answer)}</p>` : ""}
    <div class="doc" style="padding:0">
    ${arr(v.evidence).map((e) => e || {}).map((e, i) => `<p><b>${i + 1}. ${esc(e.claim)}</b><br><span class="kv">${esc(e.tool)}${e.query ? ` · ${esc(e.query)}` : ""}</span></p>`).join("") || "<p>No evidence listed.</p>"}
    ${Object.keys(ck).length ? `<h2>Checklist</h2>${Object.entries(ck).map(([k, x]) => `<p><b>${esc(k)}:</b> ${esc(typeof x === "string" ? x : JSON.stringify(x))}</p>`).join("")}` : ""}
    </div>`);
}
function modal(html) {
  $("modal-body").innerHTML = `<button class="btn" style="float:right;min-height:36px" onclick="closeModal()">Close</button>` + html;
  $("modal").hidden = false;
}
function closeModal() { $("modal").hidden = true; }
$("modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeModal();
  if (e.target.tagName === "INPUT" || e.ctrlKey || e.metaKey || e.altKey) return;
  if (/^[1-9]$/.test(e.key) && $("modal").hidden) setBeat(+e.key);
});

async function startLive() {
  if (S.starting) return;
  S.starting = true; renderStageTools();
  try {
    const r = await api("/api/runs", { method: "POST", body: JSON.stringify({}) });
    openStream(`/api/runs/${r.id}/events`, newRun(r.id, "live"), true);
  } catch (e) { toast(e.message, true); }
  finally { S.starting = false; renderStageTools(); }
}
async function stopLive() {
  if (!S.run) return;
  try { await api(`/api/runs/${S.run.id}/stop`, { method: "POST" }); } catch (e) { toast(e.message, true); }
}
async function reattachLive() {
  // After a page reload, pick a still-running live investigation back up (never an old finished one).
  try {
    const a = await api("/api/runs/active");
    if (a.id && !S.run && S.mode === "live") openStream(`/api/runs/${a.id}/events`, newRun(a.id, "live"), true);
  } catch { /* console offline: health shows it */ }
}

function replaysFor(beat) { return S.replays.filter((r) => r.beat === beat); }
function playReplay(id, speed = S.speed) {
  if (S.mode !== "replay") { S.mode = "replay"; $("mode-live").setAttribute("aria-pressed", "false"); $("mode-replay").setAttribute("aria-pressed", "true"); }
  const rep = S.replays.find((x) => x.id === id);
  openStream(`/api/replays/${encodeURIComponent(id)}/events?speed=${speed}`, newRun(id, "replay", speed), rep?.beat === 5);
}
function ensureRunForBeat() {
  if (S.diagnosis || (S.run && !S.run.finished)) return;
  if (S.mode === "replay" && (S.beat === 6 || S.beat === 8)) { const r = replaysFor(5)[0]; if (r) playReplay(r.id, 0); }
}

// ---------------------------------------------------------------- documents (beats 6-9)

function renderDoc() {
  const doc = $("doc"), r = S.diagnosis, v = r?.verdict;
  const needRun = `<p>This beat reads the latest investigation. ${S.mode === "live" ? "Run one in beat 5, or switch to Replay." : "Pick a replay in beat 5."}</p>`;
  if (S.beat === 6) {
    if (!v) { doc.innerHTML = `<h2>The guardrail</h2>` + needRun + guardrailDesign(r); return; }
    const f = v.proposed_fix;
    doc.innerHTML = `${f ? `<div class="label">Proposed fix · waiting for a person</div>` : ""}
      ${f ? `<h2>${esc(f.action)}</h2><p><b>Risk:</b> ${esc(f.risk || "–")}</p><p><b>How to verify:</b> ${esc(f.verify || "–")}</p>` : `<h2>No fix proposed</h2><p>The agent did not propose a change for this run.</p>`}
      <div class="toolbar" style="margin:14px 0"><button class="btn primary" disabled title="The agent has no write access">Approve and apply</button>
      <span class="status-line">The agent cannot apply this. It has no tool that writes. A person runs it through normal change control.</span></div>` + guardrailDesign(r);
    return;
  }
  if (S.beat === 7) { renderHonest(doc); return; }
  if (S.beat === 8) {
    const p = v?.postmortem;
    if (!p) { doc.innerHTML = `<h2>The postmortem</h2>` + (v ? "<p>This run did not include a postmortem draft.</p>" : needRun); return; }
    const item = (i) => (i && typeof i === "object" ? Object.values(i).join(" — ") : String(i ?? ""));
    const list = (x) => Array.isArray(x) ? `<ul>${x.map((i) => `<li>${esc(item(i))}</li>`).join("")}</ul>` : `<p>${esc(item(x) || "–")}</p>`;
    doc.innerHTML = `<div class="label">Draft · written by Claude for people to edit · ${esc(r.kind === "replay" ? "recorded run" : "live run")}</div>
      <h2>Summary</h2><p>${esc(p.summary || "–")}</p><h2>Timeline</h2>${list(p.timeline)}<h2>Root cause</h2>${list(p.root_cause)}
      <h2>Blast radius</h2>${list(p.blast_radius)}<h2>Action items</h2>${list(p.action_items)}<h2>Gaps in the map</h2>${list(p.map_gaps)}`;
    return;
  }
  if (S.beat === 9) { renderScorecard(doc); return; }
}

function guardrailDesign(r) {
  const tools = r?.tools?.length ? r.tools : null;
  return `<h2>What the agent can touch</h2><div class="grid2"><div class="kv">
    ${tools ? tools.map((t) => `<div class="yes">✓ ${esc(t.replace(/^mcp__/, "").replace("__", "."))}</div>`).join("") : `<div class="yes">✓ 6 reduction tools (read-only queries)</div><div class="yes">✓ Grafana MCP: Prometheus, Loki, Tempo, alerting (read)</div>`}
    </div><div class="kv"><div class="no">✗ shell (Bash)</div><div class="no">✗ read or write files</div><div class="no">✗ web fetch or search</div><div class="no">✗ Grafana writes (--disable-write, Viewer role)</div><div class="no">✗ kubectl, git, chaos, flags</div></div></div>
    <p class="hint" style="margin-top:12px">Tool results are treated as data, never as instructions. A rehearsal plants a malicious log line to prove an injected instruction has nothing to act on.</p>`;
}

function renderHonest(doc) {
  const reps = replaysFor(7);
  const cur = S.run && S.run.kind === "replay" && reps.some((x) => x.id === S.run.id) ? S.run : null;
  doc.innerHTML = `<div class="label">Recorded real runs</div><h2>When the right answer is "not sure", or "nothing is wrong"</h2>
    ${reps.length ? `<div class="replay-list">${reps.map((x) => `<button class="replay" data-id="${esc(x.id)}">${esc(x.title || x.id)}<small>${esc(x.scenario || "")} · verdict: ${esc(x.verdict?.status || "–")} · ${esc(x.duration_s ? mmss(x.duration_s) : "")}</small></button>`).join("")}</div>` : "<p>No replays recorded yet.</p>"}
    ${cur ? `<p class="hint" style="margin-top:14px">Playing: ${esc(cur.meta?.title || cur.id)}. The verdict appears on the right.</p>` : ""}`;
  doc.querySelectorAll(".replay").forEach((el) => (el.onclick = () => playReplay(el.dataset.id)));
}

async function renderScorecard(doc) {
  try { S.scorecard = await api("/api/scorecard"); } catch (e) { doc.innerHTML = `<h2>Scorecard unavailable</h2><p>${esc(e.message)}</p>`; return; }
  const sc = S.scorecard;
  if (!sc.available) { doc.innerHTML = "<h2>The scorecard</h2><p>No rehearsal results yet.</p>"; return; }
  doc.innerHTML = `<div class="label">Rehearsal · ${esc(sc.model || "")} · ${esc(sc.generated_utc || "")}</div>
    <h2>${esc(sc.headline || "")}</h2>
    <table><thead><tr><th>Scenario</th><th>Passed</th><th>Median time</th><th>Median cost</th><th>Status</th></tr></thead><tbody>
    ${(sc.rows || []).map((x) => {
      const off = x.on_menu === false;
      const pill = off ? ["off", "off the menu"] : x.passed === x.runs ? ["pass", "pass"] : x.passed ? ["fail", "mixed"] : ["fail", "fail"];
      return `<tr><td>${esc(x.title)}<br><span class="hint">${esc(off ? x.off_reason || "" : x.expect || "")}</span></td>
      <td class="num">${x.runs ? `${x.passed} / ${x.runs}` : "–"}</td>
      <td class="num">${x.median_s != null ? esc(mmss(x.median_s)) : "–"}</td><td class="num">${x.median_usd != null ? "$" + x.median_usd.toFixed(2) : "–"}</td>
      <td><span class="pill ${pill[0]}">${pill[1]}</span></td></tr>`; }).join("")}
    </tbody></table><p class="hint" style="margin-top:12px">${esc(sc.method || "")}</p>`;
}

// ---------------------------------------------------------------- boot

async function boot() {
  $("mode-live").onclick = () => setMode("live");
  $("mode-replay").onclick = () => setMode("replay");
  $("reset-btn").onclick = resetLab;
  renderBeats();
  try { S.replays = (await api("/api/replays")).replays; } catch { S.replays = []; }
  await Promise.all([pollHealth(), pollAlerts(), pollFaults(), pollMap()]);
  setBeat(S.beat);
  reattachLive();
  setInterval(pollAlerts, 5000);
  setInterval(pollMap, 15000);
  setInterval(pollHealth, 15000);
  setInterval(pollFaults, 2000);
  setInterval(() => { if (S.run && !S.run.finished && S.run.kind === "live") $("stat-time").textContent = mmss(Date.now() / 1000 - S.run.start); }, 1000);
}
boot();
