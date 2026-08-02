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
const REL_COLOR = { center: "#3b5bdb", parent: "#7048e8", child: "#7048e8",
                    prev: "#868e96", next: "#868e96", similar: "#2f9e44",
                    references: "#e8590c", referenced_by: "#e8590c" };
const REL_LABEL = { parent: "Übergeordnet", child: "Unterseite",
                    prev: "Vorherige", next: "Nächste",
                    references: "Verweist auf", referenced_by: "Verwiesen von",
                    similar: "Ähnlich" };

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
    drawGraph(content, data);
  } catch (e) {
    content.innerHTML = `<p class="muted">Graph nicht verfügbar: ${escapeHtml(e.message)}` +
      `<br><span class="muted">Erzeuge ihn mit <code>openwiki graph-build</code>.</span></p>`;
  }
}

function drawGraph(content, data) {
  content.innerHTML = "";
  const center = data.nodes.find((n) => n.rel === "center") || { title: data.center };

  const bar = document.createElement("div");
  bar.className = "graph-bar";
  const title = document.createElement("strong");
  title.textContent = center.title;
  const open = document.createElement("button");
  open.className = "graph-open";
  open.textContent = "Seite öffnen →";
  open.addEventListener("click", () => loadPage(data.center));
  bar.append(title, open);

  const legend = document.createElement("div");
  legend.className = "graph-legend";
  for (const [rel, label] of Object.entries(REL_LABEL)) {
    const s = document.createElement("span");
    s.innerHTML = `<i style="background:${REL_COLOR[rel]}"></i>${label}`;
    legend.appendChild(s);
  }

  const W = 820, H = 560, cx = W / 2, cy = H / 2, R = Math.min(cx, cy) - 90;
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, class: "graph-svg" });
  const others = data.nodes.filter((n) => n.rel !== "center");
  const pos = { [data.center]: [cx, cy] };
  others.forEach((n, i) => {
    const a = (2 * Math.PI * i) / Math.max(1, others.length) - Math.PI / 2;
    pos[n.slug] = [cx + R * Math.cos(a), cy + R * Math.sin(a)];
  });

  data.edges.forEach((e) => {
    const [x1, y1] = pos[e.source] || [cx, cy];
    const [x2, y2] = pos[e.target] || [cx, cy];
    svg.appendChild(svgEl("line", {
      x1, y1, x2, y2, stroke: REL_COLOR[e.type] || "#ccc",
      "stroke-width": e.type === "similar" ? 1.5 : 2.5, "stroke-opacity": 0.45,
    }));
  });

  data.nodes.forEach((n) => {
    const [x, y] = pos[n.slug];
    const isCenter = n.rel === "center";
    const g = svgEl("g", { class: "graph-node", transform: `translate(${x},${y})` });
    g.appendChild(svgEl("circle", {
      r: isCenter ? 13 : 9, fill: REL_COLOR[n.rel] || "#adb5bd",
      stroke: "#fff", "stroke-width": 2,
    }));
    const label = svgEl("text", { y: isCenter ? -19 : -15, "text-anchor": "middle", class: "graph-label" });
    label.textContent = n.title.length > 26 ? n.title.slice(0, 26) + "…" : n.title;
    g.appendChild(label);
    const tip = svgEl("title", {});
    tip.textContent = `${n.title} — ${isCenter ? "aktuell" : REL_LABEL[n.rel] || n.rel}`;
    g.appendChild(tip);
    g.addEventListener("click", () => (isCenter ? loadPage(n.slug) : recenterGraph(n.slug)));
    svg.appendChild(g);
  });

  content.append(bar, legend, svg);
  content.scrollTop = 0;
}

// Re-center the graph on a neighbor without leaving the Graph tab.
async function recenterGraph(slug) {
  state.currentSlug = slug;
  setActive(slug);
  history.replaceState(null, "", "#" + slug);
  try {
    const { markdown } = await getJSON("/api/pages/" + encodeURIComponent(slug));
    state.wikiMarkdown = markdown;  // keep the Wiki tab in sync for when they open it
  } catch (_) { /* non-fatal */ }
  renderGraph();
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
