# 9. Architecture Decisions

> arc42 §9 — The important, hard-to-reverse decisions, as short ADRs (context → decision →
> consequences). **Status: draft** (decisions captured; some to be expanded with alternatives).

Each ADR: **Context**, **Decision**, **Consequences (+/−)**.

### ADR-1 — An intermediate representation between ingestion and everything else
- **Context:** Multiple source formats (PDF, MD, HTML, code) must feed one wiki/graph/RAG stack.
- **Decision:** Define one IR (`ParsedDocument`, `models.py`); all parsers produce it; all
  downstream stages consume only it. Serialization (JSON/Markdown) lives on the IR.
- **Consequences:** + New parsers slot in behind `sources.parse_source` with zero downstream
  change (proven 4×). + Downstream testable from a saved `.json`. − The IR is a lowest common
  denominator; format-specific richness (precise PDF layout) is flattened.

### ADR-2 — Local Ollama behind protocols; no cloud
- **Context:** Privacy/local-first goal; avoid API keys and network dependence.
- **Decision:** All inference via a local Ollama; access behind `Embedder`/`ChatModel`
  protocols implemented by `OllamaEmbedder`/`OllamaChat`.
- **Consequences:** + Fully offline, private, free to run. + Backends swappable; tests use
  fakes. − Requires a running Ollama with models pulled; quality/latency bounded by local models.

### ADR-3 — The Kuzu graph is an additive *mirror*, not the source of truth
- **Context:** Want graph traversal + vector search together, without a second authority.
- **Decision:** `SemanticIndex` stays authoritative; `GraphBuilder` mirrors embeddings into
  `Chunk` nodes; the graph is a pure function of wiki+index and rebuildable.
- **Consequences:** + No dual-write consistency problem; graph is disposable/rebuildable.
  + Reads never risk the index. − Duplication of embeddings; incremental upsert recomputes
  only `SIMILAR_TO` (structural/reference/entity edges need a full rebuild). − This mirror
  stance is exactly what a future "agent memory" (Path B) would need to invert.

### ADR-4 — Stdlib zero-dependency web server + no-build SPA
- **Context:** Minimal-deps goal; a browser UI is desired.
- **Decision:** `http.server`/`ThreadingHTTPServer` + a hand-written vanilla-JS SPA with a
  vendored `marked.min.js`; no framework, no bundler.
- **Consequences:** + No JS toolchain; trivial to run/audit. + Aligns with Q2. − Hand-rolled
  UI (e.g. the force-directed graph explorer, Markdown routing) costs more per feature; **no
  auth** (localhost-only assumption — see §11).

### ADR-5 — Target Python 3.13 (not 3.14)
- **Context:** Kuzu has no Windows wheel for 3.14.
- **Decision:** Pin the graph layer to ≤3.13; code targets 3.10+.
- **Consequences:** + Graph layer works on Windows. − Can't adopt 3.14 features until Kuzu ships a wheel.

### ADR-6 — Borrow GraphRAG's *ideas*, not the library (community/global search)
- **Context:** Want whole-corpus "global" sensemaking (Microsoft GraphRAG's strength).
- **Decision:** Reimplement community detection (a compact deterministic Louvain) + LLM
  community summaries + global search natively, against local Ollama, dependency-free.
- **Consequences:** + Fits the stdlib/local ethos; cheap (one LLM call/community, not per
  page); measurable. + Avoids a heavy, cloud-shaped dependency. − We maintain the algorithm;
  no Leiden/advanced features out of the box.

### ADR-7 — Optional layers as always-created, empty-by-default tables
- **Context:** Entities, communities, reinforcement are opt-in and may be absent.
- **Decision:** Create their tables in the schema (empty) + lazy `IF NOT EXISTS` migration
  for older graphs; queries are best-effort.
- **Consequences:** + Store/agent/UI code degrades gracefully; no feature flags threaded
  everywhere. − Slightly more schema; empty tables on minimal builds.

### ADR-8 — Graph opened read-only by default, writable only for edits
- **Context:** Kuzu writable access is an exclusive lock; multiple readers are fine.
- **Decision:** Open read-only for `ask`/`mcp`/most reads; open writable (with a read-only
  fallback) for `serve`/`chat` where agent edits + incremental upsert + reinforcement happen.
- **Consequences:** + Concurrent readers; safe defaults. − "Learn from use" (reinforcement)
  only fires in writable contexts, limiting Path-B memory on the read-only `ask` path.

### ADR-9 — Evaluation-driven claims (measure the graph's value)
- **Context:** "Is the graph worth it?" is easy to hand-wave.
- **Decision:** A backend-agnostic eval harness (pure metrics + injected retrievers/chat);
  reproducible RAG-vs-GraphRAG-vs-Global findings recorded in `docs/RAG-vs-GraphRAG.md`.
- **Consequences:** + Design decisions are evidence-based. − Findings are on a small N / one
  corpus / one embedder (see §11).

---
TODO (completion steps): add ADRs for the project/manifest layer, the fingerprint-based
incremental build, and entity-normalization; add an "alternatives considered / rejected"
line to ADR-3, ADR-4, ADR-6; date/number them consistently if we adopt a formal ADR log.
