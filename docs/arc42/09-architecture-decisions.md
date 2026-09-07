# 9. Architecture Decisions

> arc42 §9 — The important, hard-to-reverse decisions as ADRs. **Status: complete.**
> Format per ADR: **Status · Context · Decision · Alternatives considered · Consequences (+/−)**.
> "Accepted (revisit for Path B)" flags decisions the agent-memory direction will re-open.

## ADR index

| # | Decision | Status | Quality goal |
|---|---|---|---|
| [1](#adr-1) | Intermediate representation between ingestion and the rest | Accepted | Q4, Q3 |
| [2](#adr-2) | Local Ollama behind `Embedder`/`ChatModel` protocols; no cloud | Accepted | Q1, Q4 |
| [3](#adr-3) | Kuzu graph is an additive *mirror*, not the source of truth | Accepted (revisit for Path B) | Q4 |
| [4](#adr-4) | Stdlib zero-dependency web server + no-build SPA | Accepted | Q2 |
| [5](#adr-5) | Target Python 3.13 (not 3.14) | Accepted | — |
| [6](#adr-6) | Borrow GraphRAG's *ideas*, not the library | Accepted | Q2, Q5 |
| [7](#adr-7) | Optional layers as always-created, empty-by-default tables | Accepted | Q4 |
| [8](#adr-8) | Graph read-only by default, writable only for edits | Accepted (revisit for Path B) | correctness |
| [9](#adr-9) | Evaluation-driven claims | Accepted | Q5 |
| [10](#adr-10) | Project manifest + settings precedence | Accepted | usability |
| [11](#adr-11) | Incremental builds via a per-stage fingerprint chain | Accepted | performance |
| [12](#adr-12) | Bounded-deterministic, normalized entity extraction | Accepted | Q3, quality |
| [13](#adr-13) | New capabilities as subcommands, not more flags | Accepted | Q4 |

---

### ADR-1
**Intermediate representation between ingestion and everything else.**
- **Context:** Multiple source formats (PDF, MD, HTML, code) must feed one wiki/graph/RAG stack.
- **Decision:** One IR (`ParsedDocument`, `models.py`); all parsers produce it; all downstream
  stages consume only it. Serialization (JSON/Markdown) lives on the IR.
- **Alternatives:** Each parser feeds `WikiBuilder` directly with format-specific data —
  rejected (N parsers × M stages coupling; no shared serialization/testing).
- **Consequences:** + New parsers slot in behind `sources.parse_source` with zero downstream
  change (proven 4×); downstream testable from a saved `.json`. − The IR is a lowest common
  denominator; format-specific richness (precise PDF layout) is flattened.

### ADR-2
**Local Ollama behind protocols; no cloud.**
- **Context:** Privacy/local-first goal; avoid API keys and network dependence.
- **Decision:** All inference via a local Ollama, accessed through the `Embedder`/`ChatModel`
  protocols (`OllamaEmbedder`/`OllamaChat`).
- **Alternatives:** A cloud API (OpenAI-style) — rejected (privacy, keys, cost, offline).
  Hard-code Ollama calls in the agent — rejected (untestable, unswappable).
- **Consequences:** + Fully offline, private, free; backends swappable; tests use fakes.
  − Requires a running Ollama with models pulled; quality/latency bounded by local models.

### ADR-3
**The Kuzu graph is an additive *mirror*, not the source of truth.** *(revisit for Path B)*
- **Context:** Want graph traversal + vector search together without a second authority.
- **Decision:** `SemanticIndex` stays authoritative; `GraphBuilder` mirrors embeddings into
  `Chunk` nodes; the graph is a pure function of wiki+index and rebuildable.
- **Alternatives:** Make the graph the single store (drop the NumPy index) — rejected
  (dual-write consistency; lose the simple brute-force path; graph no longer disposable). No
  graph at all — rejected (lose GraphRAG/global search/exploration).
- **Consequences:** + No dual-write problem; graph disposable/rebuildable; reads never risk the
  index. − Embeddings duplicated; incremental upsert recomputes only `SIMILAR_TO`. − This
  mirror stance is exactly what agent memory (Path B) must invert.

### ADR-4
**Stdlib zero-dependency web server + no-build SPA.**
- **Context:** Minimal-deps goal; a browser UI is desired.
- **Decision:** `ThreadingHTTPServer` + a hand-written vanilla-JS SPA with a vendored
  `marked.min.js`; no framework, no bundler.
- **Alternatives:** FastAPI/Flask + React/Vue — rejected (runtime deps + a JS build toolchain
  vs Q2). A desktop or TUI app — rejected (browser reach + zero install).
- **Consequences:** + No JS toolchain; trivial to run/audit; aligns with Q2. − Hand-rolled UI
  (force-directed explorer, Markdown routing) costs more per feature; **no auth** (§11).

### ADR-5
**Target Python 3.13 (not 3.14).**
- **Context:** Kuzu has no Windows wheel for 3.14.
- **Decision:** Pin the graph layer to ≤3.13; code targets 3.10+.
- **Alternatives:** Drop Kuzu for a server DB (Neo4j) — rejected (external server breaks
  single-file/local). Use NetworkX — rejected (no persistence/vector index). Run 3.14 without
  the graph — rejected (graph is core).
- **Consequences:** + Graph layer works on Windows. − Can't adopt 3.14 features until Kuzu ships a wheel.

### ADR-6
**Borrow GraphRAG's *ideas*, not the library (community / global search).**
- **Context:** Want whole-corpus "global" sensemaking (Microsoft GraphRAG's strength).
- **Decision:** Reimplement community detection (a compact deterministic Louvain) + LLM
  community summaries + global search natively, against local Ollama, dependency-free.
- **Alternatives:** Adopt the `graphrag` library — rejected (heavy dep, OpenAI-shaped,
  batch/cloud, token-hungry). No global search — rejected (lose the whole-corpus question class).
- **Consequences:** + Fits the stdlib/local ethos; cheap (one LLM call/community, not per page);
  measurable (Finding 3: Global beats RAG 9–1). − We maintain the algorithm; no Leiden/advanced
  features out of the box.

### ADR-7
**Optional layers as always-created, empty-by-default tables.**
- **Context:** Entities, communities, reinforcement are opt-in and may be absent.
- **Decision:** Create their tables in the schema (empty) + a lazy `IF NOT EXISTS` migration for
  older graphs; queries are best-effort (try/except → empty).
- **Alternatives:** Conditional schema + feature flags threaded through every query — rejected
  (flag sprawl, brittle). Separate optional databases — rejected (complexity).
- **Consequences:** + Store/agent/UI code degrades gracefully; no flags everywhere; works on
  graphs built before a layer existed. − Slightly more schema; empty tables on minimal builds.

### ADR-8
**Graph opened read-only by default, writable only for edits.** *(revisit for Path B)*
- **Context:** Kuzu writable access is an exclusive lock; multiple readers are fine.
- **Decision:** Open read-only for `ask`/`mcp`/most reads; open writable (with a read-only
  fallback) for `serve`/`chat` where edits + incremental upsert + reinforcement happen.
- **Alternatives:** Always writable — rejected (single-process; blocks concurrent readers).
  Always read-only — rejected (no live edits, no usage memory).
- **Consequences:** + Concurrent readers; safe defaults. − "Learn from use" (reinforcement) only
  fires in writable contexts, limiting Path-B memory on the read-only `ask` path (debt D2).

### ADR-9
**Evaluation-driven claims (measure the graph's value).**
- **Context:** "Is the graph worth it?" is easy to hand-wave.
- **Decision:** A backend-agnostic eval harness (pure metrics + injected retrievers/chat);
  reproducible RAG-vs-GraphRAG-vs-Global findings recorded in `docs/RAG-vs-GraphRAG.md`.
- **Alternatives:** Trust intuition / vendor claims — rejected (the graph's value was
  non-obvious; it does *not* help local recall but *does* help answer quality + global search).
- **Consequences:** + Design decisions are evidence-based. − Findings are on a small N / one
  corpus / one embedder (§11 R5).

### ADR-10
**Project manifest (`openwiki.toml`) + settings precedence.**
- **Context:** Users keep several knowledge bases; settings shouldn't be retyped per command.
- **Decision:** A per-project `openwiki.toml` groups sources + layout + settings; unset settings
  resolve **flag > manifest > `~/.openwiki/config.toml` > built-in default**. No manifest → the
  historical `./output` defaults (back-compat).
- **Alternatives:** Environment variables only — rejected (no persistence / side-by-side
  projects). A single global config — rejected (can't vary per project). Flags only — rejected
  (retyping; no reproducibility).
- **Consequences:** + Reproducible, side-by-side projects; explicit flags always win. − One more
  concept (the project) + a hand-rolled TOML writer (`tomllib` reads but can't write).

### ADR-11
**Incremental builds via a per-stage fingerprint chain.**
- **Context:** A full rebuild (esp. entity extraction) is slow; most edits touch one stage.
- **Decision:** `pipeline.compute_fingerprints` hashes each stage's inputs+params into a chain in
  `.openwiki/state.json`; `stale_stages` runs only what changed (`--force`/`--only` override).
- **Alternatives:** Always rebuild everything — rejected (slow). File-mtime checks only —
  rejected (miss parameter changes like `--split-level`).
- **Consequences:** + Fast iterative builds; `status` reports per-stage state. − The chain must
  capture every meaningful param; a missed param risks a stale skip.

### ADR-12
**Bounded-deterministic, normalized entity extraction.**
- **Context:** LLM entity extraction is noisy, non-deterministic, and fragments surface variants
  (Signal/Signale, Datenstruktur/Datenstrukturen).
- **Decision:** Greedy + fixed seed for near-determinism, output bounding + retry-on-empty for
  robustness, and a German-aware `_normalize` (umlaut/ß fold, conservative inflection strip) to
  merge variants without over-merging distinct compounds (Systemgrenze ≠ Systemzustand).
- **Alternatives:** Use raw LLM output — rejected (variant fragmentation). Pull in an NLP/stemmer
  dependency — rejected (dep + German tuning). Accept non-determinism — rejected (breaks Q3).
- **Consequences:** + Cleaner, mergeable entities; offline-testable with a fake chat. − Extraction
  is still slow (~1 call/page) and opt-in; normalization is heuristic (§11 R4).

### ADR-13
**New capabilities as subcommands, not more flags.**
- **Context:** The CLI is the primary surface; capabilities keep growing.
- **Decision:** Each capability is a new `argparse` subcommand (`communities`, `decay`,
  `eval`, …); shared behavior comes from a common parent parser + `_apply_project`.
- **Alternatives:** A few commands with many mode flags — rejected (flag explosion; unclear,
  hard-to-discover surface).
- **Consequences:** + Discoverable (`--help` per command); clean per-command project resolution.
  − More subcommands to document; some conceptual overlap (e.g. `ask --global` vs a command).

---
*Chapter complete. ADR-3 and ADR-8 are the decisions the Path-B agent-memory direction
(§11 D1/D2) will re-open — see the design in `docs/path-b-memory.md`. New significant decisions
should be appended here with the next id.*
