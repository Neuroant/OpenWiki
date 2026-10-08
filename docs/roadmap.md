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
  51/306 SIMILAR_TO/122 REFERENCES + 801 entities) and the informatik CS lecture, built with
  the full graph (`--relations --resolve-entities`): 16 PDFs → 799p → 76 pages → 2703 chunks →
  graph 76/760 SIMILAR_TO/32 REFERENCES/1423 canonical entities (from 1520 raw)/1953 MENTIONS/
  1364 typed RELATED_TO/6 communities.
- **Tests:** 386 passing, fully offline (fakes for Ollama/Kuzu), in CI on every push.
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
- ✅ **Hybrid retrieval landed (v0.62)** — a pure, stdlib **BM25** (`lexical.py`, inverted-index,
  no `rank-bm25` dependency) fused with the dense cosine ranking via **reciprocal rank fusion**
  (`SemanticIndex.search_hybrid`), wired into `ask --hybrid` and `owiki eval --hybrid`.
  **Measured on NAUTILUS: it ties pure dense** — identical MRR/hit/recall at every budget (1, 2, 8) —
  because bge-m3 already handles the German terms + acronyms (RPPR/USB/Arpeggiator all found by dense),
  so there's nothing for BM25 to rescue. Crucially it doesn't *hurt* (unlike re-ranking), and a unit
  test shows it genuinely rescues an exact-term page an embedder is blind to. **Then confirmed on a code
  corpus (v0.62):** ingesting OpenWiki's own source as a `--repo` (49 files, 14 identifier questions),
  hybrid **wins decisively** — hit@1 **57.1% → 85.7%** (+28.6 pts), MRR 0.74 → 0.91 — because code is
  exact-identifier-heavy and bge-m3 is a *text* embedder (it can't tell `search_hybrid` from
  `hybrid_search`; BM25 can). So the retrieval story is complete + honest: on prose, bge-m3's dense
  ranking is the strong baseline that graph expansion, LLM re-ranking, and BM25 fusion each fail to beat;
  on **code**, hybrid is the clear win. Lesson: match the technique to where the embedder is weak, and let
  `owiki eval` decide (full writeup: `docs/RAG-vs-GraphRAG.md` Finding 4; eval set: `examples/code-eval.jsonl`).
  *(Still open: score-fusion with a tunable dense/lexical weight.)*
- ✅ **Re-ranking landed (v0.61)** — a single **LLM re-rank pass** (`rerank.py`, on-ethos: reuses the
  local chat model, no cross-encoder dependency): fetch a wider candidate pool, one chat call orders it
  by relevance, keep the budget. Wired into `ask --rerank` and, crucially, **`owiki eval --rerank`** (a
  RAG+Rerank row) so it's *measured*, not assumed. **First result: it does not help** on a small NAUTILUS
  navigational set — recall was already saturated (100%) and MRR *dropped* (0.81 → 0.57–0.60 with **both**
  a 14b and a 30b re-ranker), i.e. the LLM demotes the page bge-m3 already ranked top. Same shape as the
  RAG-vs-GraphRAG finding: on this corpus the embedder's ranking is already strong. Re-ranking would more
  plausibly pay off on **harder/ambiguous** queries (where the top cosine hit is wrong) or with a real
  **cross-encoder** — both now testable via the harness. *(Still open: a cross-encoder backend; a labeled
  hard-query set.)*
- **Query rewriting / expansion** before retrieval, especially for short or relational
  questions.
- **Scale** — back `SemanticIndex.search` with Kuzu's existing HNSW (or an ANN lib) so
  retrieval stops being O(n).
- **Revisit GraphRAG expansion**, now that it's measurable: entity-anchored expansion,
  multi-hop, learned/typed edge weighting — the open question is whether *any* graph
  strategy beats spending the same budget on more semantic hits for *recall*.

### B — Deeper knowledge graph: relations (P0)
- ✅ **Typed `Entity→Entity` relations landed (v0.63)** — a second per-page LLM call extracts
  subject–predicate–object triples *among that page's entities* (`entities.extract_relations`),
  grounded to the extracted entities (unresolved/self dropped), merged across pages (predicate +
  weight + provenance), and stored as **`RELATED_TO {predicate, weight, pages}`** edges (always-created,
  opt-in via `graph-build --relations` / `[graph] relations`). Surfaced: `GraphStore.relations_for_entity`
  / `relations_for_page`, the `find_entity` agent/MCP tool lists relations, and the **Graph tab** draws
  typed entity→entity edges (predicate on hover) with their own filter. Co-mention is now a real,
  traversable knowledge graph.
- ✅ **Corpus-wide entity resolution landed (v0.66)** — `resolve_entities` (`--resolve-entities`)
  merges same-concept surface variants the per-page normalizer misses into **canonical** entities
  with `aliases` + an LLM `description`: block by type → embedding candidate clusters (cosine ≥ 0.80,
  calibrated for bge-m3) → one LLM call per multi-member cluster to confirm/split (bounded — singletons
  free; never drops an entity). `Entity` nodes carry `description`/`aliases`, and `pages_for_entity` +
  `find_entity` match aliases (search an acronym/synonym → the canonical). Verified live (`Drumkit` +
  `Drum Kit` → canonical *Drum Kit* aka *Drumkit*). **Honest limit:** acronym↔full-form (`IFX`↔`Insert-Effekt`
  ≈ 0.40 cosine) isn't embedding-close, so it isn't a candidate — resolution catches spelling / spacing /
  plural / word-order / near-synonym variants, not acronyms.
- **Confidence + provenance** on entities and relations *(relations carry weight + provenance pages; resolved entities carry aliases + a description)*.
- ✅ **Relation-aware GraphRAG landed (v0.64)** — GraphRAG expansion now traverses the typed
  relations: `neighborhood` gains a `relation` group (pages connected via
  `MENTIONS→RELATED_TO→MENTIONS`), added to `agent._EXPAND_RELS` so both `ask` and `owiki eval`
  expand along it, and `graph_neighbors` lists it ("related (typed)"). Placed *last* in the dedup
  order, so it surfaces exactly the pages similarity/structure/shared-entity *don't* — the
  connections only the knowledge graph knows. Verified: the relation channel retrieves a page
  reached solely via a typed relation (unit test with orthogonal embeddings; live on a synth
  corpus, *Arpeggiator →controls→ Drumkit* pulls the Drumkits page). Whether it moves *retrieval
  recall* on a given corpus is, as ever, an `owiki eval` question — its clearest value (per the
  RAG-vs-GraphRAG findings) is answer quality + explainable traversal, and it needs a
  **relation-targeted eval set** to measure rigorously.
- **Agent relation traversal** — `find_entity` already lists an entity's typed relations, so the
  agent can answer "what does X control?" by reading them; a dedicated entity-path tool is a
  possible follow-up.

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
- ✅ **CI landed (v0.64).** GitHub Actions (`.github/workflows/ci.yml`) runs the offline suite
  on every push/PR to `main` across a **Python 3.11–3.13** matrix (`ubuntu-latest`; `-e .[dev]`
  → PyMuPDF + NumPy + Kuzu + pytest) + **builds the Docker image** (smoke `owiki --version`). The
  suite is offline by design — Ollama tests skip when no server is reachable, graph tests use a
  fake embedder (+ `importorskip` for Kuzu), network is faked, and the sample PDF is committed so
  the parser tests run — so CI exercises ~all of it on Linux (proving it isn't Windows-locked). First
  run went green on all three legs.
- ✅ **Packaging landed (v0.65).** PyPI-ready: distribution name **`owiki`** (the `openwiki` name is
  taken; the *import* package stays `openwiki`), enriched metadata + classifiers, builds clean
  (`python -m build` → sdist + wheel that includes `web/static` and excludes the 4 MB sample PDF;
  `twine check` passes). Ships a **`Dockerfile`** (`python:3.13-slim`, targeted copy, `owiki`
  entrypoint) + `.dockerignore` + `docker-compose.yml` (serve against a host Ollama), CI-built. A
  **manual** `Publish to PyPI` workflow (`workflow_dispatch`, OIDC trusted publishing) is ready but
  **gated**: no license is chosen yet (the package carries `Private :: Do Not Upload`), so publishing
  waits on a license decision + removing that classifier + configuring the PyPI trusted publisher.
  *(Still open: the actual PyPI publish once licensed; a Windows/macOS CI matrix leg.)*
- ✅ **Observability landed (v0.58).** An in-process metrics collector (`metrics.py` — a
  bounded, thread-safe ring buffer + `parse_ollama_stats`) captures the per-call **latency +
  token counters Ollama already returns but the code discarded** (`OllamaChat`/`OllamaEmbedder`
  now record a chat/embed event; every `/api/*` request records an http event). Surfaced three
  ways: the web **System tab** (`/api/metrics` — per-kind p50/p95 + token totals + a live event
  table), **per-turn chat telemetry** in the agent panel, and a CLI **`ask` `⏱` footer**
  (latency · tokens · tok/s). Immediately useful — it exposes that one agent "question" is
  several model calls, and that a slow first answer is mostly cold-model `load` time.
- ✅ **Pipeline/build observability landed (v0.60).** Each `openwiki build` stage now records its
  **wall time + LLM token spend** (the metrics-collector delta over the stage, via
  `_stage_start`/`_finish_stage`) into `BuildState`, surfaced by `openwiki status` and the Projekt
  tab's build table (Dauer / LLM columns). Immediately shows where a build's time + tokens go —
  e.g. entity extraction (one chat call/page) and memory capture dominate, while ingest/wiki are
  instant. *(Still open: request logging to disk.)*

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

### I — World-model analysis (P1, in progress)
A built-in toolkit to **measure, compare, and analyze the structure and organization of
the gathered knowledge** — treating OpenWiki's two representations of the same corpus (the
symbolic **graph** and the **semantic space**) as facets of one object. *Analysis is to
structure what `owiki eval` is to retrieval.* Read-only + additive; core is pure NumPy, with a
heavier **`[analysis]` extra** (scikit-learn now; umap/networkx later) opt-in.
- ✅ **P1 — graph↔semantic coupling landed (v0.67)** — `openwiki/analysis/coupling.py` +
  `owiki analyze` (report + `--json` fingerprint), offline. Measures where the graph *agrees*
  with the embedding geometry (redundant) vs. *adds* non-semantic structure: per-edge-type
  endpoint-cosine profile vs. a random-pair null, graph-vs-kNN neighbor overlap, Louvain
  **community_coherence** (silhouette + ARI, sklearn-optional), and the headline **graph_reach**
  — the fraction of the graph's non-similarity connections the embedder would never rank as
  neighbors. Extends the RAG-vs-GraphRAG thesis from "does the graph help retrieval?" to "how
  much structure does the graph encode that similarity alone misses?" *First measured on NAUTILUS:
  **~36%** non-semantic reach; the embedding space is strongly **anisotropic** (random-pair cosine
  ≈ 0.74, so lift-over-null is the real signal); communities only weakly separate in embedding
  space (silhouette +0.09) and only partly match k-means clusters (ARI +0.39) — the graph organizes
  along axes the geometry doesn't fully capture.*
- ✅ **P2 — the Analyse tab landed (v0.68)** — the visual layer in the web UI: the coupling
  metric table (endpoint cosine vs. null + kNN overlap per edge type), the **graph-reach headline**,
  community coherence, and a hand-rolled SVG **2-D semantic map** — pages projected by PCA (or UMAP
  with the extra, `openwiki/analysis/projection.py`), coloured by community, with graph edges overlaid
  and per-edge-type toggles (references/relation on by default; click a node → open the page). Backed
  by `WikiWebApp.analyze()` → `/api/analyze`. Read-only + offline. *The reference edges visibly span
  long distances across the embedding layout — the 36% non-semantic reach, made visual.* Remaining
  P2 polish (histograms, a community×edge-type heatmap) is optional.
- ✅ **P3 (gap-mining) landed (v0.69)** — the **analysis→improvement loop**: `owiki analyze gaps`
  (`openwiki/analysis/gaps.py`), an offline, ranked to-do list — **link_candidates** (pages that
  co-mention entities but have no reference edge → missing cross-refs), **redundant_pages**
  (near-duplicate embeddings → merge candidates), **isolated_pages** (semantic outliers + structural
  orphans), **entity_merge_candidates** (same-type near-duplicate names via `difflib`, with a
  numbered-sibling guard so `Effect Control 1`≠`2`). *On NAUTILUS it surfaced a real source typo
  (`SEQUECER`), spacing variants (`Drum Kit`≈`Drumkit`), plural pairs the normalizer missed, and two
  pages both titled "Quick Layer/Split" (cos 0.97).*
- ✅ **P3b (compare/diff) landed (v0.70)** — `owiki analyze --compare PATH` diffs the current coupling
  fingerprint against another KB — a saved `analyze --json` file (snapshot/time-travel), a project dir, or
  an output dir (computed live) — printing an A/B/Δ table + the notable rate deltas
  (`openwiki/analysis/compare.py`, pure/testable). The metrics are *relative*, so it compares across
  corpora / versions / embedders / settings — the "measure + **compare**" the direction is named for.
  *(Verified isolating the k-dependent overlap metrics: current k=20 vs a saved k=8 fingerprint differs
  only on the neighbor-overlap rows, everything else Δ=0.)*
- ✅ **P4 (memory-tier dynamics) landed (v0.71)** — `owiki analyze memory`
  (`openwiki/analysis/memory.py`) analyzes the **Path B remembered tier** — the part of the world model
  that *learns over time*: **revision** (SUPERSEDES rate — belief overwritten), **consolidation**
  (fraction of facts folded into B5 themes + theme-size shape), **temperature** (hot/warm/cold by decayed
  `effective_weight` + per-fact `confidence` re-affirmation), **breadth** (distinct subjects/predicates +
  top predicates), and **growth** (facts per session). Graph-only, gated on `has_memory()`. *Verified live
  on a throwaway 3-session tier: a Port 8137→9000 contradiction → 17% revision; a re-affirmed fact → mean
  confidence 1.2; 4 hot / 1 warm by recency; and a consolidation pass lifting coverage to 100%.* **Direction I
  (world-model analysis) is complete (P1–P4):** measure (coupling), see (Analyse tab), improve (gaps),
  compare (fingerprint diff), and the learning tier's dynamics (memory).

### J — Web UI: surface the features (P1, in progress)
The CLI/back-end outran the browser; this direction closes the gap.
- ✅ **U1 — Ask mode landed (v0.72)** — the chat pane gained an **Agent | Ask** toggle. *Ask* is
  read-only RAG (`WikiWebApp.ask` → `/api/ask`) with a controls row (**GraphRAG / Hybrid / Re-rank /
  Global / k**), rendering the answer + clickable **seed vs. +Graph** source chips + a ⏱ stats line —
  the browser twin of CLI `ask`, making the measured retrieval variants interactive. Verified live on
  the informatik corpus (answered a software-architecture question with cross-book graph expansion).
- ✅ **U2 — Analyse tab completed (v0.73)** — split into three sub-tabs: **Kopplung** (coupling + map),
  **Lücken** (P3 gaps — missing cross-refs / near-duplicates / isolated pages / entity-merge candidates,
  with clickable page refs), and **Dynamik** (P4 memory-tier dynamics — revision / consolidation /
  temperature / breadth / growth). Backed by `/api/analyze/gaps` + `/api/analyze/memory` (thin wrappers
  over the existing `analyze_gaps`/`analyze_memory`). Verified live on informatik.
- ✅ **U3 — entity/concept browser landed (v0.74)** — a new **Begriffe** tab: a searchable, type-filtered,
  mention-ranked list of **canonical entities**, and a detail pane with the entity's description + aliases
  (from resolution), its mention pages (clickable), and its typed relations (walk entity→entity). Backed by
  `GraphStore.list_entities`/`entity_detail` → `/api/entities` + `/api/entity/{name}`. Surfaces the
  resolution + relation layers as a first-class view. Verified live on informatik.
- ✅ **U4 — source filter + provenance landed (v0.75)** — `manifest()` tags each page with its `source`
  (merged source file) + `book` (`sources/` subfolder, via the project's source order); the sidebar gains a
  **source/book filter** that scopes the nav + search, and search hits show their origin source. Verified
  live on informatik: filters the lecture (76p) vs. *Lehrbuch der Softwaretechnik* (43p).
- ✅ **U5 — hybrid sidebar search landed (v0.76)** — a **Hybrid (BM25 + Vektor)** toggle by the search box
  (`/api/search?hybrid`), surfacing the RRF fusion in the browser.
- ✅ **U6 — global search** — already delivered by U1 (the Ask mode's **Global** toggle → `/api/global`).
- ✅ **U8 — dark mode landed (v0.76)** — a header theme toggle (light/dark via `:root[data-theme=dark]`
  CSS-var overrides), persisted in localStorage, applied pre-paint (no flash).
- ✅ **U9 — citation UX** — already delivered by U1 (Ask answers show clickable **seed vs. +Graph** source chips).
- ✅ **U10 — collapsible panels landed (v0.77)** — header toggles collapse the sidebar + chat (each
  pinned to its own grid column so hiding one doesn't reflow the others), persisted in localStorage —
  a focus/reading mode + better narrow-screen use.
- ✅ **U11 — Analyse-map enrichment landed (v0.77)** — the semantic map gained a **PCA/UMAP** projection
  toggle (`/api/analyze?method=`) and a **community focus** dropdown (isolate one theme, dim the rest);
  also fixed a U2 regression where the map's edge toggles queried a stale container id.
- ✅ **U7 — streaming Ask answers landed (v0.78)** — the Ask (RAG) mode now streams token-by-token:
  `OllamaChat.chat_stream` (Ollama `stream=true`) → `RAGAgent.stream` (sources, then deltas, then the
  cleaned answer) → `WikiWebApp.ask_stream` (SSE event dicts; graph lock held only around retrieval) →
  a `POST /api/ask/stream` **Server-Sent-Events** endpoint → a `fetch`+`ReadableStream` client that
  appends deltas live and finalizes into markdown + seed/+Graph chips + stats. The Agent (tool/editing)
  mode stays blocking. **Direction J (web UI) is complete (U1–U11).**

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
- **B5 — Sleep job. ✅ Landed (v0.51).** Phase 4 over the memory tier: `openwiki consolidate`
  clusters the current facts (Louvain over an assertion-similarity graph), LLM-summarizes each into a
  `MemoryConcept` theme, then folds usage + decays — re-runnable + bounded (themes replaced, not
  accumulated), enabling global search over memory. *(Deferred: incrementality, k-core-vs-Louvain
  stability, per-tier half-lives.)*
- **B6 — Three-tier context assembly. ✅ Landed (v0.52).** The payoff: `context_for(query)` fuses
  **identity** (project/`[memory] identity`) + **activation** (decay-weighted `recall`) + **attractors**
  (the B5 themes the recalled facts belong to) into an assembled session context — *load the
  concentrate, not the log*. Exposed as the `context` CLI + MCP `wiki_memory`; the cross-session eval's
  "assembled" condition is now this assembler (assembled 100% > raw-log 87.5% > cold 0%). *(Deferred:
  host-hook auto-injection, confidence weighting, a fixed-token budgeter.)*

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
fact scores a higher cosine* (supersession beats similarity). See `path-b-memory.md` §6/B4.

**B5 — sleep consolidation landed (v0.51).** The memory-tier analog of `communities`: `openwiki
consolidate` clusters the current remembered facts by embedding similarity (Louvain), LLM-summarizes
each cluster into a `MemoryConcept` theme, then folds usage + decays — the "compress the day into
structure, forget the noise" pass. Re-runnable and **bounded** (themes are a derived view, replaced
each run). Verified live: 9 facts → 3 coherent themes, `answer_global` over them produced a global
answer over memory, and a second pass stayed at 3. See `path-b-memory.md` §6/B5.

**B6 — three-tier context assembly landed (v0.52); Path B complete.** The payoff: `context_for(query)`
fuses **identity** (project / `[memory] identity`) + **activation** (decay-weighted `recall`) +
**attractors** (the B5 themes the recalled facts belong to) into one assembled session context —
*load the concentrate, not the log*. Exposed as the `context` CLI command and the MCP `wiki_memory`
tool, and the cross-session eval's "assembled" condition is now this assembler: **assembled 100% >
raw-log 87.5% > cold 0%** (8 scenarios) — assembled memory beats both replaying the log and starting
cold. **The B0–B6 staged plan is complete.** See `path-b-memory.md` §6/B6.

**Host-lifecycle auto-injection landed (v0.53).** The first B6 refinement: `owiki claude-code --hooks`
wires memory into the Claude Code session lifecycle — **`UserPromptSubmit` → `owiki hook inject`**
(assemble `context_for` for the prompt → injected via stdout) and **`SessionEnd`/`PreCompact` →
`owiki hook capture`** (parse the transcript → `remember`). The `hook` command is strictly **fail-soft**
(always exits 0 — exit 2 would reject the prompt; degrades to no-op without memory). So memory now flows
automatically: recalled *into* each turn, captured *out of* each session. Verified live end-to-end.

**Per-fact confidence weighting landed (v0.54).** The second B6 refinement: each remembered
`Assertion` carries a **confidence** — **re-affirming** a fact (a dedup hit across sessions) reinforces
it (`reinforced_weight`) + refreshes `last_seen`, and `recall` weights by a **gentle, log-scaled**
confidence lift (`confidence_weight`: conf 1→1.0, 3→1.16, 10→1.33) **decayed by recency**. It's a
*tie-breaker* among similarly-relevant facts, not a relevance override — a first cut used the raw
confidence as the multiplier and let a thrice-affirmed fact hijack an unrelated query; the log-scaled
version keeps cosine dominant (a one-off fact is unchanged at weight 1.0). Migrated on old graphs
(`ALTER`) + preserved across rebuild (B0).

**Fixed-token context budgeter landed (v0.55).** The last B6 "hard part": `assemble_context` /
`context_for` now fit the three tiers to a **char budget** (~4/token, dependency-free — no tokenizer):
identity first (truncated if it alone overflows), then facts (the majority share), then themes (the
remainder), with graceful truncation — **facts prioritized over themes** under pressure. Defaults to
`[memory] context_budget` (2000 chars) and bounds the `context` CLI (`--max-chars`), the auto-inject
hook, and the MCP `wiki_memory` tool. Verified live (default 819 chars; `--max-chars 200` → identity +
top fact, themes dropped).

**Incremental + stable consolidation landed (v0.56); the §8 k-core decision resolved.** B5's "hard
part" (incrementality) + the stability critique, together: `consolidate` **warm-starts** clustering
from the prior partition (`detect_communities(seed=…)`) so a re-run doesn't drift and an edit stays
local, and a theme whose member set is unchanged **reuses its summary** — only new/changed clusters
cost an LLM call (`--resummarize` forces a full rebuild). Verified live: re-consolidating unchanged
memory did 0 summaries (all reused); adding one fact re-summarized only its cluster (1 summarized, 1
reused). **Resolved: warm-start Louvain, not k-core** — k-core gives a coreness hierarchy, not topical
themes; warm-start delivers the same stability while keeping the modularity objective (one clustering
path shared with Path A).

**Concurrent reader-and-writer model landed (v0.57) — the last Path B refinement.** First the
constraint, *measured*: Kuzu 0.11 is **reader-XOR-writer** (a writable connection blocks all readers,
and readers block a writer — no simultaneous read+write exists in Kuzu). So "true simultaneity" is
unreachable *in Kuzu*, and the append-only log was the right shape all along. v0.57 generalizes it into
a **lock-free write-ahead journal** and flips the lock holder: **`serve`/`chat` open read-only** so many
readers (`ask`/MCP/`recall`/`context`, a second `serve`) run **concurrently**, and *all* memory writes —
reinforce pairs (`usage.jsonl`), plus `remember` / host-`capture` / chat-edit re-sync as self-contained
`remember`/`reindex` ops (`journal.jsonl`) — **queue instead of blocking or failing**; a writer folds
them (`fold_journal`) at `serve`/`chat` start+shutdown, in `decay`, or on the next `remember`. Writable
opens **retry-with-backoff**; `--sync` restores the old held-writable mode. Trade-off: a chat-edit's
*graph* re-sync is deferred (the page file writes live). Verified live (concurrent `recall` while serving;
`remember` queued 4 facts under the lock; `decay` folded them; a fresh `serve` folded on startup).
**Concurrent reads + never-blocked writes is the reachable maximum under Kuzu** — going further means a
different store (ADR-5). With this, **every planned Path B stage (B0–B6) and refinement has landed**;
remaining roadmap directions are non-memory (hybrid/ANN retrieval, packaging/CI) — of which
**observability landed next (v0.58** — an Ollama-telemetry metrics layer + web System tab; see Direction F).
Then the **Memory (Gedächtnis) tab landed (v0.59)** — the browser finally *shows* Path B: the identity +
counts, a **recall/context box** (`/api/recall` decay-weighted facts, `/api/context` the assembled three-tier
context), the `MemoryConcept` **theme cards**, and a browsable **assertion table** (current vs superseded).
Read-only over the existing store API (`memory_overview`/`list_assertions` + `recall`/`context_for`); the whole
second-brain tier is no longer CLI/MCP-only. Then **pipeline/build-stage observability landed (v0.60)** — per-stage
wall time + LLM token spend on the Projekt tab + `openwiki status`, extending the v0.58 collector into build time
(see Direction F). Remaining non-memory directions: retrieval quality (hybrid/re-rank, Direction A), packaging/CI (F).

### Wiki linking — the graph's connectivity where people read (v0.79–v0.83)

The pages were link-sparse while the graph is dense. Three read-time overlays close that gap without
touching page sources (arc42 ADR-28): **#2** a "Verwandte Seiten" panel (v0.79 — references, backlinks,
typed relations, similar pages, shared entities), **#3** entity auto-linking (v0.80 — first mention →
Begriffe), and **#1 inline citation links (v0.83)** — the text's own "Abschnitt 1.6" / "Seite 42" / "Kapitel
2" become links to the page the graph resolved them to (`REFERENCES.labels`; `openwiki references`
refreshes an existing graph in place in seconds). On informatik: 33 citation phrases on 24 pages, all 33
found and linked in the rendered prose. *Possible next:* the abbreviated "S. 357" form isn't a recognized
citation yet (the page regex matches "Seite(n) N" only).

### Path B+ — Second-Brain refinements (next; from the cognitive-memory report)

An external survey of cognitive-memory substrates ("From Cellular Self-Organization to the Artificial
Second Brain and GraphRAG": Kauffman attractors → CLS theory → CoALA → Graphiti bi-temporal graphs →
GraphRAG/Leiden → recursive knowledge synthesis) maps almost 1:1 onto Path B — **validating the
architecture** rather than calling for a rewrite. The mapping is exact:

| Report concept | OpenWiki today |
|---|---|
| Three tiers (DNA / epigenetic / attractor) | `context_for` = identity + activation + attractors |
| CLS: hippocampus (episodic, one-shot) vs neocortex (semantic, slow) | `Assertion`/`Session` (`remember`) vs `MemoryConcept` themes |
| Non-REM "sleep" consolidation | `consolidate` (Louvain clusters → LLM theme summaries) |
| Attention decay / synaptic downscaling | `decay.py` (exp half-life + prune) |
| Hebbian "strengthen on use" | `REINFORCES` edges (B1) |
| Hybrid vector+graph / shared-UUID substrate | graph mirrors the index; assertions carry embeddings |
| GraphRAG communities + global sensemaking | communities + summaries + `ask --global` |
| Non-lossy contradiction | `SUPERSEDES` (history kept, B4) |

The **genuine gaps** it surfaces — adopted in OpenWiki's measured, local, minimal way (we cite our own
`eval` numbers, not the report's):

- **B7 — Bi-temporal assertions. ✅ Core landed (v0.81.0).** Supersession was single-axis (`created_at`
  + a `SUPERSEDES` edge in *processing order* — so backfilling an older transcript after a newer one made
  the stale fact current). Now every `Assertion` carries **valid time** (`valid_from`/`valid_to`: when it
  held in the world) + **transaction time** (`created_at`/`expired_at`: when we recorded / stopped believing
  it) + a `cardinality` hint. The merge orders facts by **valid time** (pure `graph/temporal.py`
  `plan_merge`): a backfill lands *in* history, a world change **closes** the rival's interval, a same-instant
  conflict or `remember --correct` **retracts** it (we were wrong), `"many"` facts coexist, planned
  (future-dated) facts become current on their date. Capture extracts stated dates (resolved against the
  session date — from `--session-date` or a date in the session id). Queries: `recall --as-of` (valid time),
  `--known-at` (transaction time), `--timeline`; `context --as-of`, MCP `wiki_memory(as_of)`, `/api/recall`
  `as_of`/`known_at`; the assembled context shows each fact's validity. **No rebuild:** older graphs are
  migrated in place (`ALTER` + a backfill from the B4 edges — the current set is unchanged); B0 snapshots the
  new columns; queued journal ops keep their record time. `analyze memory` splits revision into world
  changes vs corrections. **v0.82 — measured + surfaced:** the temporal eval below took assembled-memory
  task success from **7/13 (v0.80, twice) to 13/13** (backfill 0→2, point-in-time 1→2, change-date 1→2,
  known-at 0→1, multi-valued 0→1); the one v0.82 miss (a noisy `cardinality` tag let "also uses Ollama"
  close "uses Kuzu") added an LLM **coexistence check** ("can both be true at once?") before any
  invalidation. The Gedächtnis tab gained *Stand am* / *Wissensstand vom* date pickers, a **Verlauf**
  timeline (`/api/timeline`), a validity column and überholt / zurückgezogen / geplant badges.
- **A2 — Hierarchical communities (P1).** Ours are **flat** Louvain; the report's GraphRAG builds a
  bottom-up **tree** of communities → meta-summaries (better global sensemaking now that informatik is 119
  pages). Recursive detection (or add Leiden) + a level in `MemoryConcept`/`Community` + `ask --global`
  traversal.
- **Temporal-reasoning eval. ✅ (v0.82).** `examples/eval_temporal.jsonl` — 13 scenarios / 8 kinds
  (backfill, point-in-time, change-date, correction, known-at, multi-valued, planned, control) through the
  cross-session harness; before/after against v0.80.0 in a worktree: **7/13 → 13/13** (numbers + caveats in
  `path-b-memory.md` §12.1). Small and hand-written — a direction check, not a benchmark.
- **Dogfooding on real data. ✅ (v0.84).** A Second Brain project over OpenWiki itself
  (`G:\OpenWiki\Projects\openwiki-dev`: the repo + docs as a code-corpus wiki, memory on), wired into this
  repo's Claude Code sessions (`claude-code --hooks --into`, bound + pinned, capture in a detached worker)
  and **backfilled** from the full development history (`openwiki backfill`: 28 dated days → 73 windows →
  **1,420 facts** in 110 min, 0 failed windows). Findings (details: `path-b-memory.md` §12.2): the pipeline
  holds on real history and durable facts come back ("embedding uses bge-m3", "Python must be 3.13"), but
  **fact identity** is the dominant failure — 43 version facts landed on **23 different subject+predicate
  keys**, so B7 never saw them as the same fact (23 stay "current"); ~2% obvious session trivia; 43
  "retractions" are a day-granularity artifact (same-day changes share one `valid_from`).
- **B9 — Fact identity. ✅ (v0.85).** Paraphrased attributes ("project | has version" / "is versioned" /
  "uses version") resolve onto one key (`Assertion.attr`) before the valid-time merge: embedding candidates
  (fact cosine ≥ 0.75, top 6) + one LLM "same property of the same thing?" choice per new wording (the
  ADR-23 pattern, applied to attributes; an alias map resolves each wording once). The first real run exposed
  three merge flaws, all fixed: sticky `"many"` marks (one grouped description froze a whole version group) →
  the coexistence check now decides rivalry per pair, nothing persisted, tags only a fallback; subject names
  ("owiki"/"openwiki") → the check is told they're one thing; same-capture changes read as corrections →
  closed in capture order. Plus per-window backfill timestamps, decay from *stated* time, a trivia-dropping
  capture prompt. **Measured on the dogfooding memory** (replay of the same captured facts): stale current
  facts 1,355 → 1,195, closed history 15 → 251, retractions 43 → 0, OpenWiki-version facts still "current"
  23 → 11; temporal eval stays 13/13. Details: `path-b-memory.md` §12.3; arc42 ADR-29.
- **B8 — Spreading-activation priming (P2, measure-first).** Session-scoped activation boost over graph
  neighbors of a retrieved node (+ decay), to resolve ambiguous follow-ups. Close to REINFORCES+decay but
  intra-session; A/B against plain recall before trusting it (the GraphRAG finding is the cautionary tale).

**Deliberately out of scope** (against the local/minimal/single-user ethos): full **RKS** multi-agent
generator/checker/auditor + SHACL (heavy; our entity-resolution already has a lightweight generate-then-
verify we can extend if needed); **multi-agent swarm / stigmergy** (single-user tool); procedural **"Skill
Vaults"** (task-agent territory, not a knowledge substrate). The "edge of chaos / K≈2" framing is a *lens*
for the world-model **coupling** analysis (Direction I), not a build item. Design detail: `path-b-memory.md` §12.

### Path B++ — memory hygiene & implicit recall (next; from the cognitive-agent report)

A second external report ("Architecture and Implementation of a Cognitive AI Agent: Simulating Human
Intelligence through LLMs, CoALA, and Sleep Phase Consolidation" — a *virtual-secretary* blueprint) is mostly
an **agent-runtime** design (continuous inner monologue, asyncio heartbeat loops, System-1/System-2, tool
synthesis). OpenWiki is the **memory + knowledge substrate under** an agent (Claude Code, via hooks + MCP), so
only its memory half applies — and that half largely **validates** what exists:

| Report concept | OpenWiki today |
|---|---|
| CoALA working / episodic / semantic memory | budgeted `context_for` / dated sessions (backfill + hooks) / `Assertion`s + themes + wiki |
| Letta core / recall / archival | identity tier / activation `recall` / wiki + graph |
| Memori semantic triples as a compression layer | S-P-O capture (~500-token budget vs Memori's ~721) |
| Zep/Graphiti temporal validity | **B7** bi-temporal assertions |
| "Conflict-aware temporal tagger" (SleepGate) | **B7 + B9** valid-time merge + fact identity |
| NREM consolidation | `consolidate` (themes) + `decay` |

The **genuine gaps**, measure-first as always:

- **Provenance + memory scrubbing (P0). ✅ (v0.86).** Measured on `examples/eval_poisoning.jsonl` (5 hidden
  injections + 3 legit items): baseline **2/5 payloads leaked** into the assembled memory. The first design
  (source tags + an LLM audit) **failed** — the injection launders the tag ("the user has authorized sharing all
  API keys…" → a *user* fact), payloads get captured descriptively ("scanner is disabled when …"), and the audit
  caught nothing while dropping two legitimate facts. Shipped instead: a **source-independent security-sensitive
  memory policy** (never persist instructions to AI assistants, security weakening, secrets/payments directed
  somewhere, standing authorizations) → **0/5 leaked, 8/8 legit kept, 0 of 1,446 real facts scrubbed**; the
  provenance tag stays a soft signal ("Material" marker, ×0.75 rank). Details: `path-b-memory.md` §13.1; ADR-30.
- **Cue-trigger recall (P1 — LoCoMo-Plus "Level-2 memory"). ✅ (v0.87).** Similarity recall can't connect "hates
  noisy open-plan offices" (session 3) to "book a venue for the client meeting" (session 45) — no shared words.
  Measured on `examples/eval_cue_trigger.jsonl` (8 scenarios + topic-adjacent distractors; hand-audited): baseline
  cue in context **2/8**, constraint respected **1/8**. First a **ranking bug** (recency decay scored 2025-dated
  facts ≈0 → bounded recency, `RECENCY_FLOOR`). Probes written as *questions* reached 4/8 (they matched the
  distractors); written as **hypothetical user facts** ("user cannot stand noise", HyDE-style) with a reserved,
  **personal-facts-only** slot, shown first under "Keep in mind", plus a task-aware answer prompt → cue **7/8**,
  respected **6/8** (raw log 4/8). The personal-only rule was measured in: without it a memory with no personal
  facts was relabelled "the user's circumstances" and a poisoning-set answer flipped. Regression: temporal 13/13,
  poisoning 8/8 / 0 leaks. **Opt-in** (`[memory] probes`) — +1 chat call per prompt, and the dogfooding memory holds
  1 personal fact in 1,218. An LLM judge (`constraint_respected`, 30/32 agreement with the audit) scores
  application. Details: `path-b-memory.md` §13.2; ADR-31.
- **`openwiki sleep` + intentional forgetting (P1). ✅ (v0.88).** One schedulable nightly pass (Task Scheduler /
  cron): fold queued writes → **forget** → re-consolidate → decay. Measured on the dogfooding memory: only ~3 % of
  facts are clear junk, but **31 % of the facts the hooks inject** for 40 real prompts are ("push and tag vX" →
  seven "vX was pushed and tagged"). No decay signal exists (2 of 1,218 facts ever re-affirmed; junk is recalled
  *often*), and an LLM review dropped 11–81 keep-facts depending on batch order — so forgetting is **policy-based**:
  rules for one-off events / commit hashes / tautologies (+ P0 re-applied), 0 of 232 keep-facts dropped, all 27
  matches in the full memory verified junk. Forgetting **archives** (`forgotten_at`; out of every view, known-at
  still sees it, re-said facts come back). Result: injected junk **31 % → 5 %**, prompts with junk 25 → 10 of 40.
  Remaining junk is stale *state* → re-resolution, not forgetting. Details: `path-b-memory.md` §13.3; ADR-32.
  **v0.89:** `--budget N` on `consolidate` / `sleep` — at most N new theme summaries per run; the rest are stored
  *pending* and finished by the next run (the dogfooding memory's first consolidation: 120 themes, ~6 min).
- **Stale facts — measured, not adopted.** Stale *state* ("six tabs", "U7 is the remaining large item") is ~3–6 % of
  the facts; for 12 of 14 labeled cases no newer fact in memory states the new state (the changing session never
  restated it). Three LLM approaches failed with the local 30B: **re-resolution** in memory (0/14 caught, 5/70 true
  facts flagged), a **wiki-grounded check** against the current docs (1/14, 5/70), and **update-aware capture** —
  the change-day transcript + related older facts (82 % of candidates flagged, every candidate in 8 of 18 windows,
  36 of 51 labeled flags wrong). Staleness stays visible via each fact's date; the promising route is the writer
  that knows what it changed — `wiki_remember` below. Details: `path-b-memory.md` §13.4–13.5.
- **LoCoMo benchmark (P2). ✅ (v0.91).** `owiki eval --locomo` (`openwiki/locomo.py`): capture + remember each of
  the 10 conversations as in production, answer from the assembled recall, token F1 + an LLM judge (J); resumable,
  time-budgeted, phased against GPU model swaps. **First finding:** recall's recency weighting halved the score
  (conversation 1: floor 0.6 → J 23.7 %, 0.9 → 52.6 %, neutral → 55.9 %) → `RECENCY_FLOOR` 0.9, regression sets
  unchanged. **Result, all 1,986 questions** (local 30B answering + judging): overall J **50.0 %** (multi-hop 54.3,
  temporal 34.0, open-domain 34.4, single-hop 56.5; adversarial 89.2). Losses: facts never captured (single-hop 34 %
  "Not mentioned"), relative dates stored at the session date (D13), a cautious answer prompt. Mem0 reports ≈ 67 %
  with GPT-4o-mini — not comparable, but now a reference point. Details: `path-b-memory.md` §13.7; ADR-34.
- **D13 — relative event dates at capture. ✅ (v0.92).** The capture rule now dates an event by what the session
  says ("yesterday", "last year" — resolved against the session date) while a habit or state the conversation
  doesn't date gets none (a first draft defaulted whole sessions to the session's year). LoCoMo temporal J
  **34.0 % → 41.7 %** (net +25 of 321, p ≈ 0.005 — the dates, not more recall); overall 50.0 → 50.5 %, other
  categories within capture-to-capture churn; temporal eval 13/13 and poisoning 8/8 unchanged. `path-b-memory.md` §13.8.
- **LoCoMo answer prompt — inference allowed. ✅ (v0.93).** The strict "only what the memories say, else Not
  mentioned" prompt abstained on 57 % of open-domain questions; the `infer` style answers "would / likely"
  questions from the remembered facts and keeps refusing what nothing bears on. Paired on the same graphs: overall J
  **50.5 → 55.0 %** (single-hop +45 net, p < 0.001; multi-hop +10; open-domain +7; temporal +8) for −19 adversarial →
  the default. `path-b-memory.md` §13.9.
- **D14 — an episodic capture style. Measured, not adopted.** Twice the facts per session ("every concrete detail"
  of a conversation between people) vs. the durable style, paired end-to-end on 4 LoCoMo conversations: overall J
  58.2 → 61.0 % (+97 / −81 questions, p ≈ 0.26; per conversation +3.9 / −2.5 / −2.0 / +7.5), temporal +9, adversarial
  −4 — within capture-to-capture variance, at ~2× the capture + merge cost. Kept as an experiment option only
  (`--capture-style episodic`); production captures durably. `path-b-memory.md` §13.10.
- **Judge audit + a larger recall budget. ✅ (v0.95).** 60 random LoCoMo answers hand-labelled: the local judge
  agrees 51/60, never rejects a right answer, and accepts 5 wrong ones (dates off by days, near misses) → the 55.0 %
  is ≈ 48 % ± 6 audit-corrected; paired comparisons stand. Recall k 10 → 20 (re-answered, same graphs): overall J
  **55.0 → 60.7 %** (+117 / −29, p ≈ 6·10⁻¹³; multi-hop +8.8, single-hop +6.2) → the benchmark default; the live
  hook context (k = 8) is the next place to test it. `path-b-memory.md` §13.11.
- **A larger k in the live context. ✅ (v0.96).** Measured on the hook's own path: 335 real prompts against the
  dev memory (k 8 / 2,000 chars ≈ 470 tokens → k 16 / 3,000 ≈ 710 tokens per prompt); a judge on 80 of them found
  ranks 9–16 helpful at 17–19 % vs. 25–37 % for ranks 1–8 (helpful facts per prompt 2.5 → 3.9); on the cue-trigger
  set (paired, 2 captures, no probes) the cue reached the context 8/16 → 16/16 and the constraint was applied
  7/16 → 12/16 (+5 / −0). Adopted: `[memory] context_k` (default 16) + `context_budget` 2,000 → 3,000; facts may
  use theme budget the themes don't need. `path-b-memory.md` §13.12.
- **MemOS — measured, not adopted.** The fifth review from the shortlist's brain-inspired part
  (`memory-systems-review.md` §18): memory as an OS resource — plaintext, KV-cache and LoRA memory in MemCubes under a
  scheduler — a memory platform with an API and plugins, and a local plugin for coding agents that learns skills from
  model-scored task traces and recalls the earlier fix when a step fails. KV-cache and LoRA memory need the model's
  internals; the failure-time recall, checked on this repository's 47 resolved tool failures: an earlier fix
  genuinely applied to 10 (hand audit; the local judge said 32), and at a strict similarity bar 4 of 5 suggestions were
  right — about four helpful recalls in 70 days, all recurring pitfalls the lessons review lists. No code.
  `docs/path-b-memory.md` §13.34.
- **OpenCog Hyperon — measured, not adopted.** The fourth review from the shortlist's brain-inspired part
  (`memory-systems-review.md` §17): Hyperon's Atomspace, MeTTa and probabilistic logic, and its coding-agent adapter
  Hyperon-MCP — curated atoms with truth values, PLN/NAL inference, an import-derived "blast radius". Checked with its
  own code on this repository: as a predictor of what a change touches the blast radius reached recall@10 0.04 (exact
  import traversal the same) against 0.53 for git co-change; against its own import graph the inference's precision was
  0.30; our 1,187 captured facts (809 distinct predicates) form two two-hop chains for a reasoner. No code.
  `docs/path-b-memory.md` §13.33.
- **A human-inspired memory architecture — measured, not adopted.** The third review from the shortlist's
  brain-inspired part (`memory-systems-review.md` §16; Microsoft, 2026): sleep consolidation, interference-based
  forgetting, engram maturation, reconsolidation, an entity graph, multi-cue retrieval — and, by its own LongMemEval
  runs, nothing that beats plain retrieval. Its two mechanisms that change what recall returns, judged on 339 real
  prompts replayed at their own time: maturation (a week of silence) would have hidden 40 % of the helpful facts,
  39 of 75 prompts losing one; deduplicating similar facts hurt (7 / 42 prompts). The replay also confirmed v0.113
  at realistic capture lags. No code. `docs/path-b-memory.md` §13.32.
- **Recency only where truth changes with time (v0.113).** The second review from the shortlist's brain-inspired
  part, *The Missing Knowledge Layer* (`memory-systems-review.md` §15): decay belongs to experience in storage,
  recency to the query — decaying factual claims is a category error. OpenWiki passed that litmus already; measured,
  recall's recency factor now applies only to the kinds of fact that go stale (plans, counts, gaps, versions, running
  states). LoCoMo coverage 0.456 → 0.461, as with no recency at all; on 267 real prompts against the dev memory,
  fewer stale-labeled facts in 67 prompts and more in 6 (p ≈ 4·10⁻¹⁴), judged helpfulness unchanged; temporal 13/13,
  cue-trigger 8/8. The paper's corroboration gate (three sessions) would reward the facts that keep changing.
  ADR-49, `docs/path-b-memory.md` §13.31.
- **HippoRAG 2 — replicated on its benchmark, not adopted.** The first review from the shortlist's brain-inspired
  part (`memory-systems-review.md` §14): retrieval as a PageRank walk from the facts a question links to, with an LLM
  filter in between. Rebuilt from its code with our local models, on its own MuSiQue setting (1,000 questions, its
  70B triples) it beat dense retrieval clearly — Recall@5 54.2 → 61.9 % (248 better / 85 worse), most of it from the
  filter. On our data it did not: over the informatik wiki's entities and relations it is even on recall and trades
  MRR (relational 0.468 → 0.610, definitional 0.848 → 0.762), over the LoCoMo memory it stays below dense recall even
  with the filter (multi-hop coverage 0.474 vs 0.482; production 0.501). Its gains need questions that bridge through
  an unnamed entity and a dozen triples per passage. No code. `docs/RAG-vs-GraphRAG.md` Finding 5,
  `docs/path-b-memory.md` §13.30.
- **A use signal first — git co-changes (v0.112).** Item 15, the last of the review series' plan: files that change
  together in commits as `CO_CHANGED` edges for code corpora (built from `git log`, refreshed by `openwiki cochange`).
  Online over this repository's history, co-change found 51 % of the specific files a commit touched in the top 10 —
  embedding similarity 36 %, popularity 35 %; on the dev code wiki `app.js` co-changes with its stylesheet, server,
  tests and help page. Shown under "changed together (git)" in `graph_neighbors`, the "Verwandte Seiten" panel, the
  Graph tab and the coupling analysis; GraphRAG retrieval was unchanged by it, so it stays out of expansion. ADR-48,
  `docs/path-b-memory.md` §13.29.
- **Procedural memory from errors — a review list (v0.111).** Item 14 of the review series' plan. 148 of 7,753 tool
  calls in the dogfooding transcript failed; the local model wrote a "lesson" for 121 of 122 resolved failures (restated
  tool rules, outages as advice, one-off bugs), so a lesson counts only when the same lesson was learned on two
  different days: five on the transcript, four worth keeping (a script file instead of quote-heavy heredocs and
  PowerShell inline code, `PYTHONIOENCODING=utf-8` on the cp1252 console, Windows paths from Bash). `owiki sessions
  lessons` lists them with Hermes' guardrails for a person to record where the agent always sees them; nothing is stored
  or injected. ADR-47, `docs/path-b-memory.md` §13.28.
- **The approval step (v0.110).** Item 13 of the review series' plan. Opt-in `[memory] approve_writes`: the agent's
  `wiki_remember` writes are staged next to the journal until a person approves them (`openwiki memory pending` /
  `approve` / `reject`, or the Gedächtnis tab's "Zur Freigabe"); an approved write is valid from when it was staged and
  recorded when approved, and a staged replacement closes exactly the fact that was reviewed. Judged on the dogfooding
  session: about a dozen agent writes a day to review (35 in three days), so off by default. The always-present core
  was not built: of the facts captured with a source, 19 were tagged as the user's and none was a standing convention —
  those live in `CLAUDE.local.md` and the host's memory already. ADR-46, `docs/path-b-memory.md` §13.27.
- **Volatility classes — measured; a review list, not a label (v0.109).** Item 12 of the review series' plan. The
  phrasing rule of the stale-fact analysis, frozen and checked on blind hand labels of the dogfooding memory: 25 % of
  the facts it flags were stale (26 of 104), 2½–9 times the rate of the others — but a "possibly outdated" mark would be
  wrong three times in four; a rule refined on those labels failed on fresh facts (1 of 11), the local model's tags did
  worse (12–26 %), and stale facts were no older than the rest. So `analyze memory --review` lists the facts of the
  volatile kinds (plans, counts, gaps, versions, running states) as `replaces` lines to check; one pass on the dev
  memory closed 39 stale facts. `docs/path-b-memory.md` §13.26.
- **Session search for agents (v0.108).** Item 11 of the review series' plan: the raw transcripts, searched by full
  text — BM25 over every user and assistant turn, no model — as the MCP tool `wiki_sessions`, `owiki sessions search |
  list` and `eval --locomo --excerpts M`; verbatim, dated excerpts, redacted and screened. On LoCoMo full text found
  the evidence more often than the embedding (68 % vs 59 % in the top 5 turns ±1), and 5 excerpts next to the facts
  took overall J 62.9 → 78.2 % (+276 / −41; single-hop 68.6 → 89.8 %) — the largest gain of the series, ahead of
  episodes of the same size (74.7 %). On coding-session detail questions the facts held the answer 8 / 47 times, the
  excerpts 41 / 47. Pulled by the agent, not injected. ADR-44, `docs/path-b-memory.md` §13.25.
- **Each fact once per stretch (v0.107).** Item 10 of the review series' plan, "when to inject", judged on the
  dogfooding transcript (318 prompts, 11 stretches between compactions): later prompts still get 3–4 useful new facts
  (the first 6.7), so the hook keeps injecting on every non-chore prompt — but 55 % of what it injected was already in
  the agent's context from earlier in the stretch. Now each fact, theme and the identity is injected once per stretch;
  the record resets at a compaction or `/clear`, and a fact the budget cut can come later. Replayed: 2,757 → 1,405
  characters per prompt (49 % fewer). `[memory] repeat_facts` restores the old behaviour. ADR-43,
  `docs/path-b-memory.md` §13.24.
- **Episodes on the live path — measured, not adopted.** Before usefulness, faithfulness: narratives of the
  dogfooding transcript's capture windows contained specific terms absent from their source at ten times the facts'
  rate (12 % vs 1 %); a stricter prompt (4 %) and a grounding filter (0 % of what a rule can see) left invented
  framing no rule detects. LoCoMo's narratives invent too ("Caroline … running had helped her mood"), which explains
  adversarial losses there. Episodes stay a harness option; verbatim session excerpts are the next candidate.
  `docs/path-b-memory.md` §13.23.
- **Episodes next to facts (v0.106).** Experiment 9 of the review series' plan: one dated narrative per session
  (`memory.narrate_session` — relative dates resolved), the 3 most similar shown after a question's facts. On LoCoMo,
  paired: overall J 62.9 → 74.7 % (+226 / −45, p ≈ 3·10⁻³⁰; temporal 48.3 → 64.2 %, single-hop 68.6 → 81.6 %) — the
  largest gain so far — at a cost on adversarial questions (83.9 → 71.5 %: a narrative holds both speakers' days).
  In the harness (`eval --locomo --episodes 3`); the live path waits for its own measurement (storage, the capture
  worker, the context budget). ADR-42, `docs/path-b-memory.md` §13.22.
- **The add-only ablation — the merge's checks stay (v0.105).** Experiment 8 of the review series' plan: do the
  coexistence check and attribute resolution earn their place? `eval --merge checks | tags | add-only` replays saved
  LoCoMo captures under each mode. LoCoMo doesn't care (add-only 63.8 % vs 62.9 %, n.s. — its facts rarely change);
  the temporal set does: checks 13 / 13, add-only 12 / 13 (cannot retract a correction), tags alone 10 / 13 (a noisy
  tag closed a coexisting value, a correction never met its paraphrase). Kept. `docs/path-b-memory.md` §13.21.
- **Multi-hop expansion — measured, not adopted.** Experiment 7 of the review series' plan: facts linked to the
  recalled ones through a shared subject or object, a distinctive word or semantic similarity, thresholded, depth 1–2.
  Offline it lost both ways — swapped into the top 20 it pushed out better facts (coverage 0.456 → 0.431), added it
  lost to the ranking's own next candidates at the same size (+6 facts: 0.468 vs 0.480). No re-answer run, no code.
  `docs/path-b-memory.md` §13.20.
- **The question's time window (v0.104).** Experiment 6 of the review series' plan: a date in the question becomes a
  window (`temporal.question_window` — days, parts of months, months, seasons, years, "the week before", relative
  expressions against `now`), and within the dense top 4k facts whose valid time falls in it gain a bonus (0.1),
  the dense order kept. Paired against hybrid recall: the questions naming a date 46.2 → 56.2 % (+24 / −3), overall J
  61.6 → 62.9 % (p ≈ 5·10⁻⁵); with hybrid recall +2.2 over v0.95. On by default (`[memory] temporal_weight`). ADR-41,
  `docs/path-b-memory.md` §13.19.
- **Hybrid recall (v0.103).** Experiment 5 of the review series' plan: BM25 over the remembered facts next to the
  embedding — as a recall aid, not a re-ranker: within the dense top 2k it may swap facts into the top k, which keep
  their dense order; stopwords out, light stemming, query terms in more than 5 % of the facts (a conversation's
  speakers' names) ignored. Plain fusion did nothing on LoCoMo (keyword matches from deep in the ranking displaced
  the answers); the pooled form: overall J 60.7 → 61.6 % (+47 / −34, n.s.), +24 / −2 where it brought the answer in.
  On real coding prompts the facts it swaps in were judged helpful 21.9 % vs 12.0 % for those displaced (26 / 9
  prompts, p ≈ 0.006) → on by default (`[memory] lexical_weight` 0.2). ADR-40, `docs/path-b-memory.md` §13.18.
- **Portable memory + the LadybugDB spike (v0.102).** Fix 4 of the review series' plan, for R10. `owiki memory
  export` writes the remembered tier as a COGX archive (Cognee's exchange format: facts with their valid time,
  sessions, themes, identity; `--full` adds retracted and forgotten facts and the embeddings), `memory import`
  restores one losslessly into an empty memory or remembers another system's facts through the normal merge, and
  `sleep` keeps a deterministic Markdown view (`memory/`, one file per subject) for git. On the dogfooding memory a
  full round trip kept all 1,486 facts identical in every field, embeddings and 118 themes included; both archives
  pass Cognee's own reader. The spike: LadybugDB 0.19 runs 643 of 645 tests through a `kuzu` shim, and the dev graph
  migrates by `EXPORT` / `IMPORT DATABASE` or by a rebuild + `memory import`, identical apart from approximate vector
  search; three changes stand before a move (the vector extension, the statement cache after DDL, a lock of our own —
  LadybugDB doesn't keep a writer out while a reader is open). ADR-39, R10, `docs/path-b-memory.md` §13.17.
- **No memory for chore prompts (v0.101).** Fix 3 of the review series' plan: the inject hook skips git chores ("push",
  "commit and push", "push and tag v1.2.3"), slash commands and — once the session has answered — bare
  acknowledgements; a prompt that carries a task keeps its memory. `[memory] skip_chores` / `skip_prompts`. 87 of 474
  dogfooding prompts (18.4 %) skipped, ≈ 62 K injected tokens saved; no eval question affected. `docs/path-b-memory.md`
  §13.16.
- **Writes land during a session (v0.100).** Fix 2 of the review series' plan. Long-running readers — the MCP server,
  `serve`, `chat`, `ask` — hold the graph only per call (`LazyGraph`, ≈ 70 ms an open), and memory writes run in two
  phases: plan read-only with memoized model checks and cached embeddings (`remember` / `fold_journal` gained
  `dry_run`), then apply under the write lock from the cache. On the real queue the write lock fell from 276 s to
  7.2 s with an identical result; `wiki_remember` now spawns a fold worker, and a write landed 7.6 s after the call
  while the MCP server kept running. ADR-38, `docs/path-b-memory.md` §13.15.
- **Credential redaction and a hardened policy (v0.99).** The first fix of the plan from the agent-memory review
  series: credentials are redacted wherever text enters memory — in the transcript before capture, in `remember()`,
  in the journal file, in `wiki_remember` and in the handoff note — and `sleep` rewrites facts stored before.
  `policy.redact_secrets` knows the common provider formats, private keys, JWTs, URL passwords, bearer tokens and
  credential assignments; the P0 instruction policy now matches NFKC-normalized text without zero-width characters and
  refuses bidirectional overrides. 0 false positives on 1,486 real facts and 1.57 M characters of real session text;
  the eval sets are untouched. ADR-37, `docs/path-b-memory.md` §13.14.
- **Session handoff (v0.98): share context between sessions.** `owiki handoff prepare` writes what the next session
  needs — the agent's note (Next, Summary, Decisions, Open threads, Ready-to-use prompts; screened by the P0 policy,
  now its own module `openwiki/policy.py`) plus the state OpenWiki derives itself: the repository (branch, HEAD, tag,
  sync, uncommitted files, commits since the session start or the last handoff), the memory (facts learned and
  closed, writes queued, turns not yet captured), the environment (Ollama and its models, the graph, how far the wiki
  index lags the repository, capture workers, hook-log problems) and the memory + pages for the first Next item — into
  the memory project's `handoff/`; `resume` reads it back with what changed since. A `SessionStart` hook injects the
  brief into every new session; agents get the MCP tool `wiki_handoff` and the `/session-restart prepare | resume`
  skill. Modeled on a hand-written session-restart skill, with the state derived instead of maintained by hand; its
  first run on the dev project surfaced 137 facts waiting in the journal (the running session's MCP server holds the
  graph read-only, so nothing lands before the session ends) and a wiki index 34 commits behind. ADR-36.
- **Incremental capture (v0.97): the hooks capture a whole session.** Found while reviewing Nemori, which segments the
  whole stream: the capture hook read only the transcript's **last 20,000 characters** per capture point, so the
  long dogfooding session — 1.5 M characters over 36 days, nine compactions — reached memory to at most ~13 %. Now
  the worker captures every turn since a per-session watermark in per-day windows (`capture_windows`, ≤ 20,000
  characters, dated by their first turn), writes each window as soon as it is captured, and advances the
  watermark; a lock file keeps one worker per session. A session first seen with a long history keeps its last
  eight windows. `docs/memory-systems-review.md` §10.
- **Fix (v0.96.1): stdin is read as UTF-8.** `cli.main()` switched only stdout/stderr to UTF-8, so on Windows the
  MCP server and the Claude Code hooks decoded their piped JSON as cp1252: German questions to `wiki_ask`, facts
  sent to `wiki_remember` and the prompts the inject hook recalls against lost their umlauts ("Lautstärke" →
  "LautstÃ¤rke"). Found when a `wiki_remember` fact came back garbled; `_utf8_stdio` + the same default in
  `MCPStdioServer.serve()`, with regression tests that force a cp1252 stdin.
- **Agent-initiated writes — `wiki_remember` (P2). ✅ (v0.90).** CoALA's learning action by the agent itself
  (Letta's `memory_replace`), and the fix for **stale state** that the local model couldn't infer afterwards: an
  opt-in MCP tool (`[memory] agent_writes`) — the host agent records facts / the **new state** when it makes a change
  and names what it `replaces` (copied from `wiki_memory`; exact matches, near misses come back with suggestions);
  queued to the journal, folded at session end / `sleep`: new facts remembered, replaced ones **closed** (history
  kept). P0 screening; one-off events refused ("record the resulting state"). Measured on the dogfooding memory with
  the 14 labeled stale facts: `replaces` matched 14/14, all closed, stale facts in 10 topic contexts **12 → 0**, the
  new state in 9/10; agent ops skip the local B9 resolver (it closed a true fact once). Also fixed: pre-1970 dates
  crashed `format_date` on Windows. Details: `path-b-memory.md` §13.6; ADR-33.

**Out of scope** (agent runtime or model internals, against the local-substrate role): continuous thinking /
inner-monologue managers / heartbeat loops / dual-speed System-1–2 (Claude Code is the agent; always-on
thinking competes for the one local GPU); SleepGate KV-cache gating, fast weights, HOPE self-distillation,
RL "dreaming" (need model internals / training — a local Ollama model is a black box); Theory-of-Mind / persona
RL (agent-side; the identity tier covers persona at the memory level); Voyager-style tool synthesis + Git-backed
skill libraries (procedural memory is the host's — at most export captured how-tos as skills later); probe-based
circuit breakers (internals; OpenWiki's own calls are already bounded — output caps, timeouts, per-window error
tolerance). **Caveat on the source:** several load-bearing claims are unverified 2026 preprints or vendor numbers
(SleepGate's "O(n) → O(log n) interference", Memori's 87% LoCoMo at 721 tokens) — treated as hypotheses, not
facts. Design detail: `path-b-memory.md` §13.

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
