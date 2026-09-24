# 12. Glossary

> arc42 §12 — Domain and technical terms, so everyone means the same thing. **Status: complete.**

## 12.1 Domain terms

| Term | Definition |
|---|---|
| **Agentic wiki** | A knowledge base a pipeline builds from sources and that both humans and AI agents can query, explore, and edit. |
| **IR (Intermediate Representation)** | `ParsedDocument`: the format-neutral model (metadata + outline + pages) every source parses into and every downstream stage reads. |
| **Outline** | The document's TOC tree (`OutlineItem`, with a `level`); the wiki's page hierarchy derives from it. |
| **Wiki / WikiPage** | The split output: a tree of linked Markdown pages (`index.md`, `pages/*.md`, `wiki.json`). |
| **Chunk** | An overlapping word-window slice of a page's clean text, carrying provenance (page slug, PDF pages) — the unit of retrieval. |
| **Slug** | A filesystem-safe page id (e.g. `037-datenstrukturen`); the join key across wiki/index/graph. |
| **Project** | A folder with `openwiki.toml` grouping sources + built artifacts + settings so state persists. |
| **Community** | A cluster of topically related pages (Louvain over the page graph) with an LLM summary; the unit of **global search**. |

## 12.2 Retrieval & agents

| Term | Definition |
|---|---|
| **RAG** | Retrieval-Augmented Generation: retrieve top chunks → grounded prompt → cited answer. |
| **GraphRAG** | RAG plus graph expansion: seed pages' graph neighbors (incl. typed **relations**) are pulled in and re-ranked by the query. |
| **Hybrid retrieval** | Fusing dense (cosine) with lexical (**BM25**) rankings via **RRF** (`--hybrid`); catches exact tokens the embedder blurs. Ties dense on prose, wins on code (ADR-21). |
| **BM25** | The classic lexical relevance score (term frequency × inverse document frequency, length-normalized); the lexical half of hybrid retrieval (`lexical.py`, pure). |
| **RRF (Reciprocal Rank Fusion)** | Scale-free blend of two rankings: an item's score is Σ 1/(k+rank). Fuses dense + BM25 without reconciling score scales. |
| **Re-ranking** | An optional LLM pass (`--rerank`) that reorders a wider candidate pool by relevance (one chat call, `rerank.py`); measured, opt-in (ADR-21). |
| **Global search** | Answering a whole-corpus/thematic question from the community summaries (not chunk retrieval). |
| **Grounding** | The constraint (and metric) that answers use/cite only the provided excerpts. |
| **Provenance** | The source trail carried by chunks/answers (page slug, PDF page range) enabling citations. |
| **Embedder / ChatModel** | The pluggable backend protocols (implemented by `OllamaEmbedder` / `OllamaChat`). |

## 12.3 Graph & memory

| Term | Definition |
|---|---|
| **GraphStore / GraphBuilder** | The Kuzu read (query) and write (build) sides of the graph layer. |
| **SIMILAR_TO / REFERENCES / MENTIONS / RELATED_TO** | Vector-similarity, cross-reference, page→entity, and typed entity→entity edges. |
| **Entity layer** | Opt-in LLM-extracted typed entities (`Entity` + `MENTIONS`), normalized to merge surface variants — optionally enriched with typed **relations** (ADR-22) and **resolution** (ADR-23). |
| **Typed relation (`RELATED_TO`)** | An LLM-extracted subject–predicate–object edge between two entities (`{predicate, weight, pages}`); co-mention becomes a traversable graph, and GraphRAG expansion follows it (ADR-22). |
| **Entity resolution / canonical entity / alias** | The corpus-wide pass merging same-concept surface variants into one **canonical** `Entity` with an `aliases` list + an LLM `description` (embedding candidates + LLM verify, ADR-23). |
| **REINFORCES** | A usage-memory edge (weight + last_seen) strengthened when a connection is used and decayed over time. |
| **Reinforcement / decay** | Hebbian "strengthen on use" (`reinforce`) and time-based "forget" (`decay`, exponential half-life). |
| **Remembered tier** | The authoritative Path B memory subgraph (`Session`/`Assertion` + `SUPERSEDES`, plus the `REINFORCES` overlay); additive, preserved across doc rebuilds (ADR-16); active only in Second Brain mode. |
| **Session / Assertion** | A captured "day" of experience (`Session`) and a reified subject·predicate·object fact under it (`Assertion`, with a mirrored embedding) — the memory data model (ADR-15). |
| **SUPERSEDES / supersession** | A newer `Assertion` supersedes an older one (same normalized subject+predicate, different object); nothing deleted, so history stays queryable (ADR-18). Since B7 the edge is *provenance* (closer → closed); "current" is decided by the validity columns (ADR-27). |
| **Valid time / transaction time (bi-temporal)** | A fact's two time axes (ADR-27): **valid time** `valid_from`/`valid_to` = when it held in the world (its *semantic* content); **transaction time** `created_at`/`expired_at` = when OpenWiki recorded it / stopped believing it (its *episodic* trace). "Current" = valid now ∧ believed. |
| **As-of / known-at** | Point-in-time memory queries: `recall --as-of D` = what was *true* at D (valid time); `--known-at K` = what OpenWiki *believed* at K (transaction time) — e.g. before a later correction. The web's *Stand am* / *Wissensstand vom* pickers. |
| **Backfill / closed / retracted / planned** | A **backfill** = an older session remembered after newer ones; it lands *in* history instead of overwriting the present. A **closed** fact's interval ended (the world changed — *überholt*); a **retracted** one was never true (a correction, `remember --correct` — *zurückgezogen*); a **planned** one is valid only from a future date (*geplant*). |
| **Attribute key / fact identity (B9)** | The canonical key a fact is merged under (`Assertion.attr`, normalized subject ␟ predicate): paraphrases across sessions ("has version" / "is versioned") are resolved onto one key by embedding candidates + one LLM choice, so the valid-time merge can order them (ADR-29). |
| **Cardinality / coexistence check** | Whether a predicate holds one value at a time (`one`: a port, a default) or several (`many`: the tools a project uses). The capture model tags it, but noisily, so before an invalidation a **veto-only** LLM check asks *"can both be true at the same moment?"* (`memory.facts_coexist`, ADR-27). |
| **MemoryConcept / consolidation** | A theme over a cluster of related current facts, with an LLM summary (`CONSOLIDATES` edges to its members) — the "sleep" pass (`openwiki consolidate`, B5). A *derived* view (recomputed, not snapshotted), like `Community`; supports global search over memory. |
| **Context assembly / `context_for`** | The B6 payoff: assemble a session's context from the three memory tiers — **identity** + **activation** (`recall`) + **attractors** (relevant `MemoryConcept`s) — into one block (`openwiki context` / MCP `wiki_memory`). "Load the concentrate, not the log." |
| **Usage log** | The append-only `graph.usage.jsonl` sidecar a read-only `ask`/MCP writes to; the next writer folds it into `REINFORCES` edges — read-path reinforcement without the write lock (B1/ADR-17). |
| **Write-ahead journal** | The lock-free `graph.journal.jsonl` sidecar holding queued `remember`/`reindex` ops when the graph is read-only; a later writer folds it in (`fold_journal`) — the concurrency mechanism (ADR-19). |
| **Mode (Wiki / Second Brain)** | A per-project policy (`[memory] enabled`): Wiki = document tier only (default); Second Brain = document + remembered tiers (ADR-14). |
| **Path A / Path B** | A = the consolidation layer (communities / global search, done); B = agent-memory — **complete (B0–B6)**: authoritative graph (B0), read-path reinforcement (B1), contradiction versioning (B4), sleep consolidation (B5), and three-tier context assembly (B6). **Path B+** = the Second-Brain refinements: B7 bi-temporal assertions (landed, ADR-27); A2 hierarchical communities and B8 priming (next). |

## 12.4 Platform & tooling

| Term | Definition |
|---|---|
| **Ollama** | The local LLM/embedding server (`localhost:11434`) providing all inference. |
| **Kuzu** | The embedded graph + vector database (single file); pins the project to Python ≤3.13 on Windows. |
| **MCP** | Model Context Protocol — the stdio JSON-RPC interface exposing read-only `wiki_*` tools to coding agents. |
| **SPA** | The no-build vanilla-JS single-page app served by the stdlib web server. |
| **Fingerprint chain** | Per-stage input+param hashes in `.openwiki/state.json` enabling incremental builds; each stage also records **duration + token spend** (observability). |
| **Observability / metrics collector** | The in-process, bounded ring buffer (`metrics.py`, `COLLECTOR`) capturing per-call latency + tokens Ollama returns; surfaced in the CLI `ask` footer, the **System** tab (`/api/metrics`), chat turns, and per-build-stage on Projekt (ADR-20). |
| **owiki (distribution)** | The PyPI distribution name (the *import* package stays `openwiki`; `openwiki` is taken on PyPI). Ships a wheel/sdist + a Docker image; publishing is license-gated (ADR-24). |
| **bge-m3 / qwen3** | Default embedding / chat models (multilingual, strong on the German corpora). |
| **World-model analysis (`owiki analyze`)** | The read-only, offline `analysis/` toolkit measuring the *structure* of the knowledge — coupling, gaps, compare, memory dynamics (ADR-25, §8.19). To *structure* what eval is to *retrieval*; enriched by the `[analysis]` extra. |
| **Graph↔semantic coupling / graph reach** | How the symbolic graph agrees with vs. adds to the embedding geometry; **graph reach** = the fraction of non-similarity edges the embedder would never rank as neighbors (≈36% on NAUTILUS) — the graph's non-semantic structure, quantified. |
| **Coupling fingerprint** | The compact, *relative*-metric dict `analyze_coupling` returns; two diff directly via `analyze --compare` (across corpora / versions / embedders / settings). |
| **Semantic map** | The 2-D projection (PCA, or UMAP via the extra) of pages in the Analyse tab — coloured by community, graph edges overlaid. |
| **Gap-mining** | The analysis→improvement loop (`analyze gaps`): ranked missing-cross-reference, near-duplicate, isolated-page, and entity-merge candidates. |
| **Memory-tier dynamics** | `analyze memory` over the Path B tier: revision (supersession rate), consolidation coverage, temperature (hot/cold), breadth, growth — the *learning* tier made legible. |
| **Ask mode** | The chat pane's read-only RAG mode (vs. the *Agent* tool/editing mode): interactive retrieval controls (GraphRAG/hybrid/re-rank/global/`k`) + **streamed**, cited answers (ADR-26). |
| **SSE streaming** | Server-Sent Events (`text/event-stream`) from the stdlib server: Ask answers stream token-by-token (`/api/ask/stream` → `ask_stream` → `RAGAgent.stream` → `chat_stream`), the graph lock held only around retrieval (ADR-26). |
| **Begriffe browser** | The web UI's canonical-entity explorer (name · type · aliases · description · mention pages · typed relations you can walk entity→entity) — surfaces resolution + relations (ADR-26, §8.20). |
| **Provenance (source / book)** | Each page's origin after a multi-source merge: its top-level-ancestor file (`source`) + the `sources/` subfolder (`book`); drives the sidebar filter over nav + search (ADR-26). |
| **Verwandte Seiten (related-pages panel)** | The reader overlay under each wiki page (`/api/related/{slug}`): *Verweise* (references), *Erwähnt in* (backlinks), *Verwandte Themen* (typed relations), *Ähnliche Seiten* (`SIMILAR_TO`), *Gemeinsame Begriffe* (shared entities) — graph connectivity shown where people read (ADR-28). |
| **Inline citation link** | A cross-reference phrase in the prose ("Abschnitt 1.6", "Seite 42") turned into a link to the page the graph resolved it to — every occurrence, from `REFERENCES.labels`; `openwiki references` adds the labels to an older graph in place (ADR-28, v0.83). |
| **Entity auto-link** | The first whole-word mention of each canonical entity (or alias) in a rendered page, linked to its Begriffe entry client-side — the page source stays verbatim (ADR-28). |
| **Temporal eval** | `examples/eval_temporal.jsonl` — 13 cross-session scenarios in 8 kinds (backfill, point-in-time, change-date, correction, known-at, multi-valued, planned, control) scoring B7; 7/13 (v0.80) → 13/13 (v0.82). |

## 12.5 Acronyms

| Acronym | Expansion |
|---|---|
| **ADR** | Architecture Decision Record (see §9) |
| **AGPL** | Affero General Public License (PyMuPDF's license; §2 LC1) |
| **ANN** | Approximate Nearest Neighbor (search — a scale option, §11 D3) |
| **arc42** | The architecture-documentation template these docs follow |
| **BM25** | Best Matching 25 — the lexical relevance function (hybrid retrieval, ADR-21) |
| **CI** | Continuous Integration (GitHub Actions runs the offline suite + Docker build, ADR-24) |
| **OIDC** | OpenID Connect (PyPI *trusted publishing* — token-less publish, ADR-24) |
| **PyPI** | The Python Package Index (distribution `owiki`, ADR-24) |
| **PCA / UMAP** | Principal Component Analysis / Uniform Manifold Approximation — the 2-D semantic-map projectors (PCA pure, UMAP via the `[analysis]` extra, ADR-25) |
| **ARI** | Adjusted Rand Index — community-vs-k-means agreement in the coupling analysis (needs the `[analysis]` extra, ADR-25) |
| **RRF** | Reciprocal Rank Fusion (blends dense + BM25 rankings, ADR-21) |
| **SSE** | Server-Sent Events (`text/event-stream`) — the web UI's token-streaming Ask answers (ADR-26) |
| **HNSW** | Hierarchical Navigable Small World (Kuzu's vector index; mirrored, not used for retrieval) |
| **HTTP / JSON** | HyperText Transfer Protocol / JavaScript Object Notation (the web API) |
| **IR** | Intermediate Representation (`ParsedDocument`) |
| **JSON-RPC** | JSON Remote Procedure Call (the MCP wire protocol, over stdio) |
| **KB** | Knowledge Base (a built wiki + index + graph) |
| **MCP** | Model Context Protocol (coding-agent tool interface) |
| **MoE** | Mixture of Experts (the default chat model's architecture) |
| **RAG / GraphRAG** | Retrieval-Augmented Generation / its graph-expanded variant |
| **SPA** | Single-Page Application (the no-build browser UI) |
| **TOML** | Tom's Obvious Minimal Language (`openwiki.toml`, config files) |

---
*Chapter complete. Path B terms landed (ADR-14–18); later terms cover the deepened graph (typed
relations, entity resolution — ADR-22/23), retrieval variants (hybrid, BM25, RRF, re-ranking — ADR-21),
observability (ADR-20), shipping (owiki, CI, OIDC — ADR-24), world-model analysis (coupling, graph
reach, fingerprint, semantic map, PCA/UMAP/ARI — ADR-25), the web UI (Ask mode, SSE streaming,
Begriffe browser, provenance — ADR-26; related-pages panel, entity auto-links — ADR-28), and bi-temporal
memory (valid / transaction time, as-of / known-at, backfill, retracted, coexistence check, temporal eval
— ADR-27).*
