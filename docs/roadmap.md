# OpenWiki — Implementation Roadmap

What has been built, in the order it was built — and where it could go next. OpenWiki
went from a single straight PDF→wiki pipeline to a project-aware, multi-format,
graph-augmented knowledge platform with a rigorously measured RAG-vs-GraphRAG story,
a **community consolidation layer** (global search), and the first step of a decaying
**usage-memory** graph. The first half of this doc records what shipped; the second
half turns forward — [open topics](#open-topics--known-limitations) and
[prioritized directions](#future-directions-prioritized) for what to build next.

*Provenance:* reconstructed from the git history — 70 commits / 37 tags,
2026-08-01 → 2026-09-05, v0.6.0 → v0.45.0. Version tags mark the milestones; a few
patch versions between them are omitted here for readability.

## The arc in one line

A linear IR-based pipeline (**ingest → wiki → index → RAG → edit**) grew a
**knowledge-graph** layer, a **browser UI**, a **projects** system, **multi-format
ingestion**, **coding-agent access**, an **evaluation harness** that turned
"is the graph worth it?" from opinion into numbers, a **community consolidation layer**
(global search over the whole corpus), and a decaying **usage-memory** overlay — the
first step from "document mirror" toward agent memory.

---

## Release timeline

| # | Era | Versions | Dates | What landed |
|---|---|---|---|---|
| 1 | **Foundation** | v0.6.0 | Aug 1 | Whole core pipeline + web UI, in the initial commit |
| 2 | **Knowledge graph + GraphRAG + entities** | v0.8.0–v0.14.0 | Aug 1–8 | Kuzu graph, REFERENCES, graph tools, GraphRAG retrieval, entity layer, force-directed explorer, incremental updates |
| 3 | **Coding-agent access** | v0.15.0–v0.16.0 | Aug 8–9 | Stdio MCP server; OpenCode integration |
| 4 | **Projects layer** | v0.17.0–v0.21.6 | Aug 15–16 | `openwiki.toml`, `build`/`status`, registry + global config, multi-source merge, project-aware UI, pipx installer |
| 5 | **Ontology + outline synthesis** | v0.22.0–v0.23.0 | Aug 16 | Configurable entity ontology + `owiki ontology` proposer; header-based outline synthesis |
| 6 | **Projekt tab, anti-drift, scaffolders, cross-refs, entity determinism** | v0.24.0–v0.27.2 | Aug 16 | Full Projekt overview tab; agent wiki-grounding; `owiki opencode` / `claude-code` scaffolders; section/chapter refs; deterministic + de-noised extraction |
| 7 | **Evaluation tab + `owiki eval`** | v0.28.0–v0.31.0 | Aug 17 | Retrieval benchmark; Evaluation tab phases 1–3 (benchmark, live A/B, KB-health) |
| 8 | **Entity normalization + robustness** | v0.32.0–v0.32.2 | Aug 17 | German singular/plural + umlaut/ß merging; hang-bounding; retry-on-empty |
| 9 | **Source parsers** | v0.33.0–v0.36.0 | Aug 17 | Markdown/text, web (URL/HTML), code-repo parsers behind one dispatch + project wiring |
| 10 | **Answer-quality eval + the finding** | v0.37.0–v0.38.0 | Aug 17 | `owiki eval --answers/--judge`; surfaced as an async job in the Evaluation tab |
| 11 | **CLI polish** | v0.38.1–v0.38.2 | Aug 17 | `--eval-set` project-root resolution; `--version` |
| 12 | **Consolidation layer + usage-memory + measurement (Path A/B)** | v0.39.0–v0.45.0 | Sep 5 | Community detection + LLM summaries + global search across CLI / browser / MCP; time/decay `REINFORCES` memory edges; thematic eval (Global beats RAG 9–1); Graph-tab community colouring |

---

## Feature areas in detail

### 1. Core pipeline & the IR (v0.6.0)
The spine everything else hangs off — a straight pipeline around an intermediate
representation (IR) so later stages never touch PDF internals.
- **Ingestion** — `PDFParser` (PyMuPDF) → `ParsedDocument` IR (`DocumentMetadata` +
  `OutlineItem[]` + `Page[]` with text/tables/images), serialized to canonical JSON +
  Markdown. `fitz` is confined to this one module.
- **Wiki generation** — `WikiBuilder` splits the IR along the outline into a tree of
  linked pages (`index.md`, `wiki.json`, `pages/*.md`); groups outline entries at
  PDF-page granularity.
- **Semantic search** — `chunk_wiki` → overlapping word-window chunks → `OllamaEmbedder`
  (bge-m3) → `SemanticIndex` (normalized NumPy matrix, brute-force cosine).
- **RAG agent** — retrieve top chunks → grounded system prompt → `OllamaChat` → cited
  answer (`RAGAnswer` + `Source`s, `<think>` stripped).
- **Editing agent** — `WikiAgent` multi-turn tool loop over `WikiTools`
  (`search`/`read`/`edit`/`append`/`create`), slug-guarded writes, `--dry-run`.
- **Web UI** — stdlib `http.server` + no-build vanilla-JS SPA, client-side Markdown via
  vendored `marked.min.js`.

### 2. Multi-format source parsers (v0.33.0–v0.36.0)
Proved the IR boundary: each parser slotted in behind `sources.parse_source` with zero
downstream change.
- **Markdown/plain-text** (v0.33.0) — first non-PDF parser; ATX headings → pages;
  stdlib-only; PyMuPDF now lazy-imported.
- **Web** (v0.34.0) — `http(s)` URLs (urllib) + local `.html`; stdlib `html.parser`
  subclass strips boilerplate, `<h1>`–`<h6>` → the same heading→section→page model.
- **Code repositories** (v0.35.0) — a directory → overview page + one page per source
  file; `os.walk` with noise/binary/oversize pruning.
- **Project wiring** (v0.36.0) — URLs and repos as first-class `[[sources]]` (referenced
  in place, not copied); `file_sig` signs URLs by string and repos by file tree;
  `init`/`add-source --repo`.

### 3. Knowledge graph, entities & GraphRAG (v0.8.0–v0.14.0)
An additive Kuzu layer over the wiki — reads the wiki + index, never mutates them.
- **Graph build** (v0.8.0) — Page/Chunk nodes; CHILD_OF/NEXT/PART_OF/SIMILAR_TO edges;
  HNSW vector index with embeddings *mirrored* from the index.
- **Cross-references** (v0.9.0, extended v0.26.0) — "Seite N" page refs (with
  printed↔physical offset detection) **and** "Abschnitt/Kapitel N.M" section refs via
  running-header maps → REFERENCES edges.
- **Graph-aware agent tools** (v0.10.0) — `graph_neighbors`, `find_path` (Kuzu
  shortest-path, Page↔Page only).
- **GraphRAG** (v0.11.0) — `ask` expands semantic seeds along references/similar edges,
  re-ranks by query.
- **Entity layer** (v0.12.0) — opt-in LLM extraction → `Entity` + MENTIONS;
  `find_entity`; `shared_entity` neighbor group.
- **Force-directed explorer** (v0.13.x) — hand-rolled SVG physics sim,
  click-to-expand/collapse, label-collision culling, active-subgraph highlight.
- **Incremental updates** (v0.14.0) — `serve`/`chat` open the graph writable; agent edits
  `upsert_page` live (recompute SIMILAR_TO).

### 4. Entity-extraction quality — a sustained arc
LLM extraction is noisy and non-deterministic, so this got repeated hardening:
- **Determinism + de-noising** (v0.27.1–v0.27.2) — greedy + seed; exclude
  identifiers/keywords/author names.
- **Normalization** (v0.32.0) — merge German singular/plural + umlaut/ß + hyphenation
  variants (Signal/Signale) without over-merging distinct compounds
  (Systemgrenze ≠ Systemzustand).
- **Robustness** (v0.32.1–v0.32.2) — bound output so greedy loops can't hang;
  retry-on-empty with a sampled pass (informatik: empty pages 9→2, entities +191).
- **Configurable ontology** (v0.22.0) — per-project `entity_types` + `owiki ontology`
  proposer (samples corpus, one LLM call, review-and-write).

### 5. Projects layer (v0.17.0–v0.21.x)
Turned loose CLI stages into a persistent, reproducible unit — designed as 4 phases
(all landed).
- **Phase 1** (v0.17.0) — `openwiki.toml` manifest, discovery, layout, setting-resolution
  with back-compat.
- **Phase 2** (v0.18.0) — `owiki build` (whole pipeline) + `status`, incremental via a
  per-stage fingerprint chain in `.openwiki/state.json`.
- **Phase 3** (v0.19.0) — `~/.openwiki/` global config + project registry
  (`project list/use/add/remove`).
- **Phase 4** (v0.20.0) — multiple `[[sources]]` of any type merged into one corpus
  (`combine_documents`).
- **Project-aware UI + packaging** (v0.21.x) — Projekt tab + `/api/project`; pipx
  installer; the `owiki` short alias.

See `docs/projects.md` for the full design + roadmap of this layer.

### 6. Web UI evolution
Beyond the original 3-pane SPA, the center pane grew to **six tabs**
(Projekt / Wiki / Graph / Evaluation / Tutorial / Hilfe):
- **Help & Tutorial** (v0.7.0) — Markdown docs with interactive `run:` action links.
- **Graph tab** (v0.8.0 → v0.13.x) — the explorer above.
- **Projekt tab** (v0.24.0) — full read-only knowledge-model overview: sources, per-stage
  build status, all settings, the ontology, live graph stats, index summary, registry.
- **Evaluation tab** (v0.29.0–v0.31.0, v0.38.0) — live benchmark with sliders + miss
  drill-down; live A/B compare; KB-health panel; async answer-quality job; eval-set
  selector.

### 7. Evaluation harness & the measured finding (v0.28.0–v0.38.0)
The intellectual payoff — backend-agnostic, unit-testable metrics driving a controlled
comparison.
- **Retrieval eval** (v0.28.0) — MRR/hit@k/recall@k; RAG vs GraphRAG at the same budget
  (`top_k + expand_k`).
- **Answer-quality eval** (v0.37.0) — generates both answers, scores **citation
  grounding** (objective) + an **LLM-as-judge** pairwise verdict (position-balanced).
- **The finding** — GraphRAG **does not improve retrieval recall** (RAG ≥ GraphRAG at
  every budget, both question sets) but **does improve answer quality**: relational set
  cite-hit 67% vs 58%, judge **8–4**; definitional set cite-hit tied at 86% but judge
  **11–3**. Recall drops while grounding rises.

Full writeup — methodology, both metric tables, caveats — in `docs/RAG-vs-GraphRAG.md`.

### 8. Coding-agent integration (v0.15.0–v0.16.0, v0.25.0, v0.27.0)
- **MCP server** (v0.15.0) — dependency-free stdio JSON-RPC exposing read-only `wiki_*`
  tools (ask/search/read/list/graph_neighbors/find_path/find_entity).
- **OpenCode** (v0.16.0 integration; v0.25.0 scaffolder) — `owiki opencode` generates a
  project-scoped agent + MCP config with project discovery.
- **Claude Code** (v0.27.0) — `owiki claude-code` writes `.mcp.json` + `.claude/` commands
  + an auto-applied skill.
- **Anti-drift** (v0.24.1) — stopped scaffolded agents defaulting to the Nautilus sample;
  added WikiAgent wiki-identity grounding.

Setup details in `docs/coding-agents.md`.

### 9. Structural robustness & CLI polish
- **Outline synthesis** (v0.23.0) — finer wiki pages from numbered running headers when a
  PDF has no bookmarks.
- **`write_wiki` cleanup** (v0.25.1) — clears stale `pages/*.md` on rebuild (fixed orphan
  accumulation).
- **CLI polish** (v0.38.1–v0.38.2) — `--eval-set` resolves bare names against the project
  root; `--version` flag.

---

## Where it stands

- **Corpora:** the NAUTILUS synth manual (269p → 51-page wiki → 815 chunks → graph
  51/306 SIMILAR_TO/122 REFERENCES + 801 entities) and the informatik CS lecture
  (16 PDFs → 799p → 76 pages → 2703 chunks → graph 76/760 SIMILAR_TO/32 REFERENCES +
  entities).
- **Tests:** 220 passing, fully offline (fakes for Ollama/Kuzu).
- **Stack:** Windows, Python 3.13, local Ollama (bge-m3 + qwen3:30b), Kuzu — minimal /
  stdlib-leaning throughout.

Two arcs stand out as genuinely complete: the **source parsers** (four formats, one
dispatch) and the **evaluation** work (a real, defensible finding rather than a demo).

---

## Open topics & known limitations

A candid list of what's unfinished or constrained today — the raw material for the
directions below.

- **Retrieval is brute-force and dense-only.** `SemanticIndex.search` is an O(n) NumPy
  cosine scan (fine at ~2.7k chunks, won't scale), and purely dense — no lexical/BM25
  fallback for exact German compounds, identifiers, or rare terms. Kuzu already holds a
  *mirrored* HNSW index that retrieval never uses.
- **The graph does not improve retrieval recall** (measured). Same-budget expansion
  restricts candidates to seed neighbours and re-ranks by the same query; there's no
  re-ranking model, no learned edge weighting, no multi-hop.
- **The "entity layer" is co-occurrence, not relations.** Entities connect only via shared
  `MENTIONS` on a page — no typed `Entity→Entity` relations, no corpus-wide entity
  resolution beyond name-normalization, no confidence scores or descriptions.
- **Entity extraction is slow and imperfect** — ~1 LLM call/page, non-deterministic (needs
  retry-on-empty), no batching.
- **Eval is narrow** — one corpus (German CS lecture), one embedder (bge-m3), small N
  (12–14 questions/set), one judge (same model family). The finding is defensible but not
  shown to generalize.
- **Incremental graph updates are partial** — only `SIMILAR_TO` recomputes on an agent
  edit; `CHILD_OF`/`NEXT`/`REFERENCES`/entities still need a full `graph-build`.
- **The editing agent can't restructure** — create/edit/append only; no
  delete/rename/move/merge/split page.
- **No auth anywhere** — `serve` (including its write paths) and the MCP server are
  unauthenticated: safe on localhost, unsafe the moment they're exposed.
- **Wiki pages are a mechanical outline split** — no LLM-authored summaries, no
  auto-generated glossary/index, no inline cross-links from REFERENCES/entities.
- **Ingestion fidelity is heuristic** — `pymupdf_layout` is flagged but unused; tables and
  images are extracted but barely used downstream; nothing multimodal.
- **One backend, no cross-run cache** — only Ollama implements the `Embedder`/`ChatModel`
  protocols; embeddings/LLM calls aren't cached between runs beyond the saved index.
- **Packaging is local-only** — pipx on Windows; no PyPI, no Docker, no cross-platform CI,
  no optional live-Ollama integration test.

## Future directions (prioritized)

The evaluation harness (v0.28–v0.38) is the flywheel: every retrieval/graph idea below is
now *measurable*, so the highest-value work is what feeds it. Priority reflects thesis
payoff × how cleanly it builds on what exists, tempered by the local-first,
minimal-dependency ethos. **P0** = do next · **P1** = soon · **P2** = later/opportunistic.
Effort: **S**/**M**/**L**.

| Dir | Direction | Priority | Effort | Why |
|---|---|:--:|:--:|---|
| A | Retrieval quality & scale | **P0** | M | Directly attacks the measured weakness; the eval harness scores every change |
| B | Deeper knowledge graph (relations) | **P0** | L | The frontier of the agentic-wiki thesis; where the graph could finally help *retrieval* |
| C | Evaluation breadth & rigor | **P1** | S–M | Cheap insight; de-risks every retrieval/graph claim |
| D | Wiki generation & content quality | **P1** | M | Improves the artifact users actually read |
| E | Fuller editing agent + full incremental graph | **P1** | M | Completes the "living wiki" loop |
| F | Deployment, security & multi-user | **P2\*** | M | Gated on leaving localhost — but write-without-auth is a real risk |
| G | Ingestion fidelity & new modalities | **P2** | S–L | Incremental; the layout upgrade is cheap, multimodal strains the ethos |
| H | Backends & caching | **P2** | S | Protocols already exist; local Ollama suffices |

<sub>\* Direction F is **P2 unless a shared/remote deployment becomes a goal**, at which
point it jumps to P0.</sub>

### A — Retrieval quality & scale (P0)
- **Hybrid retrieval** — fuse a lexical/BM25 signal with dense cosine (helps exact German
  compounds, identifiers, rare terms the embedder blurs).
- **Re-ranking** — a cross-encoder or a single LLM re-rank pass over the top-N; the
  cheapest measurable win, scored directly by `owiki eval`.
- **Query rewriting / expansion** before retrieval, especially for short or relational
  questions.
- **Scale** — back `SemanticIndex.search` with Kuzu's existing HNSW (or an ANN lib) so
  retrieval stops being O(n).
- **Revisit GraphRAG expansion**, now that it's measurable: entity-anchored expansion,
  multi-hop, learned/typed edge weighting — the open question is whether *any* graph
  strategy beats spending the same budget on more semantic hits for *recall*.

### B — Deeper knowledge graph: relations (P0)
- **Typed `Entity→Entity` relations** (subject–predicate–object per page), turning
  co-mention into a real knowledge graph.
- **Corpus-wide entity resolution** → canonical entities with descriptions/aliases.
- **Confidence + provenance** on entities and relations.
- **Relation-aware GraphRAG + agent tools** — answer by *traversing* relations, not just
  listing neighbours. This is the most plausible path to the graph earning its keep on
  relational *retrieval*, not only answer quality.

### C — Evaluation breadth & rigor (P1)
- **A second/third corpus** + an **embedder bake-off** — does the RAG-vs-GraphRAG finding
  generalize beyond German CS + bge-m3?
- **Automated eval-set generation** — LLM drafts questions + ground-truth pages from graph
  page-pairs (human-reviewed), scaling past hand-written sets.
- **Ablations** — which edge types and which `expand_k` the answer-quality win actually
  comes from.
- **Bigger sets + significance**; a **second judge model** to check self-preference bias.

### D — Wiki generation & content quality (P1)
- **LLM-authored page abstracts** / lead paragraphs.
- **Auto-generated glossary + alphabetical index** from the entity layer.
- **Inline cross-links** — render REFERENCES / shared-entity as real wiki links in the
  prose, not just graph edges.
- **Use tables and images** meaningfully in pages and in retrieval.

### E — Fuller editing agent + full incremental graph (P1)
- **Restructuring tools** — delete/rename/move/merge/split page, with slug + link
  integrity.
- **Full incremental graph** — recompute *all* edge types + entities on upsert (today only
  `SIMILAR_TO`), so the live wiki and graph never drift from a stale `graph-build`.
- **Edit history / undo**; richer dry-run diffs in the UI.

### F — Deployment, security & multi-user (P2, conditional)
- **AuthN + roles** (read-only vs read-write) for `serve` and the MCP server.
- **Docker image**, **PyPI publish**, **cross-platform CI** (Linux/macOS) running the
  offline suite.
- **Request logging / basic metrics** in `serve`.

### G — Ingestion fidelity & new modalities (P2)
- Adopt **`pymupdf_layout`** for higher-fidelity PDF structure (already flagged in
  `CLAUDE.md`, low effort).
- **New source types** — DOCX/EPUB, multi-page web crawl (sitemap), Confluence/Notion
  export — each a new parser behind `sources.parse_source`.
- **Multimodal** (image-aware retrieval/answers) — high value but pulls in heavy models;
  weigh against the stdlib/local-first ethos.

### H — Backends & caching (P2)
- Additional **`Embedder`/`ChatModel` backends** behind the existing protocols
  (OpenAI-compatible, llama.cpp) — keep Ollama the default.
- A **persistent cache** for embeddings + LLM calls across runs (speeds eval and rebuilds).

### If you pick one thing next
**Direction A's re-ranking pass** is the smallest change with an immediately measurable
payoff — the eval harness will tell you within one run whether it beats the current
pipeline. **Direction B (relations)** is the higher-ceiling bet: it's the most likely way
to make the graph finally win on *retrieval*, not just answer quality — which would be the
project's next real finding.

## Consolidation layer + usage-memory (Path A/B) — era 12 detail

Toward using the graph as agent memory, borrowing Microsoft GraphRAG's best ideas natively
rather than adopting the library. **Path A is complete and validated across CLI/browser/MCP
(v0.39–v0.45); Path B is opened** (v0.43). Framed as two paths:

- **Path A — in-repo consolidation layer.** Community detection + LLM community summaries
  + global search + (later) time-decayed edges. Native, local, dependency-free, measurable.
- **Path B — pivot to an agent-memory store.** Invert the data flow (sessions in),
  authoritative mutable graph, session→subgraph→merge, time/decay + contradiction
  versioning + a "sleep" consolidation job. The novel surface (nobody ships decay +
  contradiction) — the real second brain.

**Landed (Path A, first slice, v0.39):** `openwiki communities` runs a re-runnable
consolidation over the built graph — a compact, dependency-free weighted-modularity
**Louvain** (`graph/community.py`) partitions the Page↔Page graph
(SIMILAR_TO∪REFERENCES∪shared-entity), then a *local* chat model writes one summary per
community (cheap: a handful of calls, not one per page), stored as `Community` nodes +
`IN_COMMUNITY` edges. **`ask --global`** answers thematic "what are the main themes / how do
they relate" questions from those summaries — global sensemaking that chunk-RAG can't do.
Verified live on the informatik corpus (78 pages → 7 communities → a coherent 7-theme
overview). The **Projekt tab** shows a *Themen (Communities)* section (label + size + summary cards,
`/api/communities`) with a **global-search box** (`/api/global` → `ask_global`) that answers
a thematic question from the summaries and highlights the cited themes; community labels are
the model's own theme (not the hub page's title). Global search is also an **MCP**
`wiki_global` tool (advertised when the graph has communities + a chat model), so coding
agents get whole-corpus sensemaking alongside `wiki_ask`.

**Time/decay usage-memory (v0.43, Path B's first step):** the graph now has a decaying
`REINFORCES` edge overlay (`graph/decay.py` — pure exponential decay + capped
reinforcement). GraphRAG expansion on a **writable** graph (serve/chat) strengthens a
seed→pulled-in edge (Hebbian); `neighborhood`/expansion rank reinforced neighbors by
*effective* (time-decayed) weight; `openwiki decay` ages every edge to now and prunes the
faded ones (forgetting). Read-only `ask`/MCP never write. Verified live on informatik:
repeated retrieval accumulated weight (2.0 for a twice-used edge vs 1.0), and an aggressive
decay pruned all of it.

**Thematic eval (v0.44) — the community layer pays off, measured.** `owiki eval --global`
scores **global search** on a thematic question set (`eval_thematic.jsonl`: broad "how do X
and Y relate / what are the themes" questions): it generates a global answer and scores its
`[n]` **community** citations against the ground-truth communities (those covering an
expected page, via `IN_COMMUNITY`) — cite-hit / community-recall / community-precision
(`eval.run_global_eval` / `community_grounding`). On informatik (10 questions): cite-hit
**100%**, community-recall **95%**, precision **56.7%** (broad questions legitimately span
themes), and — the headline — an LLM judge preferred **Global over plain RAG 9–1**. So the
community layer earns its keep on exactly the question class it was built for, the way
GraphRAG earns its on answer quality.

**Graph-tab community colouring (v0.45)** completes Path A's UI: the explorer colours page
nodes by their community (`_page_gnode` carries the id → `communityFill`/`COMMUNITY_PALETTE`),
with a swatch legend + a "Themenfarben" toggle, and the `reinforced` usage edges got a filter
chip too. **Path A is now complete and validated across CLI/browser/MCP.** **Path B** — turning
the graph from a document *mirror* into agent *memory* — is designed from first principles in
the next section.

## Path B — the second-brain memory model

*Distilled from a design discussion on memory evolution (Kauffman networks → DNA/epigenetics →
brain consolidation → human+LLM systems) and the architecture analysis in `docs/arc42/`
(ADR-3, ADR-8; §11 D1/D2/D6). This is the North-Star design for turning OpenWiki's graph from a
document **mirror** into agent **memory**. Framing is inspiration; the engineering is in the
tables and stages.*

> **Deep design:** this section is the overview. The full design base — target architecture,
> proposed data model, per-stage detail (goal / build / builds-on / hard part / exit criterion),
> the evaluation strategy, and the open decisions — is in **[`docs/path-b-memory.md`](path-b-memory.md)**.

### The frame: memory as a self-organizing model of the world

One pattern recurs across scales — a system persists by building an internal model of its
environment and holding it at the **edge of chaos**: ordered enough to remember, plastic enough
to adapt.

- **Kauffman / Boolean networks** — genes self-organize into **attractors** (stable cycles)
  without a controller; each attractor is a cell type. Order emerges near K≈2, the edge of chaos.
- **The cell** — attractors are compressed imprints of environmental regularities; **epigenetics**
  (methylation/histones) are the *locks* deciding which genes are read **right now** — memory as
  selective activation, not just storage.
- **The brain** — a fast, capacity-bounded buffer (**hippocampus** ≈ the context window)
  consolidates during **sleep** into slow, structural long-term memory (**cortex** weights); it
  survives by **forgetting** ~99% and keeping the *structure* of experience, not the transcript.
- **The next layer** — humans (analog sensors + will) coupled with LLMs (a crystallized digital
  model of human knowledge) as an **exocortex**, whose central unsolved problem is *memory
  between sessions*.

Path B takes this literally: **the graph is the cortex; a session is a day; consolidation is sleep.**

### Biological blueprint → OpenWiki realization

| Biological mechanism | Role | OpenWiki realization | Status |
|---|---|---|---|
| Attractor (stable state) | a consolidated "concept" | `Community` node + LLM summary | ✅ Path A |
| DNA (stable code) | identity / invariants | project manifest + agent-identity grounding | ⚠️ partial |
| Epigenetics (methylation) | selective activation of context | per-query subgraph activation (GraphRAG expansion) + decay-weighted ranking | ⚠️ partial |
| Hebbian "fire together, wire together" | reinforce what's used | `GraphStore.reinforce()` on retrieval | ✅ v0.43 (writable-only) |
| Synaptic decay / pruning | forgetting | `GraphStore.decay()` (exp. half-life + prune) | ✅ v0.43 |
| Hippocampus | short-term / working buffer | the session + context window | ❌ not modeled |
| Cortex | long-term structural memory | the persistent Kuzu graph | ⚠️ a *mirror*, not authoritative (ADR-3) |
| Sleep consolidation | compress day → structure | the offline "sleep pass" (`communities`) | ⚠️ runs over docs, not sessions |

### The three-tier memory (what a new session's context is assembled from)

1. **DNA tier — identity.** Small, stable: who the user/agent is, invariants, global instructions
   (≈ the manifest + a persistent identity doc).
2. **Epigenetic tier — activation.** Per query, "methylate" (hide) the irrelevant sub-graph and
   "demethylate" (surface) the relevant one, weighted by *decayed usage* (≈ GraphRAG expansion
   over `SIMILAR_TO`/`REINFORCES`, ranked by effective weight).
3. **Attractor tier — consolidated memory.** The compressed meta-nodes (community summaries) that
   carry the *structure* of past experience, not its transcript (≈ Path A communities).

Assembling a session's context = identity (always) + the activated sub-graph (epigenetic) + the
relevant attractor summaries — **concentrate, don't replay**.

### The core algorithm: merge a session sub-graph into the world model

Each session becomes a small typed sub-graph; consolidation merges it into the persistent
macro-graph in four phases. **Phases 3–4 are where every off-the-shelf store — Neo4j, Kùzu, even
Microsoft GraphRAG — stops**, so they are Path B's real contribution.

| Phase | What it does | OpenWiki today | Path B work |
|---|---|---|---|
| 1. **Entity resolution** | anchor session nodes to existing ones (semantic + name) | entity normalization + `hybrid_search` | wire in as an explicit merge step |
| 2. **Hebbian weighting + decay** | strengthen confirmed links, fade unused | `reinforce()` / `decay()` | apply on merge; extend past the read-path |
| 3. **Contradiction harmonization** | new facts supersede old (non-monotonic) | — | **the novel piece**: time-versioned edges (`valid_from` / `superseded_by`); retrieval prefers the latest valid assertion |
| 4. **Abstraction / compression** | collapse detail into meta-nodes | `communities` (Louvain + summaries) | run over the *merged* graph, incrementally |

### Staged plan

Ordered so each stage is shippable and measurable (the project's discipline — see *Future
directions*). Stages re-open the decisions that flagged themselves for exactly this.

- **B0 — Reframe (re-opens ADR-3, debt D1). ✅ Landed (v0.48).** The graph is now *authoritative*
  for remembered content: `graph-build`/`build` **preserve** the memory tier across a doc rebuild
  (snapshot → rebuild → restore). A **session/experience** source type (`type = "session"`,
  `init/add-source --session`) feeds it *alongside* documents via a new `build` **memory** stage, and
  a per-project **Wiki vs Second-Brain mode** (`[memory] enabled`, default off) gates it — a
  remembered tier on top of the doc tier, not a replacement (arc42 ADR-14 / `path-b-memory.md` §3.1).
  *Still deferred within B0:* the full tiered write-authority model. Was the highest-leverage,
  highest-risk stage.
- **B1 — Read-path reinforcement (re-opens ADR-8, debt D2). ✅ Landed (v0.49).** Plain read-only
  `ask`/MCP now reinforce usage — they append the seed→related pairs they retrieve to an append-only
  **usage log** (`graph.usage.jsonl`) that the next writer (`serve`/`chat` startup, or `openwiki
  decay`) folds into `REINFORCES` edges — sidestepping Kuzu's exclusive write lock. Gated by Second
  Brain mode. Memory now grows where use actually happens, not just serve/chat.
- **B2 — Session capture → sub-graph.** An LLM turns a conversation into a typed sub-graph
  (entities + relations + provenance + timestamp). *(First slice landed, v0.46: flat
  subject–predicate–object capture into reified `Assertion`s.)*
- **B3 — Merge operator.** Phases 1–2 (entity resolution + Hebbian) into the world model.
  *(First slice landed, v0.46: dedup-only merge by normalized key.)*
- **B4 — Contradiction / time-versioning (debt D6). ✅ Landed (v0.50).** Phase 3 — the belief-revision
  layer; the genuinely novel, unshipped-anywhere contribution. A newer fact (same subject+predicate,
  different object) **supersedes** the older via a `SUPERSEDES` edge; recall returns the current fact,
  the superseded history stays queryable (`recall --all`), and re-asserting an old fact revives it.
- **B5 — Sleep job.** Phase 4 over the merged graph (communities + decay + abstraction) as a
  scheduled consolidation pass.
- **B6 — Three-tier context assembly.** Build a session's context from identity + activation +
  attractors — the payoff: *load the concentrate, not the log*. *(First slice landed, v0.46:
  decay-weighted `recall` — the activation tier only.)*

**First slice landed (v0.46).** A thin **B2→B3→B6** vertical ships as the `remember`/`recall`
commands: capture a transcript into `Session`/`Assertion` graph tables, dedup-merge it, and recall
facts by decay-weighted cosine — a two-session proof-of-loop (offline tests + a live informatik run).
Still doc-derived (B0 deferred) with no contradiction handling (B4 deferred).

**Headline metric landed + first result (v0.47).** `owiki eval --cross-session` implements §7's
three-condition test (cold / raw-log / assembled) over a scenario set, isolating each scenario in a
throwaway graph. First 7-scenario run (qwen3 + bge-m3): **assembled 100% · raw-log 85.7% · cold 0%**
task success, and an LLM judge preferred **assembled over raw-log 3–1** — memory helps the next
session, and *concentrating* it (recall) beats *replaying* it (raw log). Small-N caveats aside, that's
the green light for the hard stages (B0 authoritative graph, B4 contradictions). Design + full result:
`path-b-memory.md` §7.

**B0 — authoritative graph landed (v0.48).** Acting on that green light: the memory tier now
**survives document rebuilds** (`GraphBuilder` snapshots Session/Assertion/ASSERTS + the `REINFORCES`
overlay and restores them into the fresh schema — unit-tested *and* verified live with
`build --only graph --force`). A per-project **Wiki vs Second-Brain mode** (`[memory] enabled`, default
off) gates memory, and a **session source type** (`init/add-source --session`) is captured by a new
`openwiki build` **memory** stage. Path B is no longer doc-derived — experience persists. See
`path-b-memory.md` §6/B0.

**B1 — read-path reinforcement landed (v0.49).** Ordinary read-only use now teaches the graph:
`ask`/MCP append the seed→related pairs they retrieve to an append-only **usage log**
(`graph.usage.jsonl`), which the next writer (`serve`/`chat` startup, or `openwiki decay`) folds into
`REINFORCES` edges — no lock contention on the read path. Gated by Second Brain mode; verified live
(two asks → `decay` folds 2 records into 2 edges). See `path-b-memory.md` §6/B1.

**B4 — contradiction / time-versioning landed (v0.50).** The belief-revision layer, done boring &
tractable: a newer fact with the same normalized **subject+predicate** but a **different object**
adds a `SUPERSEDES` edge over the old one (nothing deleted — "current" = no incoming `SUPERSEDES`, so
validity intervals are derivable). `recall` returns **current facts only** by default (the agent gets
the live fact, not the stale one), `recall --all` shows the superseded history flagged, and
re-asserting a superseded fact **revives** it. Preserved across `graph-build` (B0). Verified live —
`remember` port 8080 then 9090 supersedes the 8080; `recall` returns only 9090 *even though the stale
fact scores a higher cosine* (supersession beats similarity). Next: **B5** (sleep consolidation) and
the full three-tier **B6** assembly. See `path-b-memory.md` §6/B4.

### Honest guardrails

- **Contradiction (Phase 3 / B4) is belief revision** — a decades-old AI problem, not a graph
  feature. The tractable version is boring and real: version edges by time/validity and prefer the
  newest valid assertion; skip the "simulate the network until it goes chaotic" metaphor.
- **Kauffman is inspiration, not an algorithm.** The operationalizable residue is one knob:
  **keep the graph at useful density** (decay + pruning) = the "edge of chaos".
- **Measure every step.** The graph's value has been counter-intuitive before (it does *not* help
  local recall; it *does* help answer quality + global search). Path B's claim — "does memory make
  the agent better in the *next* session?" — must be measured the same way, or it's just poetry.
  This likely needs a new eval axis (cross-session task success), not the current single-shot sets.

## Related docs

- `docs/arc42/` — full **architecture documentation** (arc42: goals, constraints, context,
  building blocks, runtime, deployment, concepts, ADRs, quality, risks).
- `docs/path-b-memory.md` — the **Path B (agent memory)** deep design base.
- `docs/projects.md` — the projects layer design + phase roadmap.
- `docs/RAG-vs-GraphRAG.md` — the full evaluation writeup (methodology + numbers).
- `docs/coding-agents.md` — MCP / OpenCode / Claude Code setup.
