# 8. Cross-cutting Concepts

> arc42 §8 — Principles and patterns that apply across many building blocks.
> **Status: complete.**

## 8.1 Domain model — the IR

`ParsedDocument` (`models.py`) is the single intermediate representation: `DocumentMetadata`
+ `OutlineItem[]` (the TOC tree) + `Page[]` (each with `text`, `TableData[]`, `ImageRef[]`).
`OutlineItem.level` encodes the hierarchy the wiki's page tree derives from. Every source
type parses **into** this model; every downstream stage reads **only** this model.

## 8.2 The dependency boundary

The single most important structural rule: **only `pdf_parser.py` imports `fitz`; only
`graph/builder.py` and `graph/store.py` import `kuzu`.** Everything else depends on
`models.py` (and numpy/stdlib). This is what lets new parsers and backends slot in without
downstream change, and lets most of the codebase be tested without PyMuPDF/Kuzu installed.

## 8.3 Backend protocols (pluggability)

`Embedder` (`embeddings.py`) and `ChatModel` (`llm.py`) are small Protocols. `OllamaEmbedder`
/ `OllamaChat` implement them; the agent, search, and eval depend on the protocol, never on
Ollama. Tests inject `FakeEmbedder` / `FakeChat` / `ScriptedChat`.

## 8.4 Grounding & provenance

RAG and editing agents are deliberately grounded: the system prompt forbids answering beyond
the provided excerpts. Every retrieval result and graph node carries provenance (page slug,
PDF page range); answers cite `[n]` markers that resolve back to pages. `cited_markers()`
reports which excerpts were used — the basis for the grounding metrics in §10/eval.

## 8.5 Persistence formats

| Artifact | Format | Producer |
|---|---|---|
| Parsed doc | `<stem>.json` (canonical) + `.md` | `ingest` |
| Wiki | `index.md`, `wiki.json`, `pages/*.md` | `build-wiki` |
| Index | `embeddings.npy` + `index.json` | `index` |
| Graph | single-file Kuzu DB (+ `.wal`) | `graph-build` |
| Usage log | `graph.usage.jsonl` (append-only sidecar) | read-path `ask`/MCP (B1) |
| Build state | `.openwiki/state.json` (fingerprints) | `build` |
| Config | `openwiki.toml`, `~/.openwiki/*.toml` | `init` / registry |

The graph has **two tiers**. The **document tier** is a *mirror*: embeddings are copied into
`Chunk` nodes, the NumPy index stays the source of truth, and a rebuild is a pure function of its
inputs (ADR-3). The **remembered tier** (Path B: `Session`/`Assertion`/`SUPERSEDES` + the
`REINFORCES` usage overlay) is *authoritative* — `GraphBuilder` snapshots and restores it across a
rebuild, so it survives re-ingesting sources (ADR-16). See §8.15.

## 8.6 Concurrency

`GraphStore` guards its single Kuzu connection with a re-entrant lock (`RLock`); an upsert
holds it across a batch. The threaded web server shares one connection. Kuzu 0.11 is
**reader-XOR-writer** (measured): a writable connection blocks *all* readers, and readers block a
writer — multiple readers coexist, but there is **no** simultaneous read+write.
**So all writes are decoupled from the lock via a write-ahead journal (B1 / ADR-19):** `serve`/`chat`
open **read-only by default** (readers run concurrently), and writes *queue* to a lock-free JSONL
sidecar rather than contend — reinforce pairs → `graph.usage.jsonl` (ADR-17), and `remember` /
host-`capture` / a chat-edit's graph re-sync → `graph.journal.jsonl` (`queue_remember`/`queue_reindex`).
A writer **folds** the journal (`fold_journal`) at `serve`/`chat` start+shutdown, in `decay`, or on the
next `remember`; writable opens retry-with-backoff for transient contention. `--sync` opts back into a
held-writable connection (live edit-sync, exclusive). This is the reachable ceiling under Kuzu —
concurrent reads + never-blocked writes, not true simultaneity.

## 8.7 Configuration & settings resolution

Unset settings resolve by precedence: **explicit flag > project `openwiki.toml` >
`~/.openwiki/config.toml` > built-in default** (`cli._apply_project`). With no project, the
historical `./output` defaults apply (back-compat).

## 8.8 Internationalization / encoding

The reference corpora are German. All file I/O is UTF-8 with `ensure_ascii=False`; entity
normalization folds umlauts/ß and German inflections; `cli.main()` reconfigures stdout/stderr
to UTF-8 so non-ASCII renders on Windows code pages.

## 8.9 Testing strategy

Pure logic (ranking metrics, decay math, community detection, reference/entity resolution,
MCP dispatch) is unit-tested with plain values. I/O-bound layers use fakes; Kuzu-dependent
tests are `pytest.importorskip("kuzu")`-gated. The suite runs fully offline.

## 8.10 Graceful degradation

Optional layers are always-created (possibly empty) tables and best-effort queries
(try/except → empty), so store/agent/UI code works whether or not entities, communities,
reinforcement, or the Path B memory tables (`Session`/`Assertion`/`ASSERTS`/`SUPERSEDES`) exist —
and on graphs built before a layer was added (a lazy `_ensure_memory_schema` `IF NOT EXISTS`
migration upgrades pre-existing graphs).

## 8.11 Error handling (model / network)

Calls to Ollama go through stdlib `urllib`; a `URLError`/`HTTPError` is turned into a
`RuntimeError` carrying a "is Ollama running / is the model pulled?" hint. It surfaces per
entry point (detail in §6.8): CLI → stderr + non-zero exit; web API → HTTP **503**; editing
agent → `WikiTools.dispatch` catches per-tool exceptions and returns an `ERROR: …` string the
model can react to, keeping the loop alive. Table extraction and graph-hiccups during an
agent write are caught and logged, never raised (a failed graph sync must not fail the edit).
This is distinct from §8.10, which is about *optional layers* being absent.

## 8.12 Security posture

The system assumes a **trusted local host** (§3.3, §11 R1):

- **No authentication / authorization** on `serve` (including its *write* paths) or MCP.
  Safe on `127.0.0.1`; exposing beyond localhost requires adding authN/authZ first (§7.4).
- **Path confinement** — `WikiTools` validates slugs against a strict pattern and refuses any
  resolved path outside `pages/` (`_page_path`), so `read/edit/create` can't escape the wiki.
- **MCP is read-only** — coding-agent tools never write; `edit`/`create` are not exposed there.
- **`--dry-run`** — edits can be previewed (no file write, no graph sync) before committing.
- **No secrets** — no API keys anywhere (local Ollama, ADR-2); nothing to leak.

## 8.13 Extensibility recipes

The boundaries (§5, §8.2/§8.3) exist so common extensions are local, single-file changes:

| To add… | Do this | Nothing else changes because… |
|---|---|---|
| **A source format** | New parser module with `parse() -> ParsedDocument` (reuse `markdown_parser.sections_to_document` for heading-based formats); add a case to `sources.parse_source` + `source_type`/`is_supported`/`SUPPORTED_SUFFIXES`. | downstream depends only on the IR (ADR-1). |
| **An embedding / chat backend** | Implement the `Embedder` / `ChatModel` protocol; select it via `get_embedder` / config. | search/agent/eval depend on the protocol (ADR-2). |
| **A CLI capability** | Add an argparse subcommand + a `_cmd_*` handler + a `_DISPATCH` entry + an `_apply_project` branch. | capabilities are subcommands, not flags (ADR-13). |
| **An optional graph layer** | Add an always-created (empty) table in `GraphBuilder._create_schema` + best-effort `GraphStore` methods + a lazy `IF NOT EXISTS` migration for old graphs. | store/UI code treats layers as best-effort (ADR-7). |

## 8.14 The project — the organizing unit

A **project** (an `openwiki.toml` in a folder) is OpenWiki's top-level structural concept:
the unit that groups sources, generated artifacts, settings, and build state so several
knowledge bases sit side by side and persist between commands. It mirrors familiar build
tools (full analogy + phase history in `docs/projects.md`):

| Classical tool | OpenWiki |
|---|---|
| `git init` / `cargo new` | `openwiki init` |
| `pyproject.toml` / `Cargo.toml` | `openwiki.toml` (identity + declarative config) |
| `src/` | `sources/` (files copied in; URLs/repos referenced in place) |
| `target/` · `build/` | `output/` (`wiki/`, `index/`, `graph`) — gitignored |
| `Cargo.lock` | `.openwiki/state.json` (build provenance + staleness) |
| `cargo build` / `make` | `openwiki build` (runs the pipeline from the manifest) |
| find `.git` upward | discover `openwiki.toml` upward |
| `conda activate` | `openwiki project use <name>` (registry) |

Its parts and where they live:

- **Discovery & identity** (`project.py`) — find the manifest upward from the CWD (or
  `--project` / `$OPENWIKI_PROJECT`); expose `out_dir` / `wiki_dir` / `index_dir` / `graph_path`.
- **Settings precedence** (§8.7, ADR-10) — flag > manifest > `~/.openwiki/config.toml` > default.
- **Registry** (`userconfig.py`) — a user-global list of named projects + an active pointer; a
  *from-anywhere* fallback used only when you're **not** inside a project (location always wins).
- **Multi-source corpus** (`merge.py`) — several `[[sources]]` (file / URL / repo) merged into one
  `ParsedDocument` (`combine_documents`), with per-source cross-references.
- **Incremental build state** (`pipeline.py`, ADR-11) — the per-stage fingerprint chain in
  `.openwiki/state.json` that drives `build` / `status`.

The pipeline itself stays **project-agnostic**: only `project.py` + `cli.py` know about
projects, every stage still takes explicit paths, and with no manifest the historical `./output`
defaults apply (back-compat). This keeps the project a thin *organizing* layer over an unchanged
pipeline. Deep design + roadmap: `docs/projects.md`; layout on disk: §7.

## 8.15 The remembered tier (Path B agent memory)

Alongside the document tier, a project in **Second Brain mode** (`[memory] enabled`, ADR-14) grows a
**remembered tier** — the graph accumulating what the agent *learns from experience*, not only what it
*derives from documents*. It is additive (ADR-7) and authoritative (ADR-16); Wiki Mode is literally
"memory tier off." Four concepts span it:

- **Reified facts (ADR-15).** A remembered fact is an `Assertion` node (subject · predicate · object +
  `session_id`, `created_at`, a mirrored embedding) under a `Session`, captured from a transcript by a
  pure, chat-injected pass (`memory.capture_session`). Reification is what makes a fact versionable + provenanced.
- **Merge, not append (B3).** `remember` embeds each fact and **dedups** against the *current*
  assertions by normalized `(subject, predicate, object)` (`_normalize`, ADR-12), so re-affirming a
  fact is a no-op.
- **Contradiction as supersession (ADR-18).** A newer fact with the same normalized subject+predicate
  but a different object adds `(new)-[:SUPERSEDES]->(old)` — nothing deleted, "current" = no incoming
  `SUPERSEDES`, validity intervals derivable. `recall` returns current facts only; history stays queryable (`--all`).
- **Activation + forgetting.** `recall` ranks assertions by **decay-weighted** cosine (`effective_weight`,
  the same half-life math as the `REINFORCES` usage overlay), and read-path `record_usage` / `fold_usage`
  (B1) + `decay` keep the graph at a useful density — strengthen what's used, fade what isn't.
- **Consolidation — the "sleep" pass (B5).** `openwiki consolidate` clusters the current assertions by
  embedding similarity (the doc-community Louvain, re-targeted) and LLM-summarizes each cluster into a
  **`MemoryConcept`** theme, then folds usage + decays. Like `Community` it's a *derived* view
  (recomputed, not snapshotted), so the consolidated footprint stays **bounded** as raw history grows;
  the theme summaries are the memory's "attractor" tier and support global search over memory.
- **Three-tier context assembly (B6).** `GraphStore.context_for(query, …)` fuses the tiers into one
  session context: **identity** (the project / `[memory] identity`) + **activation** (`recall`) +
  **attractors** (the themes the recalled facts belong to). Read-only + **fail-soft** (any tier may be
  empty). Exposed as the `context` CLI, the MCP `wiki_memory` tool, and — via `claude-code --hooks` —
  **Claude Code host hooks** (`UserPromptSubmit`→inject, `SessionEnd`/`PreCompact`→capture, through the
  fail-soft `owiki hook` command), so memory flows automatically. The cross-session eval scores it
  ("assembled" beats raw-log). *Load the concentrate, not the log.*

The lifecycle is **independent of documents** (ADR-14/16): `graph-build` rebuilds the document tier but
preserves the remembered tier; consolidation touches memory, never documents. The payoff metric — *does
assembled memory make the next session better?* — is the cross-session eval (`eval --cross-session`,
§10). Full design + the staged B0–B6 plan: `docs/path-b-memory.md`.

## 8.16 Observability (ADR-20)

Every Ollama call already returns latency + prompt/eval token counts; historically they were thrown
away. A pure, stdlib **metrics collector** (`metrics.py` — a thread-safe, bounded ring buffer +
`parse_ollama_stats`) captures them: the LLM/embedding backends record a `chat`/`embed` event per call
(best-effort — a metrics failure never breaks the call), the web layer records per-request `http`
events, and `openwiki build` diffs the collector per stage. It is **always-on, zero-config, no
dependency, and bounded** (so it's safe to leave running). Surfaced four ways: the CLI `ask` `⏱` footer,
the web **System** tab (`/api/metrics` — per-kind p50/p95 + token totals + a live event table), per-turn
`chat()` stats in the agent panel, and per-build-stage **duration + token spend** on the Projekt tab.
The point is honest visibility (it revealed that one agent "turn" is several model calls, and a slow
first answer is mostly cold-model *load* time), not a monitoring stack.

## 8.17 Retrieval strategy — layered, and *measured* (ADR-9/21)

Retrieval is a stack of **opt-in** layers over one baseline, each earning its place through `owiki eval`
rather than assumption:

- **Dense (baseline).** Brute-force cosine over bge-m3 chunk embeddings (`search.py`). Strong on this
  corpus — which is *why* the add-ons below don't beat it here.
- **GraphRAG expansion (ADR-6/22).** Seeds + pages reached along graph edges (references / similar /
  shared-entity / typed **relations** / reinforced), re-ranked by the query. Doesn't lift retrieval
  *recall* on strong-embedder prose, but lifts **answer quality** + enables global search.
- **Hybrid (ADR-21).** BM25 (`lexical.py`) fused with dense via reciprocal rank fusion (`search_hybrid`)
  — catches exact tokens the embedder blurs. Ties dense on prose; **wins decisively on code**.
- **LLM re-rank (ADR-21).** One chat call reorders a wider candidate pool (`rerank.py`). On this corpus
  it *doesn't* help (drops MRR).

The discipline (ADR-9) is the crosscutting concept: **add a retriever as a scored row in `owiki eval`
before trusting it**, and match the technique to where the embedder is weak. Full numbers +
methodology: `docs/RAG-vs-GraphRAG.md`.

## 8.18 The semantic graph layer — entities, relations, resolution (ADR-12/22/23)

Over the structural + vector graph sits an **opt-in, LLM-extracted** semantic layer, built in three
deterministic-where-possible stages:

- **Entities (ADR-12).** One LLM call per page extracts typed named entities against a per-project
  ontology; resolved to a **deterministic** normalized key (`_normalize`) so spelling/plural/word-order
  variants merge without a model. `Entity` nodes + `Page-[:MENTIONS]->Entity`.
- **Typed relations (ADR-22).** A second per-page pass extracts subject–predicate–object triples *among a
  page's entities*, grounded to them and merged across pages into `Entity-[:RELATED_TO {predicate}]->Entity`
  — co-mention becomes a traversable graph, and expansion follows it (§8.17).
- **Resolution (ADR-23).** A corpus-wide pass merges same-concept variants the *deterministic* normalizer
  can't see (spacing, near-synonyms) into **canonical** entities with `aliases` + a `description` —
  embedding candidate clusters (high recall) confirmed by a small per-cluster LLM call (precision). It
  never drops an entity, and aliases make acronyms/synonyms findable.

The whole layer is additive (ADR-7, always-created empty) and derived (rebuilt each `graph-build`, never
snapshotted — unlike the remembered tier, §8.15), so store/agent code degrades gracefully when it's off.

## 8.19 World-model analysis (ADR-25)

OpenWiki holds two representations of the *same* corpus — the symbolic **graph** and the continuous
**semantic space** — plus a memory tier that changes over time. The `analysis/` package (`owiki analyze`)
measures their **structure and organization**; it is to *structure* what the eval harness (§8.17) is to
*retrieval*. All of it is **read-only + additive** (never mutates graph/index, per ADR-3) and **offline**
where possible (it reads the *stored* embeddings — no Ollama). The core is pure NumPy; the heavier bits
(silhouette/ARI, UMAP) sit behind an opt-in `[analysis]` extra and degrade gracefully.

- **Coupling** — the central, OpenWiki-specific idea: *where does the graph agree with the embedding
  geometry (redundant) vs. add non-semantic structure?* Per-edge-type endpoint-cosine **against a
  random-pair null** (the space is anisotropic, so lift-over-null, not raw cosine, is the signal), graph-vs-
  kNN neighbor overlap, community coherence, and the headline **graph reach** — the fraction of non-similarity
  edges the embedder would never rank as neighbors. This quantifies the RAG-vs-GraphRAG finding (≈36%).
- **Semantic map** — a 2-D projection (PCA, or UMAP with the extra) of the pages, coloured by community,
  graph edges overlaid — the Analyse tab makes the reach *visible*.
- **Gaps** — the analysis→improvement loop: ranked missing-cross-reference, near-duplicate, isolated-page,
  and entity-merge candidates (a to-do list, not just a dashboard).
- **Compare** — a coupling **fingerprint** is a compact, *relative*-metric description, so two diff directly
  (across corpora / versions / embedders / settings).
- **Memory dynamics** — over the Path B tier (§8.15): revision (supersession rate), consolidation coverage,
  temperature (hot/cold by decayed weight + confidence), breadth, and growth per session — the *learning*
  tier made legible.

Interpretability rests on **baselines** (the random-pair null, `--compare`), not absolute thresholds — the
same measured-claims discipline as ADR-9.

---
*Chapter complete. Cross-refs: runtime error paths → §6.8; the memory tier → §8.15 + ADR-14/15/16/18;
observability → §8.16 + ADR-20; retrieval → §8.17 + ADR-9/21 + `docs/RAG-vs-GraphRAG.md`; the semantic
graph → §8.18 + ADR-12/22/23; world-model analysis → §8.19 + ADR-25; the no-auth risk → §11 R1; the project
concept → §5, ADR-10/11, §7; the boundaries these concepts rest on → §5.1 + ADR-1/2/7/13.*
