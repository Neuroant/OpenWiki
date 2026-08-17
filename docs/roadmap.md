# OpenWiki — Implementation Roadmap

What has been built, in the order it was built — and where it could go next. OpenWiki
went from a single straight PDF→wiki pipeline to a project-aware, multi-format,
graph-augmented knowledge platform with a rigorously measured RAG-vs-GraphRAG story.
The first half of this doc records what shipped; the second half turns forward —
[open topics](#open-topics--known-limitations) and
[prioritized directions](#future-directions-prioritized) for what to build next.

*Provenance:* reconstructed from the git history — 62 commits / 30 tags,
2026-08-01 → 2026-08-17, v0.6.0 → v0.38.2. Version tags mark the milestones; a few
patch versions between them are omitted here for readability.

## The arc in one line

A linear IR-based pipeline (**ingest → wiki → index → RAG → edit**) grew a
**knowledge-graph** layer, a **browser UI**, a **projects** system, **multi-format
ingestion**, **coding-agent access**, and finally an **evaluation harness** that turned
"is the graph worth it?" from opinion into numbers.

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

## Related docs

- `docs/projects.md` — the projects layer design + phase roadmap.
- `docs/RAG-vs-GraphRAG.md` — the full evaluation writeup (methodology + numbers).
- `docs/coding-agents.md` — MCP / OpenCode / Claude Code setup.
