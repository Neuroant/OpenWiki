# 8. Cross-cutting Concepts

> arc42 §8 — Principles and patterns that apply across many building blocks.
> **Status: draft.**

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
| Build state | `.openwiki/state.json` (fingerprints) | `build` |
| Config | `openwiki.toml`, `~/.openwiki/*.toml` | `init` / registry |

The graph is a **mirror**: embeddings are copied into `Chunk` nodes; the NumPy index stays
the source of truth. A rebuild is a pure function of its inputs.

## 8.6 Concurrency

`GraphStore` guards its single Kuzu connection with a re-entrant lock (`RLock`); an upsert
holds it across a batch. The threaded web server shares one connection. Writable access is
exclusive (Kuzu lock) — hence one writable process at a time with a read-only fallback.

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
(try/except → empty), so store/agent/UI code works whether or not entities, communities, or
reinforcement edges exist — and on graphs built before a layer was added.

---
TODO (completion steps): add "error handling" (Ollama unreachable messages, table-absent
fallbacks) as an explicit concept; add "security" cross-ref to §11 (no auth); add a small
"extensibility recipe" (how to add a parser / a backend / a subcommand).
