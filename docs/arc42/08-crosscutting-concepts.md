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
holds it across a batch. The threaded web server shares one connection. Writable access is
exclusive (Kuzu lock) — hence one writable process at a time with a read-only fallback.
**Read-path writes are decoupled from the lock (B1):** a read-only process records usage by
*appending* to `graph.usage.jsonl` (no lock), and the next writable process folds it in — so reads
"learn" without ever contending for exclusive access (ADR-17).

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
  empty). Exposed as the `context` CLI + MCP `wiki_memory`; the cross-session eval scores it
  ("assembled" beats raw-log). *Load the concentrate, not the log.*

The lifecycle is **independent of documents** (ADR-14/16): `graph-build` rebuilds the document tier but
preserves the remembered tier; consolidation touches memory, never documents. The payoff metric — *does
assembled memory make the next session better?* — is the cross-session eval (`eval --cross-session`,
§10). Full design + the staged B0–B6 plan: `docs/path-b-memory.md`.

---
*Chapter complete. Cross-refs: runtime error paths → §6.8; the memory tier → §8.15 + ADR-14/15/16/18;
the no-auth risk → §11 R1; the project concept → §5 (project/pipeline/userconfig/merge), ADR-10/11, §7,
`docs/projects.md`; the boundaries these concepts rest on → §5.1 + ADR-1/2/7/13.*
