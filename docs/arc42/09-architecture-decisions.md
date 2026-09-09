# 9. Architecture Decisions

> arc42 §9 — The important, hard-to-reverse decisions as ADRs. **Status: complete.**
> Format per ADR: **Status · Context · Decision · Alternatives considered · Consequences (+/−)**.
> Decisions the agent-memory direction re-opened are marked "refined by ADR-N"; Path B has since
> **landed** (ADR-14/15/16/17/18) — see those ADRs and §11 (debts D1/D2/D6 resolved).

## ADR index

| # | Decision | Status | Quality goal |
|---|---|---|---|
| [1](#adr-1) | Intermediate representation between ingestion and the rest | Accepted | Q4, Q3 |
| [2](#adr-2) | Local Ollama behind `Embedder`/`ChatModel` protocols; no cloud | Accepted | Q1, Q4 |
| [3](#adr-3) | Kuzu graph is an additive *mirror*, not the source of truth | Accepted; refined by [ADR-16](#adr-16) (Path B) | Q4 |
| [4](#adr-4) | Stdlib zero-dependency web server + no-build SPA | Accepted | Q2 |
| [5](#adr-5) | Target Python 3.13 (not 3.14) | Accepted | — |
| [6](#adr-6) | Borrow GraphRAG's *ideas*, not the library | Accepted | Q2, Q5 |
| [7](#adr-7) | Optional layers as always-created, empty-by-default tables | Accepted | Q4 |
| [8](#adr-8) | Graph read-only by default, writable only for edits | Accepted; refined by [ADR-17](#adr-17) (Path B) | correctness |
| [9](#adr-9) | Evaluation-driven claims | Accepted | Q5 |
| [10](#adr-10) | Project manifest + settings precedence | Accepted | usability |
| [11](#adr-11) | Incremental builds via a per-stage fingerprint chain | Accepted | performance |
| [12](#adr-12) | Bounded-deterministic, normalized entity extraction | Accepted | Q3, quality |
| [13](#adr-13) | New capabilities as subcommands, not more flags | Accepted | Q4 |
| [14](#adr-14) | Wiki & Second-Brain coexist as tiers of one substrate (not replacement) | Accepted (Path B) | Q4, modularity |
| [15](#adr-15) | Remembered facts as reified `Assertion` nodes (not typed edges) | Accepted (Path B / B2–B4) | Q4 |
| [16](#adr-16) | Graph preserves the remembered tier across a document rebuild | Accepted (Path B / B0) | Q4 |
| [17](#adr-17) | Read-path reinforcement via an append-only usage log | Accepted (Path B / B1) | correctness |
| [18](#adr-18) | Contradiction as append-only supersession (`SUPERSEDES`-edge-only) | Accepted (Path B / B4) | Q4, correctness |

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
**The Kuzu graph is an additive *mirror*, not the source of truth.** *(refined by [ADR-16](#adr-16))*
- **Context:** Want graph traversal + vector search together without a second authority.
- **Decision:** `SemanticIndex` stays authoritative; `GraphBuilder` mirrors embeddings into
  `Chunk` nodes; the graph is a pure function of wiki+index and rebuildable.
- **Alternatives:** Make the graph the single store (drop the NumPy index) — rejected
  (dual-write consistency; lose the simple brute-force path; graph no longer disposable). No
  graph at all — rejected (lose GraphRAG/global search/exploration).
- **Consequences:** + No dual-write problem; graph disposable/rebuildable; reads never risk the
  index. − Embeddings duplicated; incremental upsert recomputes only `SIMILAR_TO`. − This
  mirror stance is exactly what agent memory (Path B) must invert.
- **Update (Path B / B0):** [ADR-16](#adr-16) **refines** this — the *document* subgraph stays a
  pure, rebuildable mirror, but a **remembered** subgraph is now preserved across a rebuild, so the
  graph is authoritative for what it learned. The mirror stance holds for docs only.

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
**Graph opened read-only by default, writable only for edits.** *(refined by [ADR-17](#adr-17))*
- **Context:** Kuzu writable access is an exclusive lock; multiple readers are fine.
- **Decision:** Open read-only for `ask`/`mcp`/most reads; open writable (with a read-only
  fallback) for `serve`/`chat` where edits + incremental upsert + reinforcement happen.
- **Alternatives:** Always writable — rejected (single-process; blocks concurrent readers).
  Always read-only — rejected (no live edits, no usage memory).
- **Consequences:** + Concurrent readers; safe defaults. − "Learn from use" (reinforcement) only
  fires in writable contexts, limiting Path-B memory on the read-only `ask` path (debt D2).
- **Update (Path B / B1):** [ADR-17](#adr-17) **resolves** D2 without weakening this decision —
  read-only reads append usage to a log a writable process folds in, so reads reinforce without ever
  taking the exclusive write lock.

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

### ADR-14
**Wiki and Second-Brain coexist as tiers of one substrate — not replacement.** *(Path B direction)*
- **Context:** Path B (agent memory) could either *replace* Path A (the document wiki + consolidation
  / global search) as a "more advanced" system, or *coexist* with it. The framing question: are they
  the same concern? They are not — Path A is sensemaking over **authoritative documents** (ground
  truth, shared, reproducible); Path B is accumulating/reconciling **personal experience** (evolving,
  mutable, private). Different substrate, trust, and lifecycle.
- **Decision:** Coexist on **one tiered substrate**. Path A is the **authoritative document tier**
  (CANONICAL); Path B adds the **remembered tiers on top** (episodic/semantic/procedural), with
  separate write authority and lifecycle (per `docs/path-b-memory.md` §3.1/B0). **"Mode" is a
  per-project policy**, not a codebase fork: **Wiki Mode** = document tier only, read-mostly, no
  memory writes/consolidation (today's behavior); **Second Brain Mode** = document + remembered tiers,
  reads fuse both (authority × confidence weighted), capture/merge/decay/consolidation active. Second
  Brain is a *superset* of Wiki; the memory tiers are additive (ADR-7), so Wiki Mode is literally
  "memory tier off."
- **Alternatives:** (a) **Replace** Path A with Path B — rejected: throws away a working, measured,
  reproducible pipeline; forces memory machinery onto documents that don't need it; couples the
  shareable-wiki use case to personal-memory (hurts both); breaks reproducibility (Q5/ADR-9);
  migration risk; and Path B *anchors on* the document graph, so it can't replace its own foundation.
  (b) **Two separate systems/stores** — rejected: loses fused retrieval + a shared entity vocabulary;
  the payoff is querying documents and memory *together*.
- **Consequences:** + Wiki Mode is unchanged and stays deterministic/shareable; documents are the
  ground-truth anchor memory cites; global search can span both tiers; you can share the document tier
  while keeping memory private; re-ingesting documents preserves memory (independent lifecycles).
  − A tier-authority + trust-weighting model to maintain; retrieval must be tier-aware; two lifecycles
  to keep independent. Reframes (consistently with) ADR-3/ADR-8's "revisit for Path B."

### ADR-15
**Remembered facts as reified `Assertion` nodes, not typed edges.** *(Path B / B2–B4)*
- **Context:** The remembered tier must store facts that can be time-versioned, contradicted, and
  carry provenance + a per-fact embedding for recall.
- **Decision:** Represent each fact as a reified **`Assertion`** node (`subject`, `predicate`,
  `object`, `session_id`, `created_at`, `emb`) linked from its `Session` (`ASSERTS`) — a fact is a
  *node*, not a bare edge.
- **Alternatives:** Typed `Entity→Entity` edges with validity props — lighter, but versioning /
  provenance are awkward on an edge. A single `FACT` edge with `predicate` as a property
  (Cognitive Substrate's choice) — one index, no per-predicate migration, but "predicate is a filter,
  not a traversal" and provenance/versioning can't attach to an edge (they add a separate run node).
- **Consequences:** + Native versioning ([ADR-18](#adr-18)), provenance, and a per-fact vector for
  decay-weighted recall. − One extra hop in queries; the fact is *remembered* content that duplicates
  nothing in the doc tier (deliberate — it didn't come from a source). Realizes §4 of `docs/path-b-memory.md`.

### ADR-16
**The graph preserves the remembered tier across a document rebuild.** *(Path B / B0 — refines [ADR-3](#adr-3))*
- **Context:** ADR-3 makes the graph a pure, rebuildable function of the documents. Path B adds
  content learned from experience that must **not** be lost when documents are re-ingested (debt D1).
- **Decision:** Split the graph into a **derived** tier (rebuilt from docs each time) and a
  **remembered** tier (preserved). `GraphBuilder` **snapshots** the remembered subgraph
  (`Session`/`Assertion`/`ASSERTS`/`SUPERSEDES` + the `REINFORCES` usage overlay) before its
  destructive rebuild and **restores** it into the fresh schema. The graph is now *authoritative* for
  remembered content; the doc tier stays a pure function of its inputs.
- **Alternatives:** Keep the graph fully rebuildable (drop memory on rebuild) — rejected (memory
  becomes unusable the moment a source changes). A separate memory database — rejected (loses fused
  retrieval + the shared entity vocabulary; against [ADR-14](#adr-14)).
- **Consequences:** + Experience survives doc rebuilds; independent lifecycles (ADR-14) are real;
  ADR-3 still holds for the *document* tier. − `GraphBuilder` is now stateful w.r.t. an existing DB;
  assertions whose embedding dim changed are dropped (re-`remember`-able), with a warning. Addresses debt D1.

### ADR-17
**Read-path reinforcement via an append-only usage log.** *(Path B / B1 — resolves [ADR-8](#adr-8)/D2)*
- **Context:** ADR-8 opens the graph read-only for `ask`/MCP (Kuzu's write lock is exclusive), so
  "learn from use" only fired in the writable `serve`/`chat` paths (debt D2).
- **Decision:** A read-only retrieval **appends** its seed→related usage to an append-only JSONL
  sidecar (`graph.usage.jsonl`); the next **writable** process **folds it in** (`fold_usage` →
  `reinforce`) — `serve`/`chat` on startup, or `openwiki decay`. Reads teach the graph without ever
  taking the write lock. Gated by Second Brain mode.
- **Alternatives:** Open the graph writable on `ask` — rejected (lock contention; serializes readers,
  breaks ADR-8). A background writer daemon — rejected (no always-on process in a local CLI tool).
  Skip read-path learning — rejected (that *is* the debt).
- **Consequences:** + Usage memory grows from *all* reads, not just serve/chat; zero read-path lock
  contention; the log survives a rebuild and folds in what still matches. − Writes are **deferred**,
  not simultaneous (a true concurrent reader-and-writer model is still future) — acceptable for the
  CLI/MCP pattern, where a writer runs between read sessions. Addresses debt D2.

### ADR-18
**Contradiction as append-only supersession (`SUPERSEDES`-edge-only).** *(Path B / B4)*
- **Context:** A newer fact can contradict an older one (same subject+predicate, different object);
  the agent must answer the *current* fact while the superseded history stays queryable (debt D6).
- **Decision:** On `remember`, a new fact sharing a **normalized subject+predicate** with a current
  fact but a **different object** adds `(new)-[:SUPERSEDES]->(old)`. **Nothing is deleted**; *current*
  = no incoming `SUPERSEDES`, so validity intervals are *derivable* (`valid_from` = `created_at`,
  `valid_to` = the superseder's time). `recall` returns current facts only by default. Detection is
  deliberately **boring**: exact normalized subject+predicate, different object, later timestamp.
- **Alternatives:** `valid_from`/`valid_to` columns + a `superseded_by` pointer (the §4 sketch) —
  heavier and needs a column migration (Kuzu `ALTER`); the edge alone yields the same intervals.
  Delete the old fact on conflict — rejected (loses history). An LLM/semantic contradiction judge —
  rejected (non-deterministic, over-reach; a false *negative* is safer than wrongly hiding a valid fact).
- **Consequences:** + Time-travel + **revival** (re-asserting a superseded fact revives it) come for
  free; no column migration (one always-created edge table, [ADR-7](#adr-7)); preserved across a
  rebuild ([ADR-16](#adr-16)). − Conservative detection misses synonym-predicate contradictions; per-fact
  `confidence` is deferred. Addresses debt D6.

---
*Chapter complete. The Path-B agent-memory direction has **landed** its load-bearing decisions:
ADR-14 (coexistence) + ADR-15/16/17/18 realize it and resolve the §11 debts D1/D2/D6 that ADR-3/ADR-8
flagged. Deep design in `docs/path-b-memory.md`. New significant decisions should be appended here with the next id.*
