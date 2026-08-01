"use strict";

const $ = (sel) => document.querySelector(sel);
const state = { manifest: null, pages: {}, firstSlug: null, currentSlug: null };
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
    $("#content").innerHTML = marked.parse(markdown);
    interceptLinks();
    state.currentSlug = slug;
    setActive(slug);
    $("#content").scrollTop = 0;
    history.replaceState(null, "", "#" + slug);
  } catch (e) {
    $("#content").innerHTML = `<p class="muted">Fehler: ${escapeHtml(e.message)}</p>`;
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

$("#chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("#chat-input");
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
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
    input.focus();
  }
});
$("#chat-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#chat-form").requestSubmit(); }
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
