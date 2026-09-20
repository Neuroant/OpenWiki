# 9. Architecture Decisions

> arc42 §9 — The important, hard-to-reverse decisions as ADRs. **Status: complete.**
> Format per ADR: **Status · Context · Decision · Alternatives considered · Consequences (+/−)**.
> Decisions the agent-memory direction re-opened are marked "refined by ADR-N"; Path B has since
> **landed** (ADR-14–19). Later decisions deepen the graph (ADR-22 typed relations + relation-aware
> GraphRAG, ADR-23 entity resolution) and add **observability** (ADR-20), a *measured* retrieval-add-on
> discipline (ADR-21), a **shipping** story (ADR-24 packaging + CI), a **world-model analysis** toolkit
> (ADR-25, Direction I), and a **capability-complete web UI** (ADR-26, Direction J).

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
| [8](#adr-8) | Graph read-only by default, writable only for edits | Accepted; refined by [ADR-17](#adr-17)/[ADR-19](#adr-19) (Path B) | correctness |
| [9](#adr-9) | Evaluation-driven claims | Accepted | Q5 |
| [10](#adr-10) | Project manifest + settings precedence | Accepted | usability |
| [11](#adr-11) | Incremental builds via a per-stage fingerprint chain | Accepted | performance |
| [12](#adr-12) | Bounded-deterministic, normalized entity extraction | Accepted; refined by [ADR-23](#adr-23) | Q3, quality |
| [13](#adr-13) | New capabilities as subcommands, not more flags | Accepted | Q4 |
| [14](#adr-14) | Wiki & Second-Brain coexist as tiers of one substrate (not replacement) | Accepted (Path B) | Q4, modularity |
| [15](#adr-15) | Remembered facts as reified `Assertion` nodes (not typed edges) | Accepted (Path B / B2–B4) | Q4 |
| [16](#adr-16) | Graph preserves the remembered tier across a document rebuild | Accepted (Path B / B0) | Q4 |
| [17](#adr-17) | Read-path reinforcement via an append-only usage log | Accepted (Path B / B1) | correctness |
| [18](#adr-18) | Contradiction as append-only supersession (`SUPERSEDES`-edge-only) | Accepted (Path B / B4) | Q4, correctness |
| [19](#adr-19) | Concurrency as read-only readers + a lock-free write-ahead journal | Accepted (Path B / B1) | correctness, Q2 |
| [20](#adr-20) | Observability via an in-process, bounded metrics ring buffer | Accepted | Q5, performance |
| [21](#adr-21) | Retrieval add-ons (hybrid, re-rank) measured, not adopted on faith | Accepted | Q5 |
| [22](#adr-22) | Typed `Entity→Entity` relations + relation-aware GraphRAG | Accepted | Q4, Q5 |
| [23](#adr-23) | Corpus-wide entity resolution (embedding candidates + LLM verify) | Accepted | Q3, quality |
| [24](#adr-24) | Ship as the `owiki` distribution + CI; publishing license-gated | Accepted | Q2, usability |
| [25](#adr-25) | World-model analysis as a read-only, additive toolkit (`owiki analyze`) | Accepted | Q5, Q4 |
| [26](#adr-26) | Capability-complete no-build SPA + SSE streaming (Direction J) | Accepted | usability, Q2 |

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
  taking the exclusive write lock. [ADR-19](#adr-19) then **generalizes** this: `serve`/`chat` also
  open **read-only by default** (so readers run concurrently), with *all* writes routed through a
  lock-free journal — the "writable only for edits" mode becomes the opt-in `--sync`.

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
  not simultaneous. [ADR-19](#adr-19) generalizes this log into a full write-ahead journal (and settles
  the "true concurrent reader-and-writer" question against Kuzu's measured reader-XOR-writer lock).
  Addresses debt D2.

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

### ADR-19
**Concurrency as read-only readers + a lock-free write-ahead journal.** *(Path B / B1 — generalizes [ADR-8](#adr-8)/[ADR-17](#adr-17))*
- **Context:** ADR-17 let read-only `ask`/MCP reinforce via a usage log, but `serve`/`chat` still held
  an **exclusive writable** lock for their whole lifetime — blocking *every* other process (even a
  read-only `ask`). Path B's "second brain" wants to `ask`/`remember`/`recall` while a `serve` runs.
  The Kuzu locking model was **measured**, not assumed: a writable connection blocks all readers, **and**
  a read-only connection blocks a writer (multiple readers coexist). Kuzu 0.11 is **reader-XOR-writer** —
  there is **no** simultaneous read+write (no MVCC/WAL).
- **Decision:** Since simultaneity is impossible *in Kuzu*, target the reachable maximum — **concurrent
  readers + never-blocked writes**. `serve`/`chat` open **read-only by default** (readers coexist), and
  *all* memory writes are **queued to a lock-free write-ahead journal** rather than taking the lock:
  reinforce pairs → `graph.usage.jsonl` (ADR-17), and `remember` / host-`capture` / chat-edit graph
  re-sync → `graph.journal.jsonl` as self-contained `remember`/`reindex` ops (`graph/journal.py`;
  `queue_remember`/`queue_reindex` append read-only). A **writer folds** the journal
  (`GraphStore.fold_journal(embedder)`) at `serve`/`chat` start+shutdown, in `openwiki decay`, or on the
  next `remember`; a locked-out `remember`/`capture` **queues** instead of failing. Writable opens
  **retry-with-backoff** for transient two-writer contention. `--sync` restores the ADR-8 held-writable
  mode (live edit-sync, exclusive).
- **Alternatives:** Keep `serve` writable (ADR-8 as-is) — rejected (blocks all concurrent access, the
  actual pain). Short-lived writable opens per write from within a read-only `serve` — rejected (the
  process's own read lock conflicts with its writable open; self-deadlock). A background writer daemon —
  rejected (no always-on process; ADR-17's reasoning). Switch to an MVCC store (DuckDB/SQLite/Neo4j) for
  true concurrency — rejected here (ADR-5/ADR-6 keep Kuzu; that's a store change, not a code change).
- **Consequences:** + Many readers run while serving; writes never block or get lost (queued + folded).
  + One journal mechanism spans reinforce + remember + edit re-sync; both sidecars survive a rebuild. −
  A chat-edit's **graph** re-sync is now **deferred** (folded on the next writable pass), though the page
  file writes live; heavy edit-sync workloads should use `--sync`. − Journaled writes lag until a writer
  runs (a long-lived read-only `serve` accumulates the journal until its shutdown/next-start fold). This
  is the **honest resolution** of ADR-8/D2's "true concurrent reader-and-writer" question: not achievable
  within Kuzu; the journal is the ceiling.

### ADR-20
**Observability via an in-process, bounded metrics ring buffer.** *(v0.58 / v0.60)*
- **Context:** the LLM + embedding backends receive rich per-call telemetry from Ollama (latency +
  prompt/eval token counts) and **discarded** all of it — there was no visibility into where time or
  tokens went, at serve-time or build-time.
- **Decision:** a pure/stdlib `metrics.py` — a thread-safe, **bounded** ring buffer (`MetricsCollector`
  + module-level `COLLECTOR`) + `parse_ollama_stats` (ns→ms, tokens/sec). `OllamaChat`/`OllamaEmbedder`
  record a `chat`/`embed` event per call (best-effort — a metrics failure never breaks the call); the
  web layer records per-request `http` events; `_cmd_build` diffs the collector per stage. Surfaced in
  the CLI (`ask` footer), the web **System** tab (`/api/metrics`), per-turn `chat()` stats, and
  per-build-stage timing/tokens on **Projekt**.
- **Alternatives:** external APM / OpenTelemetry — rejected (a dependency, off-ethos for a local tool);
  logging only — rejected (not queryable/aggregatable).
- **Consequences:** + always-on, zero-config, no dependency; immediately useful (revealed that one agent
  "turn" is several model calls, and a slow first answer is mostly cold-model *load* time). − the bounded
  buffer under-counts a very large build's per-stage tokens if a stage emits > `maxlen` events (1024
  comfortably spans a full build); in-process only (not persisted across runs).

### ADR-21
**Retrieval add-ons are *measured* against pure dense, not adopted on faith.** *(v0.61 / v0.62 — extends [ADR-9](#adr-9))*
- **Context:** beyond GraphRAG (ADR-6, found *not* to lift retrieval recall on this corpus), the usual
  next upgrades are **hybrid** lexical+dense retrieval and an **LLM re-rank** pass. Do they help *here*?
- **Decision:** build both on-ethos and wire each into `owiki eval` as a scored retriever *before*
  trusting it — LLM re-rank (`rerank.py`: one chat call orders a wider pool) and **hybrid** BM25+dense
  via reciprocal rank fusion (`lexical.py`: a pure BM25 + RRF, no `rank-bm25` dependency). Ship them
  opt-in (`--rerank` / `--hybrid`); let the harness decide per corpus.
- **Alternatives:** a cross-encoder reranker — rejected (a model + dependency, off the local/stdlib
  ethos); a vector DB / ANN — deferred (the corpus is small).
- **Consequences:** measured (`docs/RAG-vs-GraphRAG.md` Findings 4): on strong-embedder German prose both
  **tie or lose** (re-rank *drops* MRR; hybrid ties), but **hybrid wins decisively on a code corpus**
  (hit@1 57%→86%). + the capabilities exist and are corpus-testable; a clean negative result is as
  valuable as a positive one. − no default retrieval change — dense stays the baseline.

### ADR-22
**Typed `Entity→Entity` relations + relation-aware GraphRAG.** *(v0.63 / v0.64 — extends [ADR-6](#adr-6)/[ADR-12](#adr-12))*
- **Context:** the entity layer (ADR-12) captured **co-mention** only; a real knowledge graph needs typed
  relations between entities, and retrieval/agents should be able to *traverse* them.
- **Decision:** an opt-in second per-page LLM pass (`extract_relations`, `--relations`) extracts
  subject–predicate–object triples *among that page's entities*, grounded to them (unresolved/self
  dropped), merged across pages into `RELATED_TO {predicate, weight, pages}` edges (always-created,
  ADR-7). Expansion traverses them: `neighborhood` gains a `relation` group
  (`MENTIONS→RELATED_TO→MENTIONS`) added to `agent._EXPAND_RELS`, so `ask` / `owiki eval` /
  `graph_neighbors` are relation-aware; the Graph tab draws the typed edges (predicate on hover).
- **Alternatives:** reified relation nodes (like Assertions, ADR-15) — rejected (an edge-with-property
  suffices for aggregate relations); one combined entity+relation call — rejected (risks degrading the
  tuned entity extraction).
- **Consequences:** + co-mention becomes a real, traversable, explainable graph. − a second LLM call per
  entity-rich page (opt-in); on dense corpora relation-connected pages often overlap similar/structural
  neighbours (the channel's unique value is the pages nothing else connects — a rigorous retrieval-lift
  measurement needs a relation-targeted eval set).

### ADR-23
**Corpus-wide entity resolution: block-by-type → embedding candidates → LLM verify.** *(v0.66 — refines [ADR-12](#adr-12))*
- **Context:** ADR-12 resolves entities by a *deterministic* normalized key (spelling/plural/word-order).
  Same-concept variants it can't see (spacing, near-synonyms) stay as duplicate nodes, and there are no
  aliases or descriptions.
- **Decision:** an opt-in corpus-wide pass (`resolve_entities`, `--resolve-entities`) — **block by type**,
  generate candidate clusters by **embedding cosine** (≥ 0.80, calibrated for bge-m3), and confirm each
  multi-member cluster with **one small LLM call** (canonical name + aliases + description). Singletons
  cost nothing; an entity the model doesn't group survives unchanged. `Entity` gains `aliases` +
  `description`; `pages_for_entity` / `find_entity` match aliases (search an acronym/synonym → the canonical).
- **Alternatives:** pure-embedding merge (no LLM) — rejected (over-merges distinct same-kind entities); a
  full-entity-list LLM canonicalization — rejected (a huge prompt, unreliable, drops entities).
- **Consequences:** + candidate generation favours *recall*, the LLM provides *precision* (splits a mixed
  cluster), so cost stays bounded; aliases make variants findable. − acronym↔full-form (≈ 0.40 cosine)
  isn't embedding-close, so it isn't even a candidate — resolution catches spelling/spacing/plural/
  word-order/near-synonym variants, not acronyms (a calibrated, honest limitation).

### ADR-24
**Ship as the `owiki` distribution + CI; public publishing gated on a license.** *(v0.64 / v0.65)*
- **Context:** the tool needed automated testing + an install/deploy story; the PyPI name `openwiki` is
  taken, and no license has been chosen.
- **Decision:** GitHub Actions **CI** runs the offline suite on every push/PR across Python 3.11–3.13 +
  builds the Docker image. Packaging: distribution name **`owiki`** (the *import* package stays
  `openwiki`), enriched metadata, a clean `python -m build` / `twine check`, a `Dockerfile` + compose
  (Ollama stays an external sibling, not bundled), and a **manual** OIDC trusted-publishing workflow.
  Kept **private/unlicensed** for now → a `Private :: Do Not Upload` classifier hard-blocks accidental
  upload; publishing awaits a license decision.
- **Alternatives:** publish immediately — rejected (no license chosen); bundle Ollama + models in the
  image — rejected (huge; Ollama stays a sibling); a token-based publish — rejected (OIDC trusted
  publishing needs no stored secret).
- **Consequences:** + CI guards every change on Linux (proving it isn't locked to its Windows dev host);
  the project is installable + containerizable; publishing is one deliberate step away. − not on PyPI yet
  (license-gated); CI is Linux-only so far (no Windows/macOS leg).

### ADR-25
**World-model analysis as a read-only, additive toolkit (`owiki analyze`).** *(v0.67–v0.71, Direction I)*
- **Context:** OpenWiki holds two representations of the *same* corpus — the symbolic **graph** and the
  continuous **semantic space** (embeddings) — plus a memory tier that changes over time. Nothing measured
  the *structure and organization* of that knowledge: only runtime metrics (ADR-20) and retrieval quality
  (ADR-9) existed. The question "is this knowledge base well-organized, and how much does the graph actually
  add over the embeddings?" had no numeric answer.
- **Decision:** a new `openwiki/analysis/` package + an `owiki analyze` subcommand (ADR-13), **read-only +
  additive** (never mutates graph/index, like ADR-3) and **offline** where possible (uses the *stored*
  embeddings; no Ollama). Five capabilities: **coupling** (where the graph agrees with vs. adds to the
  embedding geometry — the headline *graph reach*), a **2-D semantic map** (Analyse tab, `/api/analyze`),
  **gaps** (ranked, actionable improvement candidates), **compare** (a coupling-fingerprint diff across
  corpora / versions / embedders / settings), and **memory** dynamics (the Path B tier's
  revision / consolidation / temperature / growth). The core is **pure NumPy**; the heavier bits (silhouette
  + ARI, UMAP projection) live behind an opt-in **`[analysis]` extra** (ADR-4/5), degrading gracefully when
  absent. *Analysis is to structure what `owiki eval` (ADR-9) is to retrieval* — it turns a qualitative
  question into measured, comparable numbers.
- **Alternatives:** fold the metrics into `eval` — rejected (a different question: structure vs. retrieval
  quality); require the extra always — rejected (keeps the base install lean, ADR-4); take a heavyweight
  graph-analytics dependency (networkx/igraph) as *core* — rejected (hand-rolled pure-NumPy suffices at this
  scale, per the `community.py` Louvain precedent).
- **Consequences:** + a principled, *measured* account of the graph's marginal value — it extends the
  RAG-vs-GraphRAG finding (≈36% of the graph's non-similarity edges are reach the embedder misses; the space
  is strongly anisotropic, so lift-over-null is the real signal); + an **analysis→improvement loop** (gaps
  surfaced a real source typo, entity-name variants, and a duplicate-titled page); + **comparability** across
  KBs/versions; + the memory analysis makes the "learning over time" tier legible. − metrics need
  baselines/nulls to be interpretable (addressed by the random-pair null + `--compare`); − the O(n²)
  page-scale computations are fine now but won't scale to very large corpora (same class as R3/D3).

### ADR-26
**Surface the backend in the browser: a capability-complete, no-build SPA (+ SSE streaming).** *(v0.72–v0.78, Direction J)*
- **Context:** the CLI/back-end had outrun the web UI — GraphRAG / hybrid / re-rank / global retrieval, the
  world-model analysis toolkit (ADR-25), the entity-resolution + typed-relation layers (ADR-22/23), and
  multi-source provenance were reachable only from the CLI or the agent's tools. The browser showed a small
  slice.
- **Decision:** a UI direction (U1–U11) that makes the SPA *surface the backend*, holding the stdlib
  server + **no-build vanilla-JS** constraint (ADR-4) throughout: an **Ask** mode with interactive retrieval
  controls (GraphRAG/hybrid/re-rank/global/`k`); the **Analyse** tab completed (coupling + gaps + memory
  dynamics + a PCA/UMAP map with community focus); a **Begriffe** entity/concept browser (canonical
  entities · aliases · description · relations); **source/book provenance** filtering; and chrome polish
  (dark mode, hybrid sidebar search, collapsible panels). Answers **stream token-by-token** over a
  **Server-Sent-Events** endpoint (`/api/ask/stream` → `WikiWebApp.ask_stream` → `RAGAgent.stream` →
  `OllamaChat.chat_stream`), with the graph lock held **only around retrieval** so generation streams
  lock-free (consistent with the reader-XOR-writer model, ADR-19). All read-only + graceful when a layer is
  absent (ADR-7).
- **Alternatives:** adopt a JS framework/build step for the richer UI — rejected (breaks ADR-4's no-build
  SPA + zero-dependency serve); WebSocket streaming — rejected (SSE is simpler, one-way, and works from the
  stdlib `ThreadingHTTPServer` with a chunked write); stream the Agent tool-loop too — deferred (its reply
  is short after tool calls; streaming a multi-step tool loop is a separate problem).
- **Consequences:** + the browser is now capability-complete — every major backend feature is explorable
  without the CLI; + streaming makes long grounded answers feel responsive; + still no build tooling, no JS
  dependencies. − more client state in one `app.js`; − the SSE path bypasses the JSON `_json` handler (a
  second response shape to maintain); − the Agent mode stays blocking (asymmetry with Ask).

---
*Chapter complete. The Path-B agent-memory direction landed via ADR-14/15/16/17/18/19; the graph then
deepened (ADR-22 typed relations + relation-aware GraphRAG, ADR-23 entity resolution), gained
**observability** (ADR-20), a *measured* retrieval-add-on discipline (ADR-21), a **shipping** story
(ADR-24 packaging + CI), a **world-model analysis** toolkit (ADR-25, Direction I), and a
**capability-complete web UI** (ADR-26, Direction J — U1–U11, incl. SSE streaming). §11 debts D1/D2/D6
are resolved. Deep designs in `docs/path-b-memory.md` and `docs/RAG-vs-GraphRAG.md`. New significant
decisions should be appended here with the next id.*
