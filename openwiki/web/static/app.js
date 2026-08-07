"use strict";

const $ = (sel) => document.querySelector(sel);
const state = { manifest: null, pages: {}, firstSlug: null, currentSlug: null,
                tab: "wiki", wikiMarkdown: null, docs: {} };
const WRITE_TOOLS = new Set(["create_page", "edit_page", "append_section"]);

async function getJSON(url) {
  const r = await fetch(url);
  const data = await r.json().catch(() => ({ error: r.statusText }));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}
async function postJSON(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({ error: r.statusText }));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}
function escapeHtml(s) { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }

// -- navigation tree --------------------------------------------------------

function renderNav() {
  const pages = state.manifest.pages || [];
  pages.forEach((p) => (state.pages[p.slug] = p));
  const build = (page) => {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = "#" + page.slug;
    a.textContent = page.title;
    a.dataset.slug = page.slug;
    a.addEventListener("click", (e) => { e.preventDefault(); loadPage(page.slug); });
    li.appendChild(a);
    const kids = (page.children || []).map((s) => state.pages[s]).filter(Boolean);
    if (kids.length) {
      const ul = document.createElement("ul");
      kids.forEach((k) => ul.appendChild(build(k)));
      li.appendChild(ul);
    }
    return li;
  };
  const ul = document.createElement("ul");
  pages.filter((p) => !p.parent).forEach((r) => ul.appendChild(build(r)));
  const nav = $("#nav");
  nav.innerHTML = "";
  nav.appendChild(ul);
  setActive(state.currentSlug);
}
function setActive(slug) {
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.slug === slug));
}

// -- page view --------------------------------------------------------------

function slugFromHref(href) {
  const path = href.split("#")[0].split("?")[0];
  const base = path.substring(path.lastIndexOf("/") + 1);
  if (!base.endsWith(".md")) return null;
  const name = base.slice(0, -3);
  return name === "index" ? state.firstSlug : name;
}
function interceptLinks() {
  document.querySelectorAll("#content a").forEach((a) => {
    const slug = slugFromHref(a.getAttribute("href") || "");
    if (slug) a.addEventListener("click", (e) => { e.preventDefault(); loadPage(slug); });
  });
}
async function loadPage(slug) {
  try {
    const { markdown } = await getJSON("/api/pages/" + encodeURIComponent(slug));
    state.currentSlug = slug;
    state.wikiMarkdown = markdown;
    activateTab("wiki");            // renders the page and highlights the Wiki tab
    setActive(slug);
    history.replaceState(null, "", "#" + slug);
  } catch (e) {
    $("#content").innerHTML = `<p class="muted">Fehler: ${escapeHtml(e.message)}</p>`;
  }
}

// -- tabs: Wiki / Hilfe / Tutorial -----------------------------------------

function activateTab(tab) {
  state.tab = tab;
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  renderActiveTab();
}
function renderActiveTab() {
  const content = $("#content");
  stopSim();  // pause any running graph layout when switching tabs
  if (state.tab === "wiki") {
    content.innerHTML = state.wikiMarkdown
      ? marked.parse(state.wikiMarkdown)
      : `<p class="muted">Keine Seite ausgewählt.</p>`;
    interceptLinks();
    content.scrollTop = 0;
  } else if (state.tab === "graph") {
    renderGraph();
  } else {
    renderDoc(state.tab);
  }
}
async function loadDoc(name) {
  if (state.docs[name]) return state.docs[name];
  const res = await fetch("/static/" + name + ".md");
  if (!res.ok) throw new Error(`${name}.md: ${res.status}`);
  const md = await res.text();
  state.docs[name] = md;
  return md;
}
async function renderDoc(name) {
  const content = $("#content");
  content.innerHTML = `<p class="muted">Wird geladen…</p>`;
  try {
    content.innerHTML = marked.parse(await loadDoc(name));
    wireRunActions();
    content.scrollTop = 0;
  } catch (e) {
    content.innerHTML = `<p class="muted">Fehler: ${escapeHtml(e.message)}</p>`;
  }
}
// Turn tutorial `run:<kind>:<arg>` links into buttons that drive the real UI.
function wireRunActions() {
  document.querySelectorAll("#content a").forEach((a) => {
    const href = a.getAttribute("href") || "";
    if (!href.startsWith("run:")) return;
    const rest = href.slice(4);
    const sep = rest.indexOf(":");
    if (sep < 0) return;
    const kind = rest.slice(0, sep);
    let arg = rest.slice(sep + 1);
    try { arg = decodeURIComponent(arg); } catch (_) { /* keep raw */ }
    a.classList.add("run-action");
    a.addEventListener("click", (e) => { e.preventDefault(); runAction(kind, arg); });
  });
}
// -- graph tab (interactive neighborhood exploration) ----------------------

const SVG_NS = "http://www.w3.org/2000/svg";
const EDGE_COLOR = { parent: "#7048e8", child: "#7048e8", prev: "#868e96", next: "#868e96",
                     similar: "#2f9e44", references: "#e8590c", referenced_by: "#e8590c",
                     shared_entity: "#0c8599", mentions: "#f08c00" };
// Legend/filter groups (a click toggles a whole relationship kind on/off).
const FILTERS = [
  { key: "hier",    label: "Hierarchie",          types: ["parent", "child"],          color: "#7048e8" },
  { key: "seq",     label: "Reihenfolge",         types: ["prev", "next"],             color: "#868e96" },
  { key: "similar", label: "Ähnlich",             types: ["similar"],                  color: "#2f9e44" },
  { key: "ref",     label: "Verweise",            types: ["references", "referenced_by"], color: "#e8590c" },
  { key: "shared",  label: "Gemeinsame Begriffe", types: ["shared_entity"],            color: "#0c8599" },
  { key: "entity",  label: "Begriffe (Entitäten)", types: ["mentions"],                color: "#f08c00" },
];
const TYPE_FILTER = {};
FILTERS.forEach((f) => f.types.forEach((t) => (TYPE_FILTER[t] = f.key)));

const GW = 900, GH = 600;   // SVG viewBox
// The live explorer graph (accumulates as you expand nodes).
const graph = { nodes: new Map(), edges: [], root: null, selected: null,
                hidden: new Set(), svg: null, raf: 0, alpha: 0,
                _nodeEls: new Map(), _edgeEls: [] };

function svgEl(tag, attrs) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const k in attrs) el.setAttribute(k, attrs[k]);
  return el;
}

async function renderGraph() {
  const content = $("#content");
  if (!state.currentSlug) {
    content.innerHTML = `<p class="muted">Keine Seite ausgewählt.</p>`;
    return;
  }
  content.innerHTML = `<p class="muted">Graph wird geladen…</p>`;
  try {
    const data = await getJSON("/api/graph/" + encodeURIComponent(state.currentSlug));
    initGraph(content, data);
  } catch (e) {
    content.innerHTML = `<p class="muted">Graph nicht verfügbar: ${escapeHtml(e.message)}` +
      `<br><span class="muted">Erzeuge ihn mit <code>openwiki graph-build</code> ` +
      `(Entitäten mit <code>--entities</code>).</span></p>`;
  }
}

function initGraph(content, data) {
  stopSim();
  graph.nodes = new Map();
  graph.edges = [];
  graph.root = data.root;
  graph.selected = data.root;
  mergeGraph(data, GW / 2, GH / 2, data.root);
  const root = graph.nodes.get(data.root);
  if (root) { root.x = GW / 2; root.y = GH / 2; root.expanded = true; }
  buildGraphDom(content);
  startSim();
}

function mergeGraph(data, ox, oy, parentId) {
  (data.nodes || []).forEach((n) => {
    if (!graph.nodes.has(n.id)) {
      // `parent` = the node that first pulled this one in (null for the root),
      // so collapsing a node can remove exactly the subtree it introduced.
      graph.nodes.set(n.id, { ...n, parent: n.id === graph.root ? null : parentId,
        x: ox + (Math.random() - 0.5) * 320, y: oy + (Math.random() - 0.5) * 320,
        vx: 0, vy: 0, fixed: false, expanded: false });
    }
  });
  const seen = new Set(graph.edges.map((e) => e.source + "|" + e.target + "|" + e.type));
  (data.edges || []).forEach((e) => {
    const k = e.source + "|" + e.target + "|" + e.type;
    if (!seen.has(k) && graph.nodes.has(e.source) && graph.nodes.has(e.target)) {
      seen.add(k);
      graph.edges.push({ source: e.source, target: e.target, type: e.type });
    }
  });
}

const isNodeHidden = (n) => n && n.kind === "entity" && graph.hidden.has("entity");
function isEdgeHidden(e) {
  const fk = TYPE_FILTER[e.type];
  if (fk && graph.hidden.has(fk)) return true;
  return isNodeHidden(graph.nodes.get(e.source)) || isNodeHidden(graph.nodes.get(e.target));
}

function buildGraphDom(content) {
  content.innerHTML = "";

  const bar = document.createElement("div");
  bar.className = "graph-bar";
  const sel = graph.nodes.get(graph.selected) || graph.nodes.get(graph.root);
  const title = document.createElement("strong");
  title.textContent = sel ? sel.label : graph.root;
  const hint = document.createElement("span");
  hint.className = "graph-hint muted";
  hint.textContent = "Klick = erweitern · Doppelklick = einklappen · ziehen zum Anordnen";
  const reset = document.createElement("button");
  reset.className = "graph-reset";
  reset.textContent = "Zurücksetzen";
  reset.addEventListener("click", () => renderGraph());
  const open = document.createElement("button");
  open.className = "graph-open";
  open.textContent = "Seite öffnen →";
  open.addEventListener("click", () => loadPage(graph.selected || graph.root));
  bar.append(title, hint, reset, open);

  const filters = document.createElement("div");
  filters.className = "graph-filters";
  FILTERS.forEach((f) => {
    const chip = document.createElement("button");
    chip.className = "graph-filter" + (graph.hidden.has(f.key) ? " off" : "");
    chip.innerHTML = `<i style="background:${f.color}"></i>${f.label}`;
    chip.addEventListener("click", () => {
      graph.hidden.has(f.key) ? graph.hidden.delete(f.key) : graph.hidden.add(f.key);
      buildGraphDom(content);
      bumpSim();
    });
    filters.appendChild(chip);
  });

  const svg = svgEl("svg", { viewBox: `0 0 ${GW} ${GH}`, class: "graph-svg" });
  const edgeG = svgEl("g", {});
  const nodeG = svgEl("g", {});
  svg.append(edgeG, nodeG);
  graph.svg = svg;
  graph._edgeEls = [];
  graph._nodeEls = new Map();
  graph._labelEls = new Map();

  // Node degree over currently-visible edges — drives label priority.
  const deg = {};
  graph.edges.forEach((e) => {
    if (isEdgeHidden(e)) return;
    const line = svgEl("line", { stroke: EDGE_COLOR[e.type] || "#ccc",
      "stroke-width": e.type === "similar" ? 1.3 : 2, "stroke-opacity": 0.4 });
    edgeG.appendChild(line);
    graph._edgeEls.push({ e, line });
    deg[e.source] = (deg[e.source] || 0) + 1;
    deg[e.target] = (deg[e.target] || 0) + 1;
  });

  graph.nodes.forEach((n) => {
    if (isNodeHidden(n)) return;
    const g = svgEl("g", { class: "gnode" + (n.expanded ? " expanded" : "") });
    const r = n.kind === "entity" ? 10 : (n.root ? 13 : 9);
    if (n.expanded && !n.root) {   // outer ring marks an expanded node (double-click to collapse)
      g.appendChild(svgEl("circle", { r: r + 5, fill: "none",
        stroke: n.kind === "entity" ? "#f08c00" : "#4dabf7", "stroke-width": 1.5, "stroke-opacity": 0.5 }));
    }
    if (n.kind === "entity") {
      g.appendChild(svgEl("rect", { x: -7, y: -7, width: 14, height: 14,
        transform: "rotate(45)", fill: "#f08c00", stroke: "#fff", "stroke-width": 2 }));
    } else {
      g.appendChild(svgEl("circle", { r: n.root ? 13 : 9,
        fill: n.root ? "#3b5bdb" : "#4dabf7", stroke: "#fff", "stroke-width": 2 }));
    }
    const label = svgEl("text", { y: -14, "text-anchor": "middle", class: "graph-label" });
    label.textContent = n.label.length > 24 ? n.label.slice(0, 24) + "…" : n.label;
    g.appendChild(label);
    const tip = svgEl("title", {});
    tip.textContent = n.kind === "entity" ? `${n.label} [${n.etype}]` : n.label;
    g.appendChild(tip);
    attachNodeEvents(g, n);
    nodeG.appendChild(g);
    graph._nodeEls.set(n.id, g);
    graph._labelEls.set(n.id, label);
    n._deg = deg[n.id] || 0;
    n._lw = undefined;  // real width measured lazily via getBBox() once rendered
  });

  content.append(bar, filters, svg);
  content.scrollTop = 0;
}

function attachNodeEvents(g, n) {
  let sx = 0, sy = 0, moved = false, down = false;
  g.addEventListener("pointerdown", (ev) => {
    down = true; moved = false; sx = ev.clientX; sy = ev.clientY;
    n.fixed = true;
    g.setPointerCapture(ev.pointerId);
  });
  g.addEventListener("pointermove", (ev) => {
    if (!down) return;
    if (!moved && Math.hypot(ev.clientX - sx, ev.clientY - sy) > 4) moved = true;
    if (moved) {
      const p = clientToSvg(ev.clientX, ev.clientY);
      n.x = p.x; n.y = p.y;
      bumpSim();
      drawPositions();
    }
  });
  g.addEventListener("pointerup", (ev) => {
    down = false;
    n.fixed = false;
    try { g.releasePointerCapture(ev.pointerId); } catch (_) {}
    if (moved) return;               // was a drag, not a click
    // Disambiguate single click (expand) from double click (collapse).
    if (n._clickTimer) {             // second click within the window → collapse
      clearTimeout(n._clickTimer); n._clickTimer = null;
      collapseNode(n);
    } else {
      n._clickTimer = setTimeout(() => {
        n._clickTimer = null;
        if (graph.nodes.has(n.id)) onNodeClick(n);
      }, 260);
    }
  });
}

async function onNodeClick(n) {
  if (n.kind === "page") graph.selected = n.id;
  if (!n.expanded) {
    n.expanded = true;
    try {
      const data = await postJSON("/api/graph/expand", { type: n.kind, id: n.id });
      mergeGraph(data, n.x, n.y, n.id);   // new nodes get this node as their parent
    } catch (_) { n.expanded = false; }
  }
  buildGraphDom($("#content"));   // reflects new nodes + updated selection
  bumpSim();
}

// Double-click: collapse the subtree this node introduced (inverse of expand).
function collapseNode(n) {
  if (n.root) return;   // the root is the anchor — use "Zurücksetzen" to start over
  const gone = descendantsOf(n.id);
  if (!gone.size) { n.expanded = false; return; }  // nothing to collapse
  gone.forEach((id) => graph.nodes.delete(id));
  graph.edges = graph.edges.filter((e) => !gone.has(e.source) && !gone.has(e.target));
  n.expanded = false;
  if (gone.has(graph.selected)) graph.selected = graph.root;
  buildGraphDom($("#content"));
  bumpSim();
}

function descendantsOf(id) {
  const children = {};
  graph.nodes.forEach((nd) => { if (nd.parent) (children[nd.parent] ||= []).push(nd.id); });
  const out = new Set();
  const stack = [...(children[id] || [])];
  while (stack.length) {
    const cur = stack.pop();
    if (out.has(cur)) continue;
    out.add(cur);
    (children[cur] || []).forEach((c) => stack.push(c));
  }
  return out;
}

// -- force simulation -------------------------------------------------------

function startSim() { graph.alpha = 0.9; ensureSim(); }
function bumpSim() { graph.alpha = Math.max(graph.alpha, 0.5); ensureSim(); }
function ensureSim() { if (!graph.raf) graph.raf = requestAnimationFrame(simStep); }
function stopSim() { if (graph.raf) cancelAnimationFrame(graph.raf); graph.raf = 0; }

function simStep() {
  physicsTick();
  drawPositions();
  graph.alpha *= 0.97;
  graph.raf = graph.alpha > 0.02 ? requestAnimationFrame(simStep) : 0;
}

function physicsTick() {
  const ns = [...graph.nodes.values()].filter((n) => !isNodeHidden(n));
  const cx = GW / 2, cy = GH / 2;
  ns.forEach((n) => { n.fx = 0; n.fy = 0; });
  for (let i = 0; i < ns.length; i++) {
    for (let j = i + 1; j < ns.length; j++) {
      const a = ns[i], b = ns[j];
      let dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy || 1;
      const d = Math.sqrt(d2), f = 11000 / d2, ux = dx / d, uy = dy / d;
      a.fx += ux * f; a.fy += uy * f; b.fx -= ux * f; b.fy -= uy * f;
    }
  }
  graph.edges.forEach((e) => {
    if (isEdgeHidden(e)) return;
    const a = graph.nodes.get(e.source), b = graph.nodes.get(e.target);
    if (!a || !b) return;
    let dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy) || 1;
    const rest = e.type === "mentions" ? 95 : 135;
    const f = (d - rest) * 0.03, ux = dx / d, uy = dy / d;
    a.fx += ux * f; a.fy += uy * f; b.fx -= ux * f; b.fy -= uy * f;
  });
  ns.forEach((n) => {
    if (n.fixed) return;
    n.fx += (cx - n.x) * 0.007; n.fy += (cy - n.y) * 0.007;
    n.vx = (n.vx + n.fx) * 0.85; n.vy = (n.vy + n.fy) * 0.85;
    n.x += n.vx * graph.alpha; n.y += n.vy * graph.alpha;
    n.x = Math.max(24, Math.min(GW - 24, n.x));
    n.y = Math.max(24, Math.min(GH - 24, n.y));
  });
}

function drawPositions() {
  graph._edgeEls.forEach(({ e, line }) => {
    const a = graph.nodes.get(e.source), b = graph.nodes.get(e.target);
    if (!a || !b) return;
    line.setAttribute("x1", a.x); line.setAttribute("y1", a.y);
    line.setAttribute("x2", b.x); line.setAttribute("y2", b.y);
  });
  graph._nodeEls.forEach((g, id) => {
    const n = graph.nodes.get(id);
    if (n) g.setAttribute("transform", `translate(${n.x},${n.y})`);
  });
  if (graph.alpha < 0.25) declutterLabels();  // only once the layout is calming down
}

// Greedy, priority-based label culling: hide labels whose box overlaps a
// higher-priority label already placed. Hidden labels still show on hover.
function labelPriority(n) {
  if (n.root) return 1e6;
  let p = (n.expanded ? 400 : 0) + (n._deg || 0) * 10;
  if (n.id === graph.selected) p += 800;
  if (n.kind === "entity") p += 5;
  return p;
}

function declutterLabels() {
  if (!graph._labelEls || !graph._labelEls.size) return;
  const nodes = [...graph.nodes.values()]
    .filter((n) => !isNodeHidden(n) && graph._labelEls.has(n.id))
    .sort((a, b) => labelPriority(b) - labelPriority(a));
  const placed = [];
  for (const n of nodes) {
    if (n._lw === undefined) {           // measure the real rendered width once
      try { n._lw = graph._labelEls.get(n.id).getBBox().width + 8; }
      catch (_) { n._lw = n.label.length * 7; }
    }
    const w = n._lw, cx = n.x, cy = n.y - 14;
    const box = { x0: cx - w / 2, x1: cx + w / 2, y0: cy - 10, y1: cy + 6 };
    const hit = placed.some((p) => box.x0 < p.x1 && box.x1 > p.x0 && box.y0 < p.y1 && box.y1 > p.y0);
    graph._labelEls.get(n.id).style.opacity = hit ? "0" : "1";
    if (!hit) placed.push(box);
  }
}

function clientToSvg(clientX, clientY) {
  const pt = graph.svg.createSVGPoint();
  pt.x = clientX; pt.y = clientY;
  const p = pt.matrixTransform(graph.svg.getScreenCTM().inverse());
  return { x: p.x, y: p.y };
}

function runAction(kind, arg) {
  if (kind === "page") {
    loadPage(arg);
  } else if (kind === "search") {
    const box = $("#search");
    box.value = arg;
    runSearch(arg);          // results appear in the sidebar (always visible)
    box.focus();
  } else if (kind === "ask") {
    sendChat(arg);           // reply/edits appear in the chat pane (always visible)
  } else if (kind === "graph") {
    state.currentSlug = arg;
    setActive(arg);
    history.replaceState(null, "", "#" + arg);
    activateTab("graph");    // opens the Graph tab centered on this page
  }
}

// -- semantic search --------------------------------------------------------

let searchTimer = null;
$("#search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  const q = e.target.value.trim();
  const box = $("#search-results");
  if (!q) { box.hidden = true; box.innerHTML = ""; return; }
  searchTimer = setTimeout(() => runSearch(q), 250);
});
async function runSearch(q) {
  const box = $("#search-results");
  try {
    const { results } = await postJSON("/api/search", { query: q, k: 8 });
    box.innerHTML = "";
    if (!results || !results.length) {
      box.innerHTML = `<div class="hit"><div class="s">Keine Treffer</div></div>`;
    } else {
      results.forEach((r) => {
        const div = document.createElement("div");
        div.className = "hit";
        div.innerHTML = `<div class="t"></div><div class="s"></div><div class="x"></div>`;
        div.querySelector(".t").textContent = r.title;
        div.querySelector(".s").textContent = `${r.score.toFixed(3)} · PDF S.${r.pdf_page_start}–${r.pdf_page_end}`;
        div.querySelector(".x").textContent = r.text;
        div.addEventListener("click", () => { box.hidden = true; loadPage(r.slug); });
        box.appendChild(div);
      });
    }
    box.hidden = false;
  } catch (e) {
    box.hidden = false;
    box.innerHTML = `<div class="hit"><div class="s">Fehler: ${escapeHtml(e.message)}</div></div>`;
  }
}

// -- chat / agent -----------------------------------------------------------

function addMsg(role, text) {
  const div = document.createElement("div");
  div.className = "msg " + role;
  div.textContent = text;
  const log = $("#chat-log");
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}
function fmtArgs(args) {
  if (!args) return "";
  return Object.entries(args)
    .map(([k, v]) => {
      let s = typeof v === "string" ? v : JSON.stringify(v);
      if (s.length > 40) s = s.slice(0, 40) + "…";
      return `${k}=${s}`;
    })
    .join(", ");
}
function renderToolCalls(container, calls) {
  if (!calls || !calls.length) return;
  const tools = document.createElement("div");
  tools.className = "tools";
  calls.forEach((c) => {
    const line = document.createElement("div");
    const isWrite = WRITE_TOOLS.has(c.name);
    line.className = "tool" + (isWrite ? " edit" : "");
    line.textContent = `${isWrite ? "✎ " : "· "}${c.name}(${fmtArgs(c.arguments)})`;
    tools.appendChild(line);
  });
  container.appendChild(tools);
}

async function sendChat(message) {
  message = (message || "").trim();
  if (!message) return;
  addMsg("user", message);
  const btn = $("#chat-send");
  btn.disabled = true;
  const bubble = addMsg("agent", "…");
  try {
    const data = await postJSON("/api/chat", { message });
    bubble.textContent = data.reply || "(keine Antwort)";
    const calls = data.tool_calls || [];
    renderToolCalls(bubble, calls);
    const writes = calls.filter((c) => WRITE_TOOLS.has(c.name));
    if (writes.length) {
      await refreshNav();
      const touched = writes.map((c) => c.arguments && c.arguments.slug);
      if (touched.includes(state.currentSlug)) loadPage(state.currentSlug);
      else {
        const created = writes.find((c) => c.name === "create_page" && c.arguments && c.arguments.slug);
        if (created) loadPage(created.arguments.slug);
      }
    }
  } catch (err) {
    bubble.textContent = "Fehler: " + err.message;
  } finally {
    btn.disabled = false;
    $("#chat-input").focus();
  }
}

$("#chat-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = $("#chat-input");
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  sendChat(message);
});
$("#chat-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#chat-form").requestSubmit(); }
});

document.querySelectorAll(".tab").forEach((b) => {
  b.addEventListener("click", () => activateTab(b.dataset.tab));
});

async function refreshNav() {
  state.manifest = await getJSON("/api/wiki");
  state.pages = {};
  renderNav();
}

// -- boot -------------------------------------------------------------------

async function init() {
  try {
    state.manifest = await getJSON("/api/wiki");
    $("#doc-title").textContent = state.manifest.title || "";
    const pages = state.manifest.pages || [];
    state.firstSlug = pages.length ? pages[0].slug : null;
    renderNav();
    const hash = location.hash.replace(/^#/, "");
    const start = hash && state.pages[hash] ? hash : state.firstSlug;
    if (start) loadPage(start);
    else $("#content").innerHTML = `<p class="muted">Keine Seiten gefunden.</p>`;
  } catch (e) {
    $("#content").innerHTML = `<p class="muted">Konnte Wiki nicht laden: ${escapeHtml(e.message)}</p>`;
  }
}
init();
