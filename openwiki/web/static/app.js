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
  stopMetricsPoll();  // stop the System-tab metrics poll when switching away
  if (state.tab === "wiki") {
    content.innerHTML = state.wikiMarkdown
      ? marked.parse(state.wikiMarkdown)
      : `<p class="muted">Keine Seite ausgewählt.</p>`;
    interceptLinks();
    content.scrollTop = 0;
  } else if (state.tab === "graph") {
    renderGraph();
  } else if (state.tab === "project") {
    renderProject();
  } else if (state.tab === "eval") {
    renderEval();
  } else if (state.tab === "memory") {
    renderMemory();
  } else if (state.tab === "system") {
    renderSystem();
  } else if (state.tab === "analyse") {
    renderAnalyse();
  } else if (state.tab === "entities") {
    renderEntities();
  } else {
    renderDoc(state.tab);
  }
}

// -- System / observability tab ---------------------------------------------

function fmtMs(ms) {
  if (ms == null) return "";
  return ms >= 1000 ? (ms / 1000).toFixed(2) + " s" : Math.round(ms) + " ms";
}
function startMetricsPoll(fn) { stopMetricsPoll(); state.metricsTimer = setInterval(fn, 2000); }
function stopMetricsPoll() { if (state.metricsTimer) { clearInterval(state.metricsTimer); state.metricsTimer = 0; } }

const KIND_LABEL = { chat: "Chat-Modell", embed: "Embeddings", http: "API-Anfragen" };

function renderMetrics(data) {
  const sum = data.summary || {};
  const kinds = Object.keys(sum);
  const cards = kinds.length ? kinds.map((k) => {
    const s = sum[k];
    const tok = (s.eval_tokens || s.prompt_tokens)
      ? `<div class="m-row"><span>Tokens</span><b>${s.prompt_tokens} ein / ${s.eval_tokens} aus</b></div>` : "";
    return `<div class="m-card"><div class="m-card-h">${KIND_LABEL[k] || k}</div>
      <div class="m-row"><span>Aufrufe</span><b>${s.count}</b></div>
      <div class="m-row"><span>p50 / p95</span><b>${fmtMs(s.p50_ms)} / ${fmtMs(s.p95_ms)}</b></div>
      <div class="m-row"><span>Gesamtzeit</span><b>${fmtMs(s.total_ms)}</b></div>${tok}</div>`;
  }).join("") : `<p class="muted">Noch keine Aktivität — stelle dem Agenten eine Frage oder durchsuche das Wiki.</p>`;
  const rows = (data.events || []).map((e) => {
    const tps = e.tokens_per_sec ? `${e.tokens_per_sec} tok/s` : "";
    let tok = "";
    if (e.eval_tokens != null) tok = `${e.prompt_tokens ?? 0} / ${e.eval_tokens}`;
    else if (e.prompt_tokens != null) tok = `${e.prompt_tokens} / –`;
    const when = new Date(e.t * 1000).toLocaleTimeString();
    return `<tr><td class="num">${when}</td><td><span class="m-kind ${e.kind}">${e.kind}</span></td>
      <td class="m-name">${escapeHtml(e.name)}</td><td class="num">${fmtMs(e.duration_ms)}</td>
      <td class="num">${tok}</td><td class="num">${tps}</td></tr>`;
  }).join("");
  return `<div class="m-head"><strong>System &amp; Observability</strong>
      <span class="muted">Live-Telemetrie · ${data.total_events || 0} Ereignisse im Puffer · alle 2 s aktualisiert</span></div>
    <div class="m-cards">${cards}</div>
    <h3 class="m-h3">Letzte Ereignisse</h3>
    <table class="m-table"><thead><tr><th>Zeit</th><th>Art</th><th>Name</th><th>Dauer</th><th>Tokens (ein/aus)</th><th>Rate</th></tr></thead>
    <tbody>${rows || `<tr><td colspan="6" class="muted">—</td></tr>`}</tbody></table>`;
}

async function renderSystem() {
  const content = $("#content");
  content.innerHTML = `<div id="sys"><p class="muted">Wird geladen…</p></div>`;
  const draw = async () => {
    let data;
    try {
      data = await getJSON("/api/metrics?limit=60");
    } catch (e) {
      const sys = $("#sys");
      if (sys) sys.innerHTML = `<p class="muted">Fehler: ${escapeHtml(e.message)}</p>`;
      return;
    }
    const sys = $("#sys");
    if (sys) sys.innerHTML = renderMetrics(data);   // null if the tab was switched away mid-fetch
  };
  await draw();
  startMetricsPoll(draw);
}

// -- Analyse tab: world-model (graph↔semantic) coupling ---------------------

// Edge kinds on the semantic map, with a fixed colour + label. references/relation
// (the non-semantic "reach" edges) are shown by default; the bulkier / redundant ones
// (similar, shared-entity, structural) are opt-in toggles.
const AN_KINDS = [
  { key: "references",    label: "REFERENCES",    color: "#ffa94d", on: true },
  { key: "relation",      label: "RELATED_TO",    color: "#da77f2", on: true },
  { key: "shared_entity", label: "shared-entity", color: "#adb5bd", on: false },
  { key: "similar",       label: "SIMILAR_TO",    color: "#4dabf7", on: false },
  { key: "child_of",      label: "CHILD_OF",      color: "#63e6be", on: false },
  { key: "next",          label: "NEXT",          color: "#82c91e", on: false },
];
const AN_LABEL = Object.fromEntries(AN_KINDS.map((k) => [k.key, k.label]));
const analyse = { data: null, colorMap: {}, show: {}, colorByTheme: true };
AN_KINDS.forEach((k) => (analyse.show[k.key] = k.on));

function analyseUnavailable(data) {
  const why = data.reason === "no_index"
    ? `Kein Suchindex geladen — starte den Server mit <code>-i/--index</code> (oder baue ihn mit <code>openwiki index</code>).`
    : `Kein Wissensgraph geladen — starte den Server mit <code>--graph</code> (oder baue ihn mit <code>openwiki graph-build</code>).`;
  return `<div class="an-head"><strong>Weltmodell-Analyse</strong></div><p class="muted">${why}</p>`;
}

function analyseMetrics(c) {
  const prof = c.edge_profile || {}, ov = c.neighbor_overlap || {};
  const order = ["similar", "references", "shared_entity", "relation", "child_of", "next"];
  const rows = order.map((k) => {
    const p = prof[k] || {};
    const ovs = ov[k] == null ? "—" : ov[k].toFixed(2);
    if (!p.n) return `<tr class="an-dim"><td>${AN_LABEL[k]}</td><td class="num">0</td>
      <td class="num">—</td><td class="num">—</td><td class="num">${ovs}</td></tr>`;
    const lift = (p.lift >= 0 ? "+" : "") + p.lift.toFixed(3);
    return `<tr><td>${AN_LABEL[k]}</td><td class="num">${p.n}</td>
      <td class="num">${p.mean.toFixed(3)}</td><td class="num">${lift}</td><td class="num">${ovs}</td></tr>`;
  }).join("");
  const nul = prof._null || {};
  const coh = c.community_coherence || {};
  const cohLine = coh.available
    ? `Silhouette <b>${coh.silhouette >= 0 ? "+" : ""}${coh.silhouette.toFixed(3)}</b> · ARI vs. k-means <b>${coh.ari >= 0 ? "+" : ""}${coh.ari.toFixed(3)}</b> <span class="muted">(${coh.communities} Communities)</span>`
    : `<span class="muted">n/a — ${escapeHtml(coh.reason || "nicht verfügbar")}</span>`;
  const r = c.graph_reach || {};
  let headline;
  if (r.non_semantic_fraction == null) {
    headline = `<p class="muted">Keine Nicht-Ähnlichkeitskanten (REFERENCES/shared-entity/RELATED_TO) —
      baue Entitäten/Relationen (<code>graph-build --relations</code>) für die Reichweiten-Kennzahl.</p>`;
  } else {
    headline = `<div class="an-big">${Math.round(r.non_semantic_fraction * 100)}%</div>
      <p>der <b>${r.pairs}</b> Nicht-Ähnlichkeitsverbindungen des Graphen verknüpfen Seiten, die der
      Embedder <b>nicht</b> als Nachbarn einstufen würde (Cosinus ≤ Median eines Zufallspaars,
      ${r.null_median.toFixed(3)}). → so viel Struktur kodiert der Graph, die reine Ähnlichkeit verpasst.</p>`;
  }
  return `<div class="an-metrics">
    <div class="an-panel">
      <h3>Kanten im semantischen Raum</h3>
      <table class="m-table an-tbl"><thead><tr><th>Kantentyp</th><th class="num">n</th>
        <th class="num">Cos(⌀)</th><th class="num">vs. Null</th><th class="num">kNN-Overlap</th></tr></thead>
        <tbody>${rows}</tbody></table>
      <p class="muted an-note">Zufallspaar-Cosinus ≈ ${(nul.mean || 0).toFixed(3)} · SIMILAR_TO ist der Anker
        (Cosinus-abgeleitet); der Abstand nach unten ist die nicht-semantische Reichweite eines Kantentyps.</p>
      <div class="an-coh">Community-Kohärenz: ${cohLine}</div>
    </div>
    <div class="an-panel an-headline">
      <h3>Graph-Reichweite</h3>${headline}
    </div>
  </div>`;
}

function analyseControls(data) {
  const toggles = AN_KINDS.map((k) => {
    const n = (data.edges[k.key] || []).length;
    return `<label class="an-tog"><input type="checkbox" data-edge="${k.key}"
      ${analyse.show[k.key] ? "checked" : ""} ${n ? "" : "disabled"}>
      <span class="an-sw" style="background:${k.color}"></span>${k.label}
      <span class="muted">(${n})</span></label>`;
  }).join("");
  const legend = (data.communities || []).map((cm) =>
    `<span class="an-leg"><span class="an-sw" style="background:${analyse.colorMap[cm.id]}"></span>${escapeHtml(cm.label || ("#" + cm.id))}</span>`
  ).join("");
  const themeTog = (data.communities || []).length
    ? `<label class="an-tog"><input type="checkbox" id="an-theme" ${analyse.colorByTheme ? "checked" : ""}>
        Themenfarben</label>` : "";
  return `<div class="an-controls">
      <div class="an-toggles">${toggles}</div>
      ${themeTog}
    </div>
    <div class="an-legend">${legend}</div>`;
}

function analyseLayout(data) {
  return `<div class="an-head"><strong>Weltmodell-Analyse</strong>
      <span class="muted">Graph ↔ semantischer Raum · ${data.projection.points.length} Seiten ·
      Projektion: ${data.projection.method.toUpperCase()}</span></div>
    ${analyseMetrics(data.coupling)}
    <h3 class="an-maph">Semantische Karte <span class="muted">— Seiten im Embedding-Raum, Graphkanten überlagert</span></h3>
    ${analyseControls(data)}
    <div id="an-map" class="an-map"></div>`;
}

function anPointColor(community) {
  if (analyse.colorByTheme && community != null && analyse.colorMap[community]) return analyse.colorMap[community];
  return "#4dabf7";
}

function drawSemanticMap() {
  const host = $("#an-map");
  if (!host || !analyse.data) return;
  const data = analyse.data, pad = 26;
  const pts = data.projection.points;
  const svg = svgEl("svg", { viewBox: `0 0 ${GW} ${GH}`, class: "an-svg" });
  const px = (x) => pad + x * (GW - 2 * pad);
  const py = (y) => pad + (1 - y) * (GH - 2 * pad);   // flip: y up
  const at = {};
  pts.forEach((p) => (at[p.slug] = p));
  // edges first (under the points), only the toggled kinds
  AN_KINDS.forEach((k) => {
    if (!analyse.show[k.key]) return;
    (data.edges[k.key] || []).forEach(([a, b]) => {
      const pa = at[a], pb = at[b];
      if (!pa || !pb) return;
      svg.appendChild(svgEl("line", {
        x1: px(pa.x), y1: py(pa.y), x2: px(pb.x), y2: py(pb.y),
        stroke: k.color, "stroke-width": 1, "stroke-opacity": 0.35,
      }));
    });
  });
  // points on top
  pts.forEach((p) => {
    const c = svgEl("circle", {
      cx: px(p.x), cy: py(p.y), r: 5, fill: anPointColor(p.community),
      stroke: "#1b1e24", "stroke-width": 1, class: "an-node", "data-slug": p.slug,
    });
    const t = svgEl("title", {});
    t.textContent = p.title + (p.community != null ? "  ·  Community " + p.community : "");
    c.appendChild(t);
    svg.appendChild(c);
  });
  svg.addEventListener("click", (e) => {
    const slug = e.target && e.target.getAttribute && e.target.getAttribute("data-slug");
    if (slug) loadPage(slug);
  });
  host.innerHTML = "";
  host.appendChild(svg);
}

function wireAnalyse() {
  document.querySelectorAll('#an input[data-edge]').forEach((cb) =>
    cb.addEventListener("change", () => { analyse.show[cb.dataset.edge] = cb.checked; drawSemanticMap(); }));
  const theme = $("#an-theme");
  if (theme) theme.addEventListener("change", () => { analyse.colorByTheme = theme.checked; drawSemanticMap(); });
}

const AN_VIEWS = [
  { key: "coupling", label: "Kopplung" },
  { key: "gaps", label: "Lücken" },
  { key: "memory", label: "Dynamik" },
];

function renderAnalyse() {
  const content = $("#content");
  if (!analyse.view) analyse.view = "coupling";
  content.innerHTML =
    `<div class="an-subtabs">` +
    AN_VIEWS.map((v) => `<button class="an-subtab" data-view="${v.key}">${v.label}</button>`).join("") +
    `</div><div id="an-view"><p class="muted">Wird geladen…</p></div>`;
  document.querySelectorAll(".an-subtab").forEach((b) =>
    b.addEventListener("click", () => { analyse.view = b.dataset.view; renderAnalyseView(); }));
  renderAnalyseView();
}

function renderAnalyseView() {
  document.querySelectorAll(".an-subtab").forEach((b) => b.classList.toggle("active", b.dataset.view === analyse.view));
  if (analyse.view === "gaps") return renderGaps();
  if (analyse.view === "memory") return renderDynamics();
  return renderCoupling();
}

async function renderCoupling() {
  const host = $("#an-view");
  host.innerHTML = `<p class="muted">Kopplung wird berechnet…</p>`;
  let data;
  try {
    data = await getJSON("/api/analyze");
  } catch (e) {
    if ($("#an-view")) $("#an-view").innerHTML = `<p class="muted">Fehler: ${escapeHtml(e.message)}</p>`;
    return;
  }
  if (!$("#an-view") || analyse.view !== "coupling") return;   // switched away mid-fetch
  if (!data.available) { $("#an-view").innerHTML = analyseUnavailable(data); return; }
  analyse.data = data;
  analyse.colorMap = {};
  (data.communities || []).forEach((cm) => (analyse.colorMap[cm.id] = COMMUNITY_PALETTE[cm.id % COMMUNITY_PALETTE.length]));
  analyse.colorByTheme = (data.communities || []).length > 0;
  $("#an-view").innerHTML = analyseLayout(data);
  wireAnalyse();
  drawSemanticMap();
}

// -- Analyse · Lücken (P3 gap-mining) ---------------------------------------

function gapPage(slug, title) {
  return `<button class="gap-page" data-slug="${escapeHtml(slug)}" title="Seite öffnen">${escapeHtml(title || slug)}</button>`;
}

function gapsLayout(d) {
  const rows = (arr, fn) => (arr && arr.length ? arr.map(fn).join("") : `<p class="muted">keine</p>`);
  const links = rows(d.link_candidates, (c) =>
    `<div class="gap-row"><span class="gap-metric">${c.shared_entities} geteilt · cos ${c.cosine.toFixed(2)}</span>` +
    `${gapPage(c.a, c.a_title)} <span class="muted">↔</span> ${gapPage(c.b, c.b_title)}</div>`);
  const red = (d.redundant_pages && d.redundant_pages.length)
    ? d.redundant_pages.map((c) =>
        `<div class="gap-row"><span class="gap-metric">cos ${c.cosine.toFixed(3)}</span>` +
        `${gapPage(c.a, c.a_title)} <span class="muted">↔</span> ${gapPage(c.b, c.b_title)}</div>`).join("")
    : `<p class="muted">keine über dem Schwellwert</p>`;
  const iso = d.isolated_pages || {};
  const outliers = rows(iso.semantic_outliers, (p) =>
    `<div class="gap-row"><span class="gap-metric">nn-cos ${p.nn_cosine.toFixed(3)}</span> ${gapPage(p.slug, p.title)}</div>`);
  const orphans = rows(iso.structural_orphans, (o) => `<div class="gap-row">${gapPage(o.slug, o.title)}</div>`);
  const ents = rows(d.entity_merge_candidates, (e) =>
    `<div class="gap-row"><span class="gap-metric">sim ${e.similarity.toFixed(2)} · ${escapeHtml(e.type)}</span>` +
    `<b>${escapeHtml(e.a)}</b> <span class="muted">≈</span> <b>${escapeHtml(e.b)}</b></div>`);
  return `<div class="an-head"><strong>Lücken &amp; Hygiene</strong> ` +
    `<span class="muted">— umsetzbare Verbesserungsvorschläge (nur lesend)</span></div>` +
    `<div class="gap-panel"><h3>Fehlende Querverweise <span class="muted">(teilen Begriffe, zitieren sich aber nicht)</span></h3>${links}</div>` +
    `<div class="gap-panel"><h3>Beinahe-Duplikate <span class="muted">(sehr ähnliche Einbettungen)</span></h3>${red}</div>` +
    `<div class="gap-panel"><h3>Isolierte Seiten</h3>` +
    `<div class="muted an-note">semantische Ausreißer (nächster Nachbar ist fern):</div>${outliers}` +
    `<div class="muted an-note">strukturelle Waisen (keine Ähnlich-/Verweis-/Begriffskante):</div>${orphans}</div>` +
    `<div class="gap-panel"><h3>Zusammenführbare Begriffe <span class="muted">(gleicher Typ, ähnliche Namen — Kandidaten für <code>--resolve-entities</code>)</span></h3>${ents}</div>`;
}

async function renderGaps() {
  const host = $("#an-view");
  host.innerHTML = `<p class="muted">Lücken werden ermittelt…</p>`;
  let d;
  try {
    d = await getJSON("/api/analyze/gaps");
  } catch (e) {
    if ($("#an-view")) $("#an-view").innerHTML = `<p class="muted">Fehler: ${escapeHtml(e.message)}</p>`;
    return;
  }
  if (!$("#an-view") || analyse.view !== "gaps") return;
  if (!d.available) { $("#an-view").innerHTML = analyseUnavailable(d); return; }
  $("#an-view").innerHTML = gapsLayout(d);
  document.querySelectorAll("#an-view .gap-page").forEach((el) =>
    el.addEventListener("click", () => loadPage(el.dataset.slug)));
}

// -- Analyse · Dynamik (P4 memory-tier dynamics) ----------------------------

function dynamicsLayout(d) {
  const c = d.counts, rev = d.revision, con = d.consolidation, t = d.temperature, b = d.breadth;
  const total = (t.hot + t.warm + t.cold) || 1;
  const seg = (n, cls) => n ? `<span class="temp-seg ${cls}" style="width:${(100 * n / total).toFixed(1)}%" title="${n}"></span>` : "";
  const tops = (b.top_predicates || []).map((p) => `${escapeHtml(p.predicate)}×${p.count}`).join(", ");
  const peak = Math.max(1, ...(d.growth || []).map((g) => g.facts));
  const growth = (d.growth || []).map((g) =>
    `<div class="gap-row"><span class="gap-metric">${escapeHtml(g.session_id)}</span>` +
    `<span class="grow-bar" style="width:${Math.max(4, Math.round(140 * g.facts / peak))}px"></span> ${g.facts}</div>`).join("") ||
    `<p class="muted">—</p>`;
  return `<div class="an-head"><strong>Gedächtnis-Dynamik</strong> ` +
    `<span class="muted">— ${c.sessions} Sitzungen · ${c.current} aktuelle Fakten (${c.superseded} überholt) · ${c.themes} Themen</span></div>` +
    `<div class="an-metrics">` +
    `<div class="an-panel"><h3>Revision & Konsolidierung</h3>` +
    `<div class="m-row"><span>Überschrieben (superseded)</span><b>${(rev.revision_rate * 100).toFixed(0)}% · ${rev.superseded}</b></div>` +
    `<div class="m-row"><span>In Themen konsolidiert</span><b>${(con.coverage * 100).toFixed(0)}% · ⌀ ${con.avg_theme_size}/Thema</b></div>` +
    `<div class="m-row"><span>Themengrößen</span><b>${con.theme_sizes.min}–${con.theme_sizes.max}</b></div></div>` +
    `<div class="an-panel"><h3>Temperatur <span class="muted">(heiß = jung/oft bestätigt)</span></h3>` +
    `<div class="temp-bar">${seg(t.hot, "hot")}${seg(t.warm, "warm")}${seg(t.cold, "cold")}</div>` +
    `<div class="muted an-note">${t.hot} heiß · ${t.warm} warm · ${t.cold} kalt · Halbwertszeit ${t.half_life_days}d</div>` +
    `<div class="m-row"><span>⌀ Konfidenz</span><b>${t.mean_confidence}</b></div>` +
    `<div class="m-row"><span>Wiederbestätigt (&gt;1)</span><b>${(t.reaffirmed_fraction * 100).toFixed(0)}%</b></div></div></div>` +
    `<div class="gap-panel"><h3>Breite</h3><div class="muted">${b.distinct_subjects} Subjekte · ${b.distinct_predicates} Prädikate` +
    `${tops ? " · top: " + tops : ""}</div></div>` +
    `<div class="gap-panel"><h3>Wachstum <span class="muted">(Fakten je Sitzung, älteste zuerst)</span></h3>${growth}</div>`;
}

async function renderDynamics() {
  const host = $("#an-view");
  host.innerHTML = `<p class="muted">Gedächtnis-Dynamik wird berechnet…</p>`;
  let d;
  try {
    d = await getJSON("/api/analyze/memory");
  } catch (e) {
    if ($("#an-view")) $("#an-view").innerHTML = `<p class="muted">Fehler: ${escapeHtml(e.message)}</p>`;
    return;
  }
  if (!$("#an-view") || analyse.view !== "memory") return;
  if (!d.available) {
    $("#an-view").innerHTML = `<div class="an-head"><strong>Gedächtnis-Dynamik</strong></div>` +
      `<p class="muted">Kein Gedächtnis-Tier — im <b>Second-Brain-Modus</b> mit <code>openwiki remember</code> ` +
      `erfassten Sitzungen wird es gefüllt (siehe Reiter <b>Gedächtnis</b>).</p>`;
    return;
  }
  $("#an-view").innerHTML = dynamicsLayout(d);
}

// -- Begriffe (entity / concept browser) ------------------------------------

const entState = { types: [], hasRelations: false, q: "", type: "", timer: 0 };

function entUnavailable(reason) {
  const msg = reason === "no_graph"
    ? `Kein Wissensgraph geladen — starte den Server mit <code>--graph</code>.`
    : `Der Graph enthält keine Begriffe — baue ihn mit <code>graph-build --entities</code> ` +
      `(kanonisch + Aliasse mit <code>--resolve-entities</code>).`;
  return `<div class="ent-head"><strong>Begriffe</strong></div><p class="muted">${msg}</p>`;
}

function entCard(e) {
  const n = (e.aliases || []).length;
  const al = n ? ` · ${n} Alias${n > 1 ? "se" : ""}` : "";
  return `<button class="ent-card" data-name="${escapeHtml(e.name)}">` +
    `<span class="ent-name">${escapeHtml(e.name)}</span>` +
    `<span class="ent-meta"><span class="ent-type">${escapeHtml(e.type)}</span> ${e.mentions}×${al}</span></button>`;
}

function renderEntList(data) {
  const list = $("#ent-list");
  if (!list) return;
  const items = data.entities || [];
  list.innerHTML = items.length ? items.map(entCard).join("") : `<p class="muted">keine Treffer</p>`;
  list.querySelectorAll(".ent-card").forEach((el) =>
    el.addEventListener("click", () => {
      list.querySelectorAll(".ent-card").forEach((c) => c.classList.remove("active"));
      el.classList.add("active");
      loadEntity(el.dataset.name);
    }));
}

async function fetchEntities() {
  const list = $("#ent-list");
  if (list) list.innerHTML = `<p class="muted">Lädt…</p>`;
  try {
    const qs = `?q=${encodeURIComponent(entState.q)}&type=${encodeURIComponent(entState.type)}`;
    const data = await getJSON("/api/entities" + qs);
    if (state.tab !== "entities" || !data.available) return;
    entState.hasRelations = data.has_relations;
    renderEntList(data);
  } catch (e) {
    if (list) list.innerHTML = `<p class="muted">Fehler: ${escapeHtml(e.message)}</p>`;
  }
}

function entRelRow(r) {
  const arrow = r.direction === "out" ? "→" : "←";
  return `<div class="gap-row"><span class="gap-metric">${escapeHtml(r.predicate)} <span class="muted">(${r.weight})</span></span>` +
    `<span class="muted">${arrow}</span> <button class="ent-rel" data-name="${escapeHtml(r.other)}">${escapeHtml(r.other)}</button></div>`;
}

function renderEntDetail(d) {
  const detail = $("#ent-detail");
  if (!detail) return;
  const desc = d.description ? `<p class="ent-desc">${escapeHtml(d.description)}</p>` : "";
  const aliases = (d.aliases || []).length
    ? `<div class="ent-aliases">${d.aliases.map((a) => `<span class="ent-alias">${escapeHtml(a)}</span>`).join("")}</div>` : "";
  const pages = (d.pages || []).map((p) =>
    `<button class="gap-page" data-slug="${escapeHtml(p.slug)}">${escapeHtml(p.title || p.slug)}</button>`).join("") || `<span class="muted">—</span>`;
  const rels = (d.relations || []).length
    ? d.relations.map(entRelRow).join("") : `<p class="muted">keine typisierten Beziehungen</p>`;
  detail.innerHTML =
    `<div class="ent-dhead"><h3>${escapeHtml(d.name)}</h3> <span class="ent-type">${escapeHtml(d.type)}</span> ` +
    `<span class="muted">${d.mentions}× erwähnt</span></div>${desc}${aliases}` +
    `<h4>Erwähnt auf ${(d.pages || []).length} Seite(n)</h4><div class="ent-pages">${pages}</div>` +
    `<h4>Beziehungen</h4><div class="ent-rels">${rels}</div>`;
  detail.querySelectorAll(".gap-page").forEach((el) => el.addEventListener("click", () => loadPage(el.dataset.slug)));
  detail.querySelectorAll(".ent-rel").forEach((el) => el.addEventListener("click", () => loadEntity(el.dataset.name)));
}

async function loadEntity(name) {
  const detail = $("#ent-detail");
  if (detail) detail.innerHTML = `<p class="muted">Lädt…</p>`;
  try {
    renderEntDetail(await getJSON("/api/entity/" + encodeURIComponent(name)));
  } catch (e) {
    if (detail) detail.innerHTML = `<p class="muted">Fehler: ${escapeHtml(e.message)}</p>`;
  }
}

async function renderEntities() {
  const content = $("#content");
  content.innerHTML = `<p class="muted">Begriffe werden geladen…</p>`;
  let data;
  try {
    data = await getJSON(`/api/entities?q=${encodeURIComponent(entState.q)}&type=${encodeURIComponent(entState.type)}`);
  } catch (e) {
    content.innerHTML = `<p class="muted">Fehler: ${escapeHtml(e.message)}</p>`;
    return;
  }
  if (state.tab !== "entities") return;
  if (!data.available) { content.innerHTML = entUnavailable(data.reason); return; }
  entState.types = data.types || [];
  entState.hasRelations = data.has_relations;
  const typeOpts = `<option value="">alle Typen</option>` + entState.types.map((t) =>
    `<option value="${escapeHtml(t)}"${t === entState.type ? " selected" : ""}>${escapeHtml(t)}</option>`).join("");
  content.innerHTML =
    `<div class="ent-head"><strong>Begriffe</strong> <span class="muted">— kanonische Entitäten aus dem ` +
    `Wissensgraphen (Auflösung + Beziehungen)</span></div>` +
    `<div class="ent-controls"><input id="ent-search" type="search" placeholder="Begriff oder Alias suchen…" ` +
    `value="${escapeHtml(entState.q)}"><select id="ent-type">${typeOpts}</select></div>` +
    `<div class="ent-layout"><div id="ent-list" class="ent-list"></div>` +
    `<div id="ent-detail" class="ent-detail"><p class="muted">Wähle links einen Begriff, um Beschreibung, ` +
    `Aliasse, Seiten und Beziehungen zu sehen.</p></div></div>`;
  renderEntList(data);
  $("#ent-search").addEventListener("input", (e) => {
    entState.q = e.target.value;
    clearTimeout(entState.timer);
    entState.timer = setTimeout(fetchEntities, 250);
  });
  $("#ent-type").addEventListener("change", (e) => { entState.type = e.target.value; fetchEntities(); });
}

// -- Memory (Gedächtnis / Second Brain) tab ---------------------------------

function memReasonHtml(data) {
  if (data.reason === "no_graph")
    return `<p class="muted">Kein Wissensgraph geladen — starte den Server mit <code>--graph</code> (oder baue ihn mit <code>openwiki graph-build</code>).</p>`;
  if (data.mode)   // reason "empty", Second Brain mode on
    return `<p class="muted"><b>Second-Brain-Modus aktiv</b>, aber noch keine Sitzungen erfasst.<br>
      Erfasse eine Sitzung mit <code>openwiki remember &lt;transkript&gt;</code> — sie erscheint dann hier.</p>`;
  return `<p class="muted"><b>Wiki-Modus</b> — das Gedächtnis (Path B) ist deaktiviert.<br>
    Aktiviere den Second-Brain-Modus mit <code>[memory] enabled = true</code> in <code>openwiki.toml</code>.</p>`;
}

function memFactRow(f) {
  const badges = [];
  if (f.superseded) badges.push(`<span class="mem-badge sup">überholt</span>`);
  if (f.confidence > 1) badges.push(`<span class="mem-badge conf" title="mehrfach bestätigt">×${f.confidence}</span>`);
  const sc = (f.score != null) ? `<span class="mem-score" title="Relevanz (cos ${f.cos})">${f.score}</span>` : "";
  return `<div class="mem-fact${f.superseded ? " is-sup" : ""}">${sc}
    <span class="mem-triple"><b>${escapeHtml(f.subject)}</b> ${escapeHtml(f.predicate)} <b>${escapeHtml(f.object)}</b></span>
    <span class="mem-src">[${escapeHtml(f.session_id || "?")}]</span> ${badges.join(" ")}</div>`;
}

function renderMemoryView(data) {
  const s = data.stats || {};
  const identity = data.identity
    ? `<div class="mem-identity"><span class="mem-k">Identität</span> ${escapeHtml(data.identity)}</div>` : "";
  const chips = `<div class="mem-chips">
    <span class="mem-chip"><b>${s.sessions || 0}</b> Sitzungen</span>
    <span class="mem-chip"><b>${s.assertions || 0}</b> Fakten</span>
    <span class="mem-chip"><b>${s.superseded || 0}</b> überholt</span>
    <span class="mem-chip"><b>${s.themes || 0}</b> Themen</span></div>`;

  const recallBox = data.has_embedder ? `
    <div class="mem-recall">
      <input id="mem-q" type="text" placeholder="Woran soll ich mich erinnern? (z. B. welche Modelle nutzen wir?)" />
      <button id="mem-recall-btn">Abrufen</button>
      <button id="mem-context-btn" class="secondary">Kontext bauen</button>
    </div>
    <div id="mem-out" class="mem-out" hidden></div>`
    : `<p class="muted">Kein Suchindex geladen — Abruf und Kontext sind nicht verfügbar (starte den Server mit <code>-i</code>).</p>`;

  const themes = (data.themes || []).length ? `
    <h3 class="mem-h3">Themen (Konsolidierung)</h3>
    <div class="mem-themes">${data.themes.map((t) => `
      <div class="mem-theme"><div class="mem-theme-h">${escapeHtml(t.label || "Thema " + t.id)}
        <span class="muted">· ${t.size} Fakten</span></div>
        <div class="mem-theme-s">${escapeHtml(t.summary || "")}</div></div>`).join("")}</div>` : "";

  const rows = (data.assertions || []).map((f) => `
    <tr class="${f.superseded ? "sup" : ""}">
      <td><b>${escapeHtml(f.subject)}</b></td><td>${escapeHtml(f.predicate)}</td>
      <td><b>${escapeHtml(f.object)}</b></td><td class="m-name">${escapeHtml(f.session_id || "")}</td>
      <td class="num">${f.confidence}</td>
      <td>${f.superseded ? '<span class="mem-badge sup">überholt</span>' : ""}</td></tr>`).join("");

  return `<div class="mem-head"><strong>Gedächtnis</strong>
      <span class="muted">Path B · Second Brain — was frühere Sitzungen hinterlassen haben</span></div>
    ${identity}${chips}
    <h3 class="mem-h3">Abruf &amp; Kontext</h3>
    ${recallBox}
    ${themes}
    <h3 class="mem-h3">Erinnerte Fakten
      <label class="mem-toggle"><input type="checkbox" id="mem-show-sup" /> überholte zeigen</label></h3>
    <table class="mem-table" id="mem-table"><thead><tr>
      <th>Subjekt</th><th>Prädikat</th><th>Objekt</th><th>Sitzung</th><th>Konfidenz</th><th></th></tr></thead>
      <tbody>${rows || `<tr><td colspan="6" class="muted">—</td></tr>`}</tbody></table>`;
}

async function renderMemory() {
  const content = $("#content");
  content.innerHTML = `<p class="muted">Wird geladen…</p>`;
  let data;
  try {
    data = await getJSON("/api/memory");
  } catch (e) {
    content.innerHTML = `<p class="muted">Fehler: ${escapeHtml(e.message)}</p>`;
    return;
  }
  if (!data.available) {
    content.innerHTML = `<div class="mem-head"><strong>Gedächtnis</strong></div>` + memReasonHtml(data);
    return;
  }
  content.innerHTML = renderMemoryView(data);
  wireMemory();
  content.scrollTop = 0;
}

function wireMemory() {
  const table = $("#mem-table");
  const sup = $("#mem-show-sup");
  if (sup && table) sup.addEventListener("change", () => table.classList.toggle("show-sup", sup.checked));
  const q = $("#mem-q");
  const out = $("#mem-out");
  if (!q) return;
  const run = async (mode) => {
    const query = q.value.trim();
    if (!query) return;
    out.hidden = false;
    out.innerHTML = `<p class="muted">…</p>`;
    try {
      if (mode === "context") {
        const d = await postJSON("/api/context", { query });
        out.innerHTML = `<div class="mem-ctx-h muted">Zusammengesetzter Kontext${d.budget ? " · Budget " + d.budget + " Zeichen" : ""}</div>
          <pre class="mem-context">${escapeHtml(d.context || "(leer)")}</pre>`;
      } else {
        const d = await postJSON("/api/recall", { query, k: 8 });
        const facts = d.facts || [];
        out.innerHTML = facts.length
          ? `<div class="mem-facts">${facts.map(memFactRow).join("")}</div>`
          : `<p class="muted">Keine passenden Erinnerungen.</p>`;
      }
    } catch (e) {
      out.innerHTML = `<p class="muted">Fehler: ${escapeHtml(e.message)}</p>`;
    }
  };
  $("#mem-recall-btn").addEventListener("click", () => run("recall"));
  $("#mem-context-btn").addEventListener("click", () => run("context"));
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") run("recall"); });
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
// -- project tab (manifest, sources, build status, registry) ---------------

async function loadProjectBadge() {
  try {
    const data = await getJSON("/api/project");
    const badge = $("#project-badge");
    if (data && data.project) {
      badge.textContent = "📁 " + data.project.name;
      badge.title = data.project.root;
      badge.hidden = false;
    } else {
      badge.hidden = true;
    }
  } catch (_) { /* no project / older server — ignore */ }
}

async function renderProject() {
  const content = $("#content");
  content.innerHTML = `<p class="muted">Wird geladen…</p>`;
  try {
    const data = await getJSON("/api/project");
    if (!data.project) {
      content.innerHTML = `<div class="project-view"><h2>Kein Projekt aktiv</h2>` +
        `<p class="muted">Der Server läuft ohne OpenWiki-Projekt (direkte Pfade). ` +
        `Starte <code>openwiki serve</code> in einem Projektordner (mit <code>openwiki.toml</code>) ` +
        `oder erstelle eines mit <code>openwiki init</code>.</p></div>`;
      content.scrollTop = 0;
      return;
    }
    const p = data.project;
    const esc = escapeHtml;
    const labels = { up_to_date: "aktuell", stale: "veraltet", missing: "fehlt" };
    const badge = (s) => `<span class="pbadge pbadge-${s}">${labels[s] || s}</span>`;
    const kv = (obj) => `<table class="proj-kv"><tbody>` +
      Object.entries(obj).map(([k, v]) =>
        `<tr><td><code>${esc(k)}</code></td><td><code>${esc(String(v))}</code></td></tr>`).join("") +
      `</tbody></table>`;

    const sources = p.sources.map((s) =>
      `<li>${s.exists ? "✓" : "✗"} <code>${esc(s.path)}</code>${s.exists ? "" : ' <span class="muted">(fehlt)</span>'}</li>`
    ).join("") || `<li class="muted">(keine Quellen)</li>`;

    const fmtDur = (s) => (s == null ? "" : (s >= 1 ? s.toFixed(1) + " s" : Math.round(s * 1000) + " ms"));
    const fmtLlm = (l) => (l && l.calls
      ? `${l.calls} Aufruf(e) · ${(l.prompt_tokens || 0) + (l.eval_tokens || 0)} Tokens` : "");
    const stages = p.stages.map((st) => {
      const stats = Object.keys(st.stats || {}).length
        ? `<span class="muted">${esc(JSON.stringify(st.stats))}</span>` : "";
      return `<tr><td><code>${st.name}</code></td><td>${badge(st.status)}</td>` +
             `<td class="muted">${esc(st.built || "")}</td>` +
             `<td class="num">${fmtDur(st.duration_s)}</td>` +
             `<td class="muted">${fmtLlm(st.llm)}</td><td>${stats}</td></tr>`;
    }).join("");

    const cfg = p.settings || {};
    const settingsHtml = `<div class="proj-cols">
      <div><h4>build</h4>${kv(cfg.build || {})}</div>
      <div><h4>models</h4>${kv(cfg.models || {})}</div>
      <div><h4>graph</h4>${kv(cfg.graph || {})}</div>
      <div><h4>serve</h4>${kv(cfg.serve || {})}</div>
      <div><h4>memory</h4>${kv(cfg.memory || {})}</div>
    </div>`;

    const ont = p.ontology || [];
    const ontHtml = ont.length
      ? `<ul class="proj-list">${ont.map((o) =>
          `<li><code>${esc(o.name)}</code>${o.description ? " — <span class=\"muted\">" + esc(o.description) + "</span>" : ""}</li>`).join("")}</ul>`
      : `<p class="muted">Standard-Ontologie (kein <code>entity_types</code> gesetzt).</p>`;

    const ix = p.index;
    const indexHtml = ix
      ? kv({ "Embedding-Modell": ix.model, "Dimensionen": ix.dim, "Chunks": ix.chunks })
      : `<p class="muted">Kein Index geladen.</p>`;

    let graphHtml;
    const g = p.graph;
    if (g) {
      const et = g.entity_types || [];
      const maxN = et.reduce((m, x) => Math.max(m, x.count), 0) || 1;
      const typesHtml = et.length
        ? `<div class="ent-bars">${et.map((x) =>
            `<div class="ent-bar"><span class="ent-name">${esc(x.type)}</span>` +
            `<span class="ent-track"><span class="ent-fill" style="width:${Math.round(100 * x.count / maxN)}%"></span></span>` +
            `<span class="ent-count">${x.count}</span></div>`).join("")}</div>`
        : `<p class="muted">(keine Entitäten — mit <code>entities = true</code> bauen)</p>`;
      graphHtml = `<div class="proj-cols">
        <div><h4>Knoten</h4>${kv({ Page: g.pages, Chunk: g.chunks, Entity: g.entities })}</div>
        <div><h4>Kanten</h4>${kv({
          "CHILD_OF (Hierarchie)": g.child_of, "NEXT (Reihenfolge)": g.next,
          "PART_OF (Chunk→Page)": g.part_of, "SIMILAR_TO (ähnlich)": g.similar_to,
          "REFERENCES (Verweise)": g.references, "MENTIONS (Begriffe)": g.mentions,
          "RELATED_TO (Beziehungen)": g.relations })}</div>
      </div>
      <h4>Entitätstypen (Verteilung)</h4>${typesHtml}`;
    } else {
      graphHtml = `<p class="muted">Kein Graph geladen (mit <code>openwiki build</code> erzeugen).</p>`;
    }

    const comms = p.communities || [];
    const commHtml = comms.length
      ? `<div class="comm-list">${comms.map((c) =>
          `<div class="comm-card"><div class="comm-head">` +
          `<span class="comm-label">${esc(c.label)}</span>` +
          `<span class="comm-size">${c.size} Seiten</span></div>` +
          `<p class="comm-summary">${esc(c.summary || "")}</p></div>`).join("")}</div>`
      : `<p class="muted">Keine Communities — mit <code>openwiki communities</code> erzeugen ` +
        `(ermöglicht <code>openwiki ask --global</code>).</p>`;
    // Global search: a thematic question answered from the community summaries.
    const askBox = comms.length ? `<div class="global-ask">
        <div class="global-ask-row">
          <input id="global-q" type="text" autocomplete="off"
                 placeholder="Thematische Frage über das gesamte Wissen…" />
          <button id="global-ask-btn">Global fragen</button>
        </div>
        <p class="muted global-hint">Beantwortet aus den Themen-Zusammenfassungen oben (ein Chat-Aufruf).</p>
        <div id="global-result" class="global-result"></div>
      </div>` : "";

    const registry = (data.registry || []).map((r) =>
      `<li>${r.active ? "★" : "•"} <code>${esc(r.name)}</code> <span class="muted">${esc(r.path)}</span></li>`
    ).join("") || `<li class="muted">(keine registrierten Projekte)</li>`;

    content.innerHTML = `<div class="project-view">
      <h2>📁 ${esc(p.name)}</h2>
      ${p.description ? `<p>${esc(p.description)}</p>` : ""}
      <p class="muted"><code>${esc(p.root)}</code></p>

      <h3>Quellen <span class="muted">(${p.sources.length})</span></h3>
      <ul class="proj-list">${sources}</ul>

      <h3>Build-Status</h3>
      <table class="proj-table">
        <thead><tr><th>Stufe</th><th>Status</th><th>Gebaut</th><th>Dauer</th><th>LLM</th><th>Statistik</th></tr></thead>
        <tbody>${stages}</tbody>
      </table>
      <p class="muted">Neu bauen mit <code>openwiki build</code> — inkrementell, nur veraltete Stufen.</p>

      <h3>Einstellungen <span class="muted">(alle Pipeline-Parameter)</span></h3>${settingsHtml}

      <h3>Ontologie <span class="muted">(Entitätstypen)</span></h3>${ontHtml}

      <h3>Wissensgraph</h3>${graphHtml}

      <h3>Themen <span class="muted">(Communities · ${comms.length})</span></h3>${commHtml}${askBox}

      <h3>Semantischer Index</h3>${indexHtml}

      <h3>Registrierte Projekte</h3><ul class="proj-list">${registry}</ul>
    </div>`;
    const askBtn = $("#global-ask-btn");
    if (askBtn) {
      askBtn.addEventListener("click", runGlobalAsk);
      $("#global-q").addEventListener("keydown", (e) => { if (e.key === "Enter") runGlobalAsk(); });
    }
    content.scrollTop = 0;
  } catch (e) {
    content.innerHTML = `<p class="muted">Projekt nicht verfügbar: ${escapeHtml(e.message)}</p>`;
  }
}

// Global search: a thematic question answered from the community summaries.
async function runGlobalAsk() {
  const input = $("#global-q");
  const q = (input && input.value || "").trim();
  if (!q) return;
  const btn = $("#global-ask-btn"), out = $("#global-result");
  if (btn) btn.disabled = true;
  if (out) out.innerHTML = `<p class="muted">Antwort wird generiert… (kann 10–30 s dauern)</p>`;
  try {
    const d = await postJSON("/api/global", { question: q });
    const cited = new Set(d.cited || []);
    const comms = (d.communities || []).map((c) =>
      `<li>${cited.has(c.marker) ? "★" : "•"} <span class="gc-marker">[${c.marker}]</span> ` +
      `${escapeHtml(c.label)} <span class="muted">(${c.size} Seiten)</span></li>`).join("");
    out.innerHTML = `<div class="global-answer">${marked.parse(d.answer || "")}</div>` +
      `<h4>Communities <span class="muted">(★ = zitiert)</span></h4>` +
      `<ul class="proj-list gc-list">${comms}</ul>`;
  } catch (e) {
    if (out) out.innerHTML = `<p class="muted">Global-Suche fehlgeschlagen: ${escapeHtml(e.message)}</p>`;
  } finally {
    if (btn) btn.disabled = false;
  }
}

// -- evaluation tab (retrieval benchmark: RAG vs GraphRAG) -----------------

const evalState = { top_k: 5, expand_k: 3, evalSet: null, data: null, busy: false };
const compareState = { data: null, busy: false };
const answerEval = { timer: 0 };   // poll handle for the background answer-quality job

async function renderEval() {
  const content = $("#content");
  content.innerHTML = `<div class="eval-view">
    <h2>Evaluation</h2>
    <p class="muted">Retrieval-Qualität über die Ground-Truth-Fragen in
      <code>eval.jsonl</code> — <strong>RAG</strong> (semantisch) vs.
      <strong>GraphRAG</strong> (Seeds + Graph-Erweiterung), gleiches Budget
      <code>top_k + expand_k</code>.</p>
    <div class="eval-controls">
      <label>Eval-Menge <select id="ev-set"></select></label>
      <label>top_k <input type="range" id="ev-topk" min="1" max="12" value="${evalState.top_k}">
        <span id="ev-topk-v">${evalState.top_k}</span></label>
      <label>expand_k <input type="range" id="ev-expandk" min="0" max="8" value="${evalState.expand_k}">
        <span id="ev-expandk-v">${evalState.expand_k}</span></label>
      <button id="ev-run" class="graph-open">Ausführen</button>
      <span id="ev-status" class="muted"></span>
    </div>
    <div id="ev-results"></div>

    <h3>Antwortqualität · RAG vs. GraphRAG
      <span class="muted">(generiert Antworten — langsam, läuft im Hintergrund)</span></h3>
    <p class="muted">Bewertet die <em>generierten</em> Antworten: Zitat-Treffer gegen die
      Ground-Truth-Seiten (objektiv) und optional ein LLM-Richter. Nutzt dieselben
      <code>top_k</code>/<code>expand_k</code> wie oben.</p>
    <div class="eval-controls">
      <label><input type="checkbox" id="aq-judge" /> LLM-Richter</label>
      <label>Limit <input type="number" id="aq-limit" min="1" placeholder="alle" style="width:64px" /></label>
      <button id="aq-run" class="graph-open">Messen</button>
      <span id="aq-status" class="muted"></span>
    </div>
    <div id="aq-results"></div>

    <h3>Live A/B · RAG vs. GraphRAG</h3>
    <p class="muted">Eine Frage durch beide Retriever schicken und die abgerufenen
      Seiten (und optional die generierten Antworten) vergleichen. Nutzt dieselben
      <code>top_k</code>/<code>expand_k</code> wie oben.</p>
    <div class="eval-ab-controls">
      <input id="ab-q" type="text" placeholder="Frage stellen…" />
      <label><input type="checkbox" id="ab-ans" /> Antworten generieren
        <span class="muted">(langsam)</span></label>
      <button id="ab-run" class="graph-open">Vergleichen</button>
      <span id="ab-status" class="muted"></span>
    </div>
    <div id="ab-results"></div>

    <h3>Wissensbasis-Qualität</h3>
    <div id="health-results"><p class="muted">Wird geladen…</p></div>
  </div>`;
  const topk = $("#ev-topk"), expandk = $("#ev-expandk");
  const sync = () => { $("#ev-topk-v").textContent = topk.value; $("#ev-expandk-v").textContent = expandk.value; };
  const rerun = () => { evalState.top_k = +topk.value; evalState.expand_k = +expandk.value; runEval(); };
  topk.addEventListener("input", sync);
  expandk.addEventListener("input", sync);
  topk.addEventListener("change", rerun);   // fire on release, not every tick
  expandk.addEventListener("change", rerun);
  $("#ev-run").addEventListener("click", rerun);
  const abRun = () => runCompare();
  $("#ab-run").addEventListener("click", abRun);
  $("#ab-q").addEventListener("keydown", (e) => { if (e.key === "Enter") abRun(); });
  $("#aq-run").addEventListener("click", startAnswerEval);
  populateEvalSets();    // fill the eval-set dropdown
  refreshAnswerEval();   // reflect a running/finished answer-eval job on tab open
  content.scrollTop = 0;
  if (evalState.data) renderEvalResults(evalState.data);   // show last result instantly
  if (compareState.data) { $("#ab-q").value = compareState.data.question || ""; renderCompareResults(compareState.data); }
  runEval();
  renderHealth();
}

async function renderHealth() {
  const el = $("#health-results");
  if (!el) return;
  try {
    const h = await getJSON("/api/health");
    if (!h.graph) { el.innerHTML = `<p class="muted">Kein Graph geladen — keine Qualitätsmetriken.</p>`; return; }
    if (h.error) { el.innerHTML = `<p class="muted">${escapeHtml(h.error)}</p>`; return; }
    const esc = escapeHtml;
    const pct = h.entities ? Math.round(100 * h.singleton_entities / h.entities) : 0;
    const kv = (obj) => `<table class="proj-kv"><tbody>` + Object.entries(obj).map(([k, v]) =>
      `<tr><td>${esc(k)}</td><td><code>${esc(String(v))}</code></td></tr>`).join("") + `</tbody></table>`;
    const conn = kv({
      "Ø Verbindungen/Seite": h.avg_degree,
      "Seiten ohne Ähnlichkeitskante": h.pages_no_similar,
      "Seiten ohne Entitäten": h.pages_no_entities,
      "verwaiste Seiten": h.orphans.length,
    });
    const orphans = h.orphans.length
      ? `<ul class="proj-list">${h.orphans.slice(0, 12).map((o) =>
          `<li><a href="#" data-slug="${esc(o.slug)}" class="ab-page">${esc(o.title)}</a></li>`).join("")}</ul>`
      : `<p class="muted">Keine verwaisten Seiten — alle sind verbunden.</p>`;
    const ent = kv({
      "Entitäten gesamt": h.entities,
      "Singletons (nur 1 Seite)": `${h.singleton_entities} (${pct}%)`,
      "seitenübergreifend (≥2 Seiten)": h.cross_page_entities,
    });
    const maxHub = h.hubs.reduce((m, x) => Math.max(m, x.pages), 0) || 1;
    const hubBars = `<div class="ent-bars">${h.hubs.map((x) =>
      `<div class="ent-bar"><span class="ent-name">${esc(x.name)}</span>` +
      `<span class="ent-track"><span class="ent-fill" style="width:${Math.round(100 * x.pages / maxHub)}%"></span></span>` +
      `<span class="ent-count">${x.pages}</span></div>`).join("")}</div>`;
    const topPages = `<ul class="proj-list">${h.top_pages.slice(0, 8).map((p) =>
      `<li><a href="#" data-slug="${esc(p.slug)}" class="ab-page">${esc(p.title)}</a> ` +
      `<span class="muted">· ${p.degree}</span></li>`).join("")}</ul>`;
    el.innerHTML =
      `<div class="proj-cols">` +
      `<div><h4>Konnektivität</h4>${conn}<h4>Verwaiste Seiten</h4>${orphans}</div>` +
      `<div><h4>Entitäten-Vernetzung</h4>${ent}` +
      `<p class="muted" style="font-size:12px">Viele Singletons = die Entitäten verbinden den Korpus kaum; ` +
      `die seitenübergreifenden tragen die <code>shared_entity</code>-Kanten.</p></div>` +
      `</div>` +
      `<h4>Top-Konzepte <span class="muted">(Seiten je Entität)</span></h4>${hubBars}` +
      `<h4>Bestvernetzte Seiten <span class="muted">(ähnlich + Verweise)</span></h4>${topPages}`;
    el.querySelectorAll("a.ab-page").forEach((a) =>
      a.addEventListener("click", (e) => { e.preventDefault(); loadPage(a.dataset.slug); }));
  } catch (e) {
    el.innerHTML = `<p class="muted">Qualitätsdaten nicht verfügbar: ${escapeHtml(e.message)}</p>`;
  }
}

async function populateEvalSets() {
  const sel = $("#ev-set");
  if (!sel) return;
  try {
    const data = await getJSON("/api/eval-sets");
    if (!evalState.evalSet) evalState.evalSet = data.default;
    sel.innerHTML = (data.sets || []).map((s) =>
      `<option value="${escapeHtml(s)}"${s === evalState.evalSet ? " selected" : ""}>${escapeHtml(s)}</option>`
    ).join("") || `<option value="">(keine)</option>`;
    sel.addEventListener("change", () => { evalState.evalSet = sel.value; runEval(); });
  } catch (e) { /* endpoint may be absent */ }
}

async function runEval() {
  if (evalState.busy) return;
  evalState.busy = true;
  const status = $("#ev-status"), btn = $("#ev-run");
  if (status) status.textContent = "läuft…";
  if (btn) btn.disabled = true;
  try {
    const set = evalState.evalSet ? `&eval_set=${encodeURIComponent(evalState.evalSet)}` : "";
    const data = await getJSON(`/api/eval?top_k=${evalState.top_k}&expand_k=${evalState.expand_k}${set}`);
    evalState.data = data;
    renderEvalResults(data);
  } catch (e) {
    const r = $("#ev-results");
    if (r) r.innerHTML = `<p class="muted">Evaluation fehlgeschlagen: ${escapeHtml(e.message)}</p>`;
  } finally {
    evalState.busy = false;
    if (status) status.textContent = "";
    if (btn) btn.disabled = false;
  }
}

function renderEvalResults(data) {
  const el = $("#ev-results");
  if (!el) return;
  if (data.error) { el.innerHTML = `<p class="muted">${escapeHtml(data.error)}</p>`; return; }
  if (!data.exists) {
    el.innerHTML = `<p class="muted">Keine Eval-Menge gefunden` +
      (data.path ? ` (<code>${escapeHtml(data.path)}</code>)` : "") +
      `. Lege eine JSONL-Datei mit Zeilen ` +
      `<code>{"question": "…", "pages": ["slug"]}</code> an.</p>`;
    return;
  }
  const pct = (x) => (100 * x).toFixed(1) + "%";
  const best = {};   // highlight the leading value per metric
  ["mrr", "hit_rate", "recall"].forEach((m) => { best[m] = Math.max(...data.reports.map((r) => r[m])); });
  const cell = (r, m, txt) => `<td class="${r[m] === best[m] && data.reports.length > 1 ? "ev-best" : ""}">${txt}</td>`;
  const rows = data.reports.map((r) =>
    `<tr><td><strong>${escapeHtml(r.name)}</strong></td>` +
    cell(r, "mrr", r.mrr.toFixed(3)) + cell(r, "hit_rate", pct(r.hit_rate)) +
    cell(r, "recall", pct(r.recall)) + `</tr>`).join("");
  const last = data.reports[data.reports.length - 1];
  const misses = (last && last.items || []).filter((it) => !it.hit);
  const missHtml = misses.length
    ? `<h3>Fehlschläge <span class="muted">(${escapeHtml(last.name)}, keine erwartete Seite in Top-${data.budget})</span></h3>` +
      `<ul class="proj-list">${misses.map((it) =>
        `<li><strong>${escapeHtml(it.question)}</strong><br>` +
        `<span class="muted">erwartet ${escapeHtml(it.expected.join(", "))} · ` +
        `erhalten ${escapeHtml(it.ranked.join(", "))}</span></li>`).join("")}</ul>`
    : `<p class="muted">Keine Fehlschläge — jede Frage findet ihre Seite in Top-${data.budget}.</p>`;
  el.innerHTML =
    `<p class="muted">${data.eval_set ? "<code>" + escapeHtml(data.eval_set) + "</code> · " : ""}` +
    `${data.count} Fragen · k=${data.budget} ` +
    `(top_k=${data.top_k} + expand_k=${data.expand_k})</p>` +
    `<table class="eval-table"><thead><tr><th>Retriever</th><th>MRR</th>` +
    `<th>hit@k</th><th>recall@k</th></tr></thead><tbody>${rows}</tbody></table>` +
    missHtml;
}

async function runCompare() {
  if (compareState.busy) return;
  const q = $("#ab-q").value.trim();
  if (!q) return;
  const answers = $("#ab-ans").checked;
  compareState.busy = true;
  const status = $("#ab-status"), btn = $("#ab-run");
  if (status) status.textContent = answers ? "läuft… (Antworten können 1–2 Min dauern)" : "läuft…";
  if (btn) btn.disabled = true;
  try {
    const data = await postJSON("/api/compare", {
      question: q, top_k: evalState.top_k, expand_k: evalState.expand_k, answers });
    compareState.data = data;
    renderCompareResults(data);
  } catch (e) {
    const el = $("#ab-results");
    if (el) el.innerHTML = `<p class="muted">Vergleich fehlgeschlagen: ${escapeHtml(e.message)}</p>`;
  } finally {
    compareState.busy = false;
    if (status) status.textContent = "";
    if (btn) btn.disabled = false;
  }
}

function renderCompareResults(d) {
  const el = $("#ab-results");
  if (!el) return;
  const badge = (k) => `<span class="ab-kind ab-${k}">${k === "related" ? "+Graph" : "seed"}</span>`;
  const col = (title, s) => {
    if (!s) return `<div class="ab-col"><h4>${title}</h4><p class="muted">Kein Graph geladen.</p></div>`;
    const answer = s.answer != null
      ? `<div class="ab-answer">${marked.parse(s.answer)}</div>` : "";
    const cited = new Set(s.cited || []);
    const src = s.sources.map((x) =>
      `<li>${badge(x.kind)} <a href="#" data-slug="${escapeHtml(x.slug)}" class="ab-page">${escapeHtml(x.title)}</a>` +
      `<span class="muted"> · ${x.score}</span>` +
      (cited.has(x.marker) ? ` <span class="ab-cited">[${x.marker}] zitiert</span>` : "")).join("");
    return `<div class="ab-col"><h4>${title} <span class="muted">(${s.sources.length} Quellen)</span></h4>` +
           `${answer}<ul class="ab-src">${src}</ul></div>`;
  };
  el.innerHTML =
    `<p class="muted">„${escapeHtml(d.question)}"  ·  top_k=${d.top_k} + expand_k=${d.expand_k}` +
    (d.answers ? "" : "  ·  nur Retrieval") +
    (d.answers_available ? "" : "  ·  <em>kein Chat-Modell — nur Retrieval möglich</em>") + `</p>` +
    `<div class="ab-cols">${col("RAG", d.rag)}${col("GraphRAG", d.graphrag)}</div>`;
  el.querySelectorAll("a.ab-page").forEach((a) =>
    a.addEventListener("click", (e) => { e.preventDefault(); loadPage(a.dataset.slug); }));
}

// -- answer-quality eval (background job: RAG vs GraphRAG answers) ----------

async function startAnswerEval() {
  const btn = $("#aq-run");
  const body = {
    judge: $("#aq-judge").checked,
    limit: parseInt($("#aq-limit").value, 10) || null,
    top_k: evalState.top_k, expand_k: evalState.expand_k,
    eval_set: evalState.evalSet,
  };
  btn.disabled = true;
  try {
    const job = await postJSON("/api/answer-eval", body);
    renderAnswerEvalJob(job);
    if (job.status === "running") pollAnswerEval();
  } catch (e) {
    const el = $("#aq-results");
    if (el) el.innerHTML = `<p class="muted">Fehler: ${escapeHtml(e.message)}</p>`;
  } finally {
    btn.disabled = false;
  }
}

async function refreshAnswerEval() {
  try {
    const job = await getJSON("/api/answer-eval");
    renderAnswerEvalJob(job);
    if (job.status === "running") pollAnswerEval();
  } catch (e) { /* tab may lack the endpoint */ }
}

function pollAnswerEval() {
  if (answerEval.timer) return;
  answerEval.timer = setInterval(async () => {
    let job;
    try { job = await getJSON("/api/answer-eval"); } catch (e) { return; }
    renderAnswerEvalJob(job);
    if (job.status !== "running") { clearInterval(answerEval.timer); answerEval.timer = 0; }
  }, 3000);
}

function renderAnswerEvalJob(job) {
  const status = $("#aq-status"), el = $("#aq-results");
  if (!status || !el) return;
  if (job.status === "running") {
    status.textContent = `läuft… ${job.done || 0}/${job.total || "?"}  (2–3 Modell-Aufrufe/Frage)`;
    return;
  }
  status.textContent = "";
  if (job.status === "error") {
    el.innerHTML = `<p class="muted">Fehler: ${escapeHtml(job.error || "")}</p>`;
    return;
  }
  if (job.status !== "done" || !job.result) { el.innerHTML = ""; return; }
  const r = job.result, g = r.grounding, pct = (x) => (100 * x).toFixed(1) + "%";
  const bestHit = Math.max(g.RAG.cite_hit, g.GraphRAG.cite_hit);
  const bestRec = Math.max(g.RAG.expected_recall, g.GraphRAG.expected_recall);
  const cell = (v, best, txt) => `<td class="${v === best ? "ev-best" : ""}">${txt}</td>`;
  const rows = ["RAG", "GraphRAG"].map((name) =>
    `<tr><td><strong>${name}</strong></td>` +
    cell(g[name].cite_hit, bestHit, pct(g[name].cite_hit)) +
    cell(g[name].expected_recall, bestRec, pct(g[name].expected_recall)) + `</tr>`).join("");
  let judge = "";
  if (r.judged) {
    const t = r.tally, lead = t.GraphRAG > t.RAG ? "GraphRAG" : (t.RAG > t.GraphRAG ? "RAG" : null);
    judge = `<p class="aq-judge">LLM-Richter (positionsbalanciert): ` +
      `<span class="${lead === "GraphRAG" ? "ev-best" : ""}">GraphRAG ${t.GraphRAG}</span> · ` +
      `<span class="${lead === "RAG" ? "ev-best" : ""}">RAG ${t.RAG}</span> · unentschieden ${t.tie}</p>`;
  }
  el.innerHTML =
    `<p class="muted">${r.questions} Fragen · top_k=${job.top_k} + expand_k=${job.expand_k}` +
    `${r.judged ? "" : " · nur Grounding"}</p>` +
    `<table class="eval-table"><thead><tr><th>Retriever</th><th>Zitat-Treffer</th>` +
    `<th>Erw.-Recall</th></tr></thead><tbody>${rows}</tbody></table>${judge}`;
}

// -- graph tab (interactive neighborhood exploration) ----------------------

const SVG_NS = "http://www.w3.org/2000/svg";
const EDGE_COLOR = { parent: "#7048e8", child: "#7048e8", prev: "#868e96", next: "#868e96",
                     similar: "#2f9e44", references: "#e8590c", referenced_by: "#e8590c",
                     shared_entity: "#0c8599", mentions: "#f08c00", reinforced: "#c2255c",
                     relation: "#9c36b5" };
// Legend/filter groups (a click toggles a whole relationship kind on/off).
const FILTERS = [
  { key: "hier",    label: "Hierarchie",          types: ["parent", "child"],          color: "#7048e8" },
  { key: "seq",     label: "Reihenfolge",         types: ["prev", "next"],             color: "#868e96" },
  { key: "similar", label: "Ähnlich",             types: ["similar"],                  color: "#2f9e44" },
  { key: "ref",     label: "Verweise",            types: ["references", "referenced_by"], color: "#e8590c" },
  { key: "shared",  label: "Gemeinsame Begriffe", types: ["shared_entity"],            color: "#0c8599" },
  { key: "entity",  label: "Begriffe (Entitäten)", types: ["mentions"],                color: "#f08c00" },
  { key: "relation", label: "Beziehungen (typisiert)", types: ["relation"],            color: "#9c36b5" },
  { key: "reinforced", label: "Verstärkt (Nutzung)", types: ["reinforced"],            color: "#c2255c" },
];
const TYPE_FILTER = {};
FILTERS.forEach((f) => f.types.forEach((t) => (TYPE_FILTER[t] = f.key)));

// Distinct fills for community (theme) coloring of page nodes, by community id.
const COMMUNITY_PALETTE = [
  "#4dabf7", "#38d9a9", "#ffa94d", "#da77f2", "#ff8787", "#a9e34b", "#3bc9db",
  "#ffd43b", "#748ffc", "#f783ac", "#63e6be", "#ffc078",
];

const GW = 900, GH = 600;   // SVG viewBox
// The live explorer graph (accumulates as you expand nodes).
const graph = { nodes: new Map(), edges: [], root: null, selected: null,
                hidden: new Set(), svg: null, raf: 0, alpha: 0,
                communities: [], communityColor: {}, colorByTheme: true,
                _nodeEls: new Map(), _edgeEls: [] };

// Page-node fill: by community (theme) when enabled, else the default blue.
function communityFill(n) {
  if (graph.colorByTheme && n.community != null && graph.communityColor[n.community])
    return graph.communityColor[n.community];
  return n.root ? "#3b5bdb" : "#4dabf7";
}

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
    const [data, comm] = await Promise.all([
      getJSON("/api/graph/" + encodeURIComponent(state.currentSlug)),
      getJSON("/api/communities").catch(() => ({ communities: [] })),
    ]);
    setCommunityColors(comm.communities || []);
    initGraph(content, data);
  } catch (e) {
    content.innerHTML = `<p class="muted">Graph nicht verfügbar: ${escapeHtml(e.message)}` +
      `<br><span class="muted">Erzeuge ihn mit <code>openwiki graph-build</code> ` +
      `(Entitäten mit <code>--entities</code>).</span></p>`;
  }
}

function setCommunityColors(list) {
  graph.communities = list;
  graph.communityColor = {};
  list.forEach((c) => (graph.communityColor[c.id] = COMMUNITY_PALETTE[c.id % COMMUNITY_PALETTE.length]));
  graph.colorByTheme = list.length > 0;   // on by default when the graph has communities
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
      // `addedBy` = the expansion that first revealed this edge, so collapsing that
      // node can remove the edges it introduced (even to already-visible nodes).
      graph.edges.push({ source: e.source, target: e.target, type: e.type,
                         label: e.label, addedBy: parentId });
    }
  });
}

const isNodeHidden = (n) => n && n.kind === "entity" && graph.hidden.has("entity");
function isEdgeHidden(e) {
  const fk = TYPE_FILTER[e.type];
  if (fk && graph.hidden.has(fk)) return true;
  return isNodeHidden(graph.nodes.get(e.source)) || isNodeHidden(graph.nodes.get(e.target));
}

// A "Themenfarben" toggle + a swatch-per-community legend (empty when none).
function buildThemeLegend(content) {
  const legend = document.createElement("div");
  legend.className = "graph-legend";
  if (!graph.communities.length) return legend;
  const toggle = document.createElement("button");
  toggle.className = "graph-filter" + (graph.colorByTheme ? "" : " off");
  toggle.textContent = "Themenfarben";
  toggle.addEventListener("click", () => { graph.colorByTheme = !graph.colorByTheme; buildGraphDom(content); });
  legend.appendChild(toggle);
  if (graph.colorByTheme) {
    graph.communities.forEach((c) => {
      const item = document.createElement("span");
      item.className = "legend-item";
      item.innerHTML = `<i style="background:${graph.communityColor[c.id]}"></i>` +
        `${escapeHtml(c.label)} <span class="muted">(${c.size})</span>`;
      legend.appendChild(item);
    });
  }
  return legend;
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
  open.addEventListener("click", () => {
    const s = graph.nodes.get(graph.selected);
    loadPage(s && s.kind === "page" ? s.id : graph.root);   // entities have no page
  });
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

  // Active/selected subgraph = the selected node + the subtree it introduced.
  // When a proper branch is selected we emphasise it and dim the rest.
  const selId = graph.nodes.has(graph.selected) ? graph.selected : graph.root;
  const selSet = new Set([selId]);
  descendantsOf(selId).forEach((id) => selSet.add(id));
  const visN = [...graph.nodes.values()].filter((n) => !isNodeHidden(n)).length;
  const focus = selSet.size > 1 && selSet.size < visN;

  // Node degree over currently-visible edges — drives label priority.
  const deg = {};
  graph.edges.forEach((e) => {
    if (isEdgeHidden(e)) return;
    const inSel = selSet.has(e.source) && selSet.has(e.target);
    const base = e.type === "similar" ? 1.3 : 2;
    const line = svgEl("line", { stroke: EDGE_COLOR[e.type] || "#ccc",
      "stroke-width": focus && inSel ? base + 1.7 : base,
      "stroke-opacity": !focus ? 0.4 : (inSel ? 0.92 : 0.15) });
    if (e.type === "relation" && e.label) {   // show the predicate on hover
      const t = svgEl("title", {}); t.textContent = e.label; line.appendChild(t);
    }
    edgeG.appendChild(line);
    graph._edgeEls.push({ e, line });
    deg[e.source] = (deg[e.source] || 0) + 1;
    deg[e.target] = (deg[e.target] || 0) + 1;
  });

  graph.nodes.forEach((n) => {
    if (isNodeHidden(n)) return;
    const g = svgEl("g", { class: "gnode" + (n.expanded ? " expanded" : "")
      + (n.id === selId ? " selected" : "") + (focus && !selSet.has(n.id) ? " dim" : "") });
    const r = n.kind === "entity" ? 10 : (n.root ? 13 : 9);
    if (n.id === selId) {          // accent ring marks the active/selected node
      g.appendChild(svgEl("circle", { r: r + 7, fill: "none",
        stroke: "#1971c2", "stroke-width": 2.5, "stroke-opacity": 0.9 }));
    }
    if (n.expanded && !n.root) {   // outer ring marks an expanded node (double-click to collapse)
      g.appendChild(svgEl("circle", { r: r + 5, fill: "none",
        stroke: n.kind === "entity" ? "#f08c00" : "#4dabf7", "stroke-width": 1.5, "stroke-opacity": 0.5 }));
    }
    if (n.kind === "entity") {
      g.appendChild(svgEl("rect", { x: -7, y: -7, width: 14, height: 14,
        transform: "rotate(45)", fill: "#f08c00", stroke: "#fff", "stroke-width": 2 }));
    } else {
      g.appendChild(svgEl("circle", { r: n.root ? 13 : 9,
        fill: communityFill(n), stroke: "#fff", "stroke-width": 2 }));
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

  content.append(bar, filters, buildThemeLegend(content), svg);
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
  graph.selected = n.id;   // any node can be the active anchor (highlights its subtree)
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
  gone.forEach((id) => graph.nodes.delete(id));
  // Drop edges into the removed subtree AND edges this node introduced to survivors.
  graph.edges = graph.edges.filter(
    (e) => !gone.has(e.source) && !gone.has(e.target) && e.addedBy !== n.id);
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
  } else if (kind === "tab") {
    activateTab(arg);        // switch the center tab (e.g. project)
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

function renderChatStats(container, s) {
  if (!s) return;   // fake/non-Ollama backend, or nothing recorded
  const bits = [];
  if (s.duration_ms) bits.push(fmtMs(s.duration_ms));
  if (s.eval_tokens) bits.push(`${s.eval_tokens} Tokens`);
  if (s.tokens_per_sec) bits.push(`${s.tokens_per_sec} tok/s`);
  if (s.calls > 1) bits.push(`${s.calls} Modellaufrufe`);
  if (!bits.length) return;
  const meta = document.createElement("div");
  meta.className = "msg-meta";
  meta.textContent = "⏱ " + bits.join(" · ");
  container.appendChild(meta);
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
    renderChatStats(bubble, data.stats);
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

// -- Ask mode (RAG question-answering, read-only) ---------------------------

function askSourceChips(sources) {
  if (!sources || !sources.length) return null;
  const wrap = document.createElement("div");
  wrap.className = "src-chips";
  sources.forEach((s) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "src-chip" + (s.kind === "related" ? " related" : "");
    chip.title = "Seite öffnen · Score " + s.score;
    chip.innerHTML =
      `<span class="src-badge">${s.kind === "related" ? "+Graph" : "Seed"}</span>` +
      `[${s.marker}] ${escapeHtml(s.title || s.slug)}`;
    chip.addEventListener("click", () => loadPage(s.slug));
    wrap.appendChild(chip);
  });
  return wrap;
}

function renderAskAnswer(bubble, data) {
  bubble.classList.add("md");
  bubble.innerHTML = data.answer ? marked.parse(data.answer) : "(keine Antwort)";
  const chips = askSourceChips(data.sources);
  if (chips) bubble.appendChild(chips);
  const o = data.options || {};
  const flags = [];
  if (o.graph) flags.push("GraphRAG");
  if (o.hybrid) flags.push("Hybrid");
  if (o.rerank) flags.push("Re-rank");
  flags.push("k=" + o.k);
  const meta = document.createElement("div");
  meta.className = "msg-meta";
  meta.textContent = flags.join(" · ");
  bubble.appendChild(meta);
  renderChatStats(bubble, data.stats);
  $("#chat-log").scrollTop = $("#chat-log").scrollHeight;
}

function renderGlobalAnswer(bubble, data) {
  bubble.classList.add("md");
  bubble.innerHTML = data.answer ? marked.parse(data.answer) : "(keine Antwort)";
  const cited = new Set(data.cited || []);
  const comms = data.communities || [];
  if (comms.length) {
    const wrap = document.createElement("div");
    wrap.className = "src-chips";
    comms.forEach((c) => {
      const chip = document.createElement("span");
      chip.className = "src-chip comm" + (cited.has(c.marker) ? " cited" : "");
      chip.innerHTML = `<span class="src-badge">[${c.marker}]</span>${escapeHtml(c.label)} ` +
        `<span class="muted">(${c.size})</span>`;
      wrap.appendChild(chip);
    });
    bubble.appendChild(wrap);
  }
  const meta = document.createElement("div");
  meta.className = "msg-meta";
  meta.textContent = "Global · " + comms.length + " Themen";
  bubble.appendChild(meta);
  $("#chat-log").scrollTop = $("#chat-log").scrollHeight;
}

async function sendAsk(question) {
  question = (question || "").trim();
  if (!question) return;
  addMsg("user", question);
  const btn = $("#chat-send");
  btn.disabled = true;
  const global = $("#ask-global").checked;
  const bubble = addMsg("agent", "…");
  try {
    if (global) {
      renderGlobalAnswer(bubble, await postJSON("/api/global", { question }));
    } else {
      renderAskAnswer(bubble, await postJSON("/api/ask", {
        question,
        graph: $("#ask-graph").checked,
        hybrid: $("#ask-hybrid").checked,
        rerank: $("#ask-rerank").checked,
        k: parseInt($("#ask-k").value, 10) || 5,
      }));
    }
  } catch (err) {
    bubble.textContent = "Fehler: " + err.message;
  } finally {
    btn.disabled = false;
    $("#chat-input").focus();
  }
}

let _askCapsProbed = false;
async function ensureAskCaps() {
  if (_askCapsProbed) return;
  _askCapsProbed = true;
  try {
    const { communities } = await getJSON("/api/communities");
    if (!communities || !communities.length) {
      const g = $("#ask-global");
      g.checked = false;
      g.disabled = true;
      g.closest("label").classList.add("disabled");
      g.closest("label").title = "keine Communities — `openwiki communities` ausführen";
    }
  } catch (_) { /* leave enabled; the server reports if it can't answer */ }
}

function setChatMode(mode) {
  state.chatMode = mode;
  document.querySelectorAll(".chat-mode").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
  $("#ask-controls").hidden = mode !== "ask";
  $("#chat-input").placeholder = mode === "ask"
    ? "Frage stellen (RAG, mit Quellen)…"
    : "Frage oder Änderungswunsch…";
  if (mode === "ask") ensureAskCaps();
}
document.querySelectorAll(".chat-mode").forEach((b) =>
  b.addEventListener("click", () => setChatMode(b.dataset.mode)));

$("#chat-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = $("#chat-input");
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  (state.chatMode === "ask" ? sendAsk : sendChat)(message);
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
    loadProjectBadge();
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
