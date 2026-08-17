# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

OpenWiki is a learning project for building **agentic wikis** — pipelines that
turn source documents into structured, machine-navigable knowledge bases. Two
stages are implemented:

1. **Ingestion** — a PDF parser that extracts text, tables, the table-of-contents
   outline, and images into a structured document model.
2. **Wiki generation** — splitting that model into a tree of linked wiki pages
   along the outline.
3. **Semantic search** — chunking the wiki pages, embedding them with a local
   Ollama model, and querying by meaning.
4. **RAG agent** — retrieving the top chunks for a question and having a local
   Ollama chat model answer with citations back to the wiki pages.
5. **Editing agent** — a multi-turn, tool-using agent that can search, read, and
   edit wiki pages (write-back) through Ollama tool calls.
6. **Web UI** — a zero-dependency browser UI (stdlib `http.server` + a vanilla-JS
   SPA) to browse, search, and chat/edit.
7. **Knowledge graph** — an additive Kuzu (embedded graph + vector DB) layer over
   the wiki, with an interactive Graph tab in the UI. Reads the wiki + index,
   never mutates them. Structural + vector + cross-reference edges, plus an opt-in
   LLM-extracted **entity layer** (`Entity` nodes + `MENTIONS`).

The sample input is `301357_NAUTILUS_OG_G1.pdf`, the German Korg NAUTILUS
synthesizer manual (269 pages, 228 outline entries → a 51-page wiki → 815
embedded chunks → a graph of 51 pages / 815 chunks / 306 SIMILAR_TO + 122
REFERENCES edges, plus 801 entities / 1431 MENTIONS with `--entities`).

## Environment & commands

Windows with a local virtualenv (`.venv`, **Python 3.13** — kuzu has no 3.14
Windows wheel; code targets 3.10–3.13). Interpreter paths below are Windows; on
macOS/Linux use `.venv/bin/python`.

**Setup** — editable install pulls in PyMuPDF + pytest and adds an `openwiki`
console script:
```
py -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
```

**Projects (`openwiki.toml`)** — group a knowledge base into a folder with a
manifest so state persists and you can keep several side by side. `openwiki init
[DIR] --source FILE` scaffolds `openwiki.toml` + `sources/` + `.gitignore`. Every
other command is **project-aware**: run inside a project (discovered from the CWD,
or pass `--project DIR`) and unset paths/models/host/split-level are filled from
the manifest — explicit flags always win, and with no manifest the historical
`./output` defaults apply (back-compat). **`openwiki build`** runs the whole
declared pipeline (ingest → wiki → index → graph) into the project's layout,
incrementally — a per-stage fingerprint chain in `.openwiki/state.json` skips
stages whose inputs+params are unchanged (`--only STAGES`, `--force`);
**`openwiki status`** reports sources, settings, and per-stage build state. A
user-global **`~/.openwiki/`** (override with `$OPENWIKI_HOME`) holds `config.toml`
(cross-project setting defaults — below a project's manifest, above built-in
defaults) and `registry.toml` (**`openwiki project list/use/add/remove/add-source`**;
the active project is a from-anywhere fallback used only when you're not inside one —
location always wins). A project may declare **multiple `[[sources]]`**: `build`
ingests each, keeps its per-source IR, and `combine_documents` merges them into one
corpus (page/table/image offsets + a synthetic top-level section per source; a
single source is passed through unchanged). Cross-references resolve **within** each
source (`graph.extract_references_multi`, per-source printed-page offsets). Design +
roadmap in `docs/projects.md` (the project concept is complete — Phases 1–4 landed).

**Run the ingestion tool** — writes `<stem>.json` + `<stem>.md` under `--out`
(default `./output`):
```
.venv\Scripts\python -m openwiki ingest 301357_NAUTILUS_OG_G1.pdf
```
Options: `--out DIR`, `--no-tables`, `--images`, `--max-pages N`, `-v`. For fast
iteration use `--max-pages 5` and/or `--no-tables` — table detection is the slow
part of a full run.

**Build the wiki** — splits a parsed document (a PDF *or* a `.json` from
`ingest`) into `index.md`, `wiki.json`, and `pages/*.md` under `--out`
(default `./output/wiki`):
```
.venv\Scripts\python -m openwiki build-wiki output\301357_NAUTILUS_OG_G1.json
```
Options: `--split-level N` (outline depth that becomes its own page; default 2),
`--out DIR`, `--no-tables`, `--images`, `-v`. Passing the `.json` skips re-parsing.

**Build the search index & query it** — requires a running
[Ollama](https://ollama.com) with the embedding model pulled
(`ollama pull bge-m3`):
```
.venv\Scripts\python -m openwiki index output\301357_NAUTILUS_OG_G1.json
.venv\Scripts\python -m openwiki search "Wie stelle ich die Lautstärke ein?"
```
`index` options: `--model NAME` (default `bge-m3`), `--split-level N`,
`--chunk-size W` / `--overlap W`, `--host URL`, `--out DIR`. `search` options:
`-k N`, `--full`, `--host URL`, `-i DIR`.

**Ask a question (RAG)** — retrieval + a local chat model, with citations back to
the wiki pages:
```
.venv\Scripts\python -m openwiki ask "Was ist Smooth Sound Transitions?"
```
Options: `--model NAME` (default `qwen3:30b-a3b-instruct-2507-q4_K_M`), `-k N`,
`--temperature T`, `--show-context`, `--host URL`, `-i DIR`, and (when a graph
exists) `--graph DIR` / `--expand-k N` / `--no-graph` for **graph-augmented
retrieval** — seeds are expanded along references + similar edges (sources marked
`+`).

**Chat + edit the wiki (multi-turn agent)** — searches, reads, and edits pages
via tool calls:
```
.venv\Scripts\python -m openwiki chat --show-tools          # interactive REPL
.venv\Scripts\python -m openwiki chat -m "turn 1" -m "turn 2"   # scripted, one session
```
Options: `-m/--message TEXT` (repeatable; omit for the REPL), `--wiki DIR`,
`--dry-run` (preview edits without writing), `--show-tools`, `--model NAME`,
`--host URL`, `-i DIR`, `--graph DIR` (enables the `graph_neighbors`/`find_path`
tools when the graph exists).

**Build the knowledge graph** — writes a Kuzu DB to `output/graph/` from a source
(PDF or `ingest` JSON) + the existing index (mirrors embeddings):
```
.venv\Scripts\python -m openwiki graph-build output\301357_NAUTILUS_OG_G1.json
```
Options: `--out DIR`, `-i/--index DIR`, `--split-level N` (must match the indexed
wiki), `--similar-k N`, `--no-references` (skip the page + section cross-ref edges),
`--entities` (LLM-extract typed entities → `Entity` + `MENTIONS`; **slow**, one
call/page), `--entity-model NAME`, `--entity-types "A,B,C"` (the domain ontology;
overrides the default), `--entity-max-chars N`, `-v`.

**Web UI** — browse + search + chat/edit + graph in the browser (stdlib server):
```
.venv\Scripts\python -m openwiki serve --port 8137        # http://127.0.0.1:8137
```
Options: `--wiki DIR`, `-i/--index DIR`, `--graph DIR`, `--bind ADDR`, `--port N`,
`--model NAME`, `--host URL`, `--temperature T`, `--dry-run`. The graph tab lights
up automatically if `--graph` (default `output/graph`) exists.

**MCP server (for coding agents)** — exposes RAG+GraphRAG as stdio MCP tools:
```
.venv\Scripts\python -m openwiki mcp --wiki output\wiki -i output\index --graph output\graph
```
Read-only tools (`wiki_ask`/`wiki_search`/`wiki_read_page`/`wiki_list_pages`/
`wiki_graph_neighbors`/`wiki_find_path`/`wiki_find_entity`), advertised by
availability. Options: `--model`, `--host`, `--no-ask`. Coding-agent setup is in
`docs/coding-agents.md` (+ `examples/coding-agents/`).

**Test** — the suite parses the first 5 pages of the sample PDF and skips
cleanly if PyMuPDF or the PDF is absent:
```
.venv\Scripts\python -m pytest
.venv\Scripts\python -m pytest tests/test_pdf_parser.py::test_metadata   # single test
```

## Architecture

A straight pipeline built around an intermediate representation (IR), so later
stages never touch PDF internals:

```
PDF ──PDFParser──▶ ParsedDocument (IR) ──▶ JSON / Markdown
                          │
                   WikiBuilder ──▶ Wiki ──▶ output/wiki/ (index.md, pages/*.md, wiki.json)
                                    │
                       chunk_wiki + Embedder ──▶ SemanticIndex ──▶ output/index/
                                                      │
                                          RAGAgent + ChatModel ──▶ cited answer
                                                      │
                                  WikiAgent + WikiTools ⇄ ChatModel ──▶ edits pages/*.md
                                                      │
                            GraphBuilder ──▶ Kuzu graph (output/graph/) ──▶ GraphStore
                                                      │
                                        WikiWebApp (http.server) ──▶ browser UI (SPA)
```

- **`openwiki/models.py`** — the IR. `ParsedDocument` = `DocumentMetadata` +
  `OutlineItem[]` + `Page[]`; each `Page` holds `text`, `TableData[]`,
  `ImageRef[]`. `OutlineItem.level` encodes the TOC tree, which is what a wiki's
  page hierarchy should derive from. Serialization lives here: `to_dict()`
  (canonical full-fidelity JSON) and `to_markdown()`.
- **`openwiki/pdf_parser.py`** — `PDFParser.parse(path, max_pages=None) ->
  ParsedDocument`. Each concern is an isolated `_read_*` method (metadata /
  outline / text / tables / images). **This is the only module that imports
  `fitz` (PyMuPDF).**
- **`openwiki/wiki.py`** — `WikiBuilder.build(doc) -> Wiki` splits the IR along
  the outline: entries with `level <= split_level` become pages, deeper ones
  become in-page contents. **Key constraint:** text is only separable at
  *PDF-page* granularity, so outline entries that start on the same PDF page are
  grouped into one wiki page. `write_wiki()` emits the Markdown + `wiki.json`.
  Depends only on `models.py`, so it also runs from a saved `.json` via
  `ParsedDocument.from_dict`.
- **`openwiki/chunking.py`** — `chunk_wiki(wiki)` cuts each `WikiPage.text` (clean
  text, never the rendered Markdown) into overlapping word-window `Chunk`s that
  carry provenance (page slug, title, PDF page range).
- **`openwiki/embeddings.py`** — the `Embedder` protocol + `OllamaEmbedder`
  (`/api/embed` via stdlib `urllib`, no API key). Swap embedding backends here
  without touching search.
- **`openwiki/search.py`** — `SemanticIndex.build/save/load/search`. A normalized
  NumPy embedding matrix with brute-force cosine (a dot product); no vector DB
  because the corpus is small. Persists to `output/index/` (`embeddings.npy` +
  `index.json`). `best_chunk_per_page(query, slugs)` re-ranks specific pages by
  the query — the query-relevance step of graph-augmented retrieval.
- **`openwiki/llm.py`** — the `ChatModel` protocol + `OllamaChat` (`/api/chat`,
  stdlib urllib). Parallels `embeddings.py`.
- **`openwiki/agent.py`** — `RAGAgent`: retrieve top chunks → number them as
  excerpts → a grounded system prompt → `ChatModel` → `RAGAnswer` (answer +
  `Source`s). `<think>…</think>` is stripped; `cited_markers()` reports which
  excerpts the answer referenced. With a `GraphStore` (`graph=`), `retrieve()`
  also **expands** the semantic seeds along references/similar edges (`_expand`)
  and re-ranks the added pages by the query — GraphRAG; those `Source`s have
  `kind="related"`.
- **`openwiki/tools.py`** — `WikiTools`: the tools the editing agent calls
  (`search_wiki`, `list_pages`, `read_page`, `edit_page`, `append_section`,
  `create_page`), each returning a string. File access is confined to `pages/`,
  slugs are validated, and writes go through a `dry_run`-aware writer + edit log.
  When a `GraphStore` is passed (`graph=`), it also exposes **`graph_neighbors`**
  and **`find_path`** (advertised only when a graph is present), and
  **`find_entity`** (only when the graph has entities — `_graph_has_entities()`).
  With a writable graph + an `embedder`, every successful write calls `_sync_graph`
  → `GraphStore.upsert_page`, so agent edits update the graph incrementally.
- **`openwiki/chat_agent.py`** — `WikiAgent`: the multi-turn tool loop (model →
  tool calls → results → model …) with persistent history. Uses `chat_raw()`
  (tool calling) rather than `chat()`.
- **`openwiki/graph/`** — the Kuzu graph layer. `builder.py` (`GraphBuilder`)
  reads a `Wiki` + `SemanticIndex` and writes a property graph to `output/graph/`
  (Page/Chunk nodes; CHILD_OF/NEXT/PART_OF/SIMILAR_TO/REFERENCES edges; an HNSW
  index on `Chunk.emb` — embeddings **mirrored** from the index, which stays
  untouched; plus opt-in `Entity` nodes + `MENTIONS`). `references.py` extracts
  page ("Seite N") + section/chapter ("Abschnitt 1.6", "Kapitel 2") cross-refs
  (see the offset note below);
  `entities.py` LLM-extracts typed entities per page (opt-in); `store.py`
  (`GraphStore`) answers `neighborhood(slug)` (agent's `graph_neighbors`, incl. a
  `shared_entity` group), `find_path(a, b)`, entity queries (`entities_for_page`,
  `pages_for_entity`, `has_entities`), `hybrid_search(vec)`, and the Graph‑tab
  explorer API `explore(slug)` / `expand(type, id)` (typed page + entity nodes).
  With `writable=True` it also **upserts** pages incrementally
  (`upsert_page(slug, text, embedder)`: MERGE the Page, replace its Chunks — the
  HNSW index self-maintains on insert/delete — recompute `SIMILAR_TO`). Opened
  read-only by default, `RLock`-guarded (an upsert holds the lock across a batch;
  the threaded web server shares one connection). Only `builder`/`store` import
  `kuzu`; `references`/`entities` do not.
- **`openwiki/web/`** — the web UI. `server.py` = `WikiWebApp` (state) + a
  `ThreadingHTTPServer` handler exposing a JSON API (`/api/wiki`,
  `/api/pages/{slug}`, `/api/search`, `/api/chat`, `/api/graph/{slug}` = explore,
  `/api/graph/expand`, `/api/project` = the active project's full overview,
  `/api/eval?top_k=&expand_k=` = the retrieval benchmark, `/api/compare` (POST) =
  one question through RAG + GraphRAG side by side, `/api/health` = KB quality
  metrics) plus static files (served `no-cache`); `serve()` runs it.
  The Graph tab is a hand-rolled **force-directed explorer** (`app.js`: `physicsTick`
  spring/charge sim, click-to-expand / double-click-to-collapse via a `parent`
  (introducer) pointer + `descendantsOf`, drag, edge-type filters, active-subgraph
  focus highlight (selected node's subtree emphasised, rest dimmed), greedy
  `declutterLabels` collision culling using real `getBBox` widths) — no JS libraries. `static/` = a no-build vanilla-JS SPA with client-side Markdown via a
  vendored `marked.min.js`. The center pane has six tabs (**Projekt / Wiki /
  Graph / Evaluation / Tutorial / Hilfe**). The **Evaluation tab** (`renderEval`,
  backed by `/api/eval` → `WikiWebApp.run_eval()`, reusing `eval.make_retrievers`)
  runs the project's `eval.jsonl` benchmark live with `top_k`/`expand_k` sliders,
  shows the RAG-vs-GraphRAG metric table (leading value highlighted) + miss
  drill-down, plus a **Live A/B** panel (`runCompare` → `/api/compare` →
  `WikiWebApp.compare()`) that sends one question through both retrievers side by
  side — the retrieved pages (seed vs `+Graph` badges, clickable) and, opt-in, both
  generated answers (2 chat calls, slow) — plus a **KB-health** panel (`renderHealth`
  → `/api/health` → `GraphStore.health()`): connectivity (avg degree, orphan/gap
  pages), entity singleton ratio, concept-hub bars, best-connected pages. All
  read-only. Help & Tutorial are Markdown docs
  (`static/help.md`, `static/tutorial.md`) served as static files and rendered
  client-side. The **Projekt tab** (`renderProject`, backed by `/api/project` →
  `WikiWebApp.project_info()`) is a complete read-only overview of the active
  project's knowledge model: sources, per-stage build status, **all** pipeline
  settings (build/models/graph/serve), the entity **ontology**, live graph stats
  from `GraphStore.stats()` (node/edge counts + an entity-type distribution bar
  chart), the semantic-index summary (model/dim/chunks), and the registered-project
  list — it renders `{"project": null}` gracefully when served outside a project.
  Tutorial actions are `run:<kind>:<arg>` links (`page`/`search`/`ask`/`tab`) that
  `app.js` intercepts and drives against the live UI. Reuses
  `WikiTools`/`WikiAgent`/`SemanticIndex`.
- **`openwiki/mcp_server.py`** — a dependency-free **stdio MCP server**
  (newline-delimited JSON-RPC 2.0: `initialize`/`tools/list`/`tools/call`), like
  the web layer but for coding agents. `build_server(...)` wraps
  `WikiTools`/`RAGAgent` as read-only `wiki_*` tools; `MCPStdioServer.handle()` is
  pure (unit-tested without stdio).
- **`openwiki/project.py`** — the **project** layer: `Project` (discover via
  `find`, `load`, `resolve`; `out_dir`/`wiki_dir`/`index_dir`/`graph_path`; manifest
  `setting()` lookup) + a hand-rolled `render_manifest` (stdlib `tomllib` *reads*
  TOML but can't *write* it). Only this module + `cli.py` know about projects; the
  pipeline stays project-agnostic and keeps taking explicit paths.
- **`openwiki/pipeline.py`** — Phase 2 build orchestration *state*: a per-stage
  **fingerprint chain** (`compute_fingerprints`) + the `.openwiki/state.json`
  lockfile (`BuildState`) + `stale_stages()`. Pure/testable; the CLI's `_cmd_build`
  does the actual stage execution (PDFParser → WikiBuilder → SemanticIndex → GraphBuilder).
- **`openwiki/eval.py`** — `owiki eval`: retrieval evaluation. Pure ranking metrics
  (`reciprocal_rank`/`hit_at_k`/`recall_at_k`) + an `evaluate(items, retrieve, k)` driver
  that takes a `retrieve(question) -> ranked page slugs` callable, so it's backend-agnostic
  and unit-testable with fakes (no Ollama/Kuzu). The CLI plugs in two retrievers over the
  same budget `top_k+expand_k`: **RAG** = top semantic pages; **GraphRAG** = `top_k` semantic
  seeds + `expand_k` graph-expanded (same `_EXPAND_RELS` + `best_chunk_per_page` as the
  agent). Eval sets are per-project JSONL (`<project>/eval.jsonl`: `{"question","pages"}`).
  The controlled same-budget comparison is deliberate: it asks whether graph expansion beats
  *more* semantic hits (on definitional Qs it does not — semantic is already near-perfect;
  the graph is for relational/multi-hop questions).
- **`openwiki/merge.py`** — `combine_documents(docs, names)` merges several
  `ParsedDocument`s into one corpus (concatenate pages with a running offset, shift
  table/image page numbers, wrap each source under a synthetic level-1 outline node
  so slugs don't collide). Pure IR (depends only on `models`); single source ⇒
  passthrough. Pairs with `graph.extract_references_multi` for per-source cross-refs.
- **`openwiki/userconfig.py`** — user-global state under `~/.openwiki/`: `UserConfig`
  (`config.toml` cross-project setting defaults) + `Registry` (`registry.toml` named
  projects + active pointer). Read via `tomllib`; the registry has a tiny hand-rolled
  writer. `cli._resolve_project` adds the registry fallback on top of `Project.resolve`.
- **`openwiki/ontology.py`** — `owiki ontology`: samples the corpus + one LLM call to
  **propose** a domain `entity_types` ontology (names + descriptions + examples) that
  you review/`--write` into the manifest. Scaffolding, not a build stage — extraction
  stays deterministic. Pure (`propose_ontology`/`sample_corpus`/`format_entity_types`).
- **`openwiki/opencode_template.py`** — `owiki opencode` (and `owiki init --opencode`):
  scaffolds a ready-to-run **OpenCode** config into a project — `opencode.json` (local
  Ollama provider + the `openwiki` MCP server) + the `openwiki` agent and its
  `/openwiki-help`/`/openwiki-tutorial` commands under `.opencode/`. Generated from the
  project's own model/host, **project-agnostic** (no sample-corpus specifics), and the
  MCP command is `owiki mcp` with **project discovery** (CWD = the project folder), so
  `cd`-ing into any project and running OpenCode gets an agent scoped to *that* wiki.
  Pure string/JSON rendering (`render_files`) + file writes (`scaffold_opencode`); the
  CLI picks the MCP command (`owiki` if on PATH, else `sys.executable -m openwiki`).
- **`openwiki/claude_code_template.py`** — `owiki claude-code`: the same idea for
  **Claude Code** — writes a project-scoped `.mcp.json` (registers `openwiki` as an MCP
  server via `owiki mcp` discovery) plus `.claude/commands/*` (`wiki-ask`,
  `wiki-explore`, `openwiki-help`) and an auto-applied `.claude/skills/openwiki`. Same
  `render_files`/`scaffold_claude_code` shape; shares `cli._mcp_command()`.
- **`openwiki/cli.py`** — argparse CLI with `init`, `build`, `status`, `project`
  (`list`/`use`/`add`/`remove`/`add-source`), `opencode`, `claude-code`, `ontology`, `ingest`,
  `build-wiki`, `index`, `search`, `eval`, `ask`, `chat`, `graph-build`, `serve`, and `mcp`
  subcommands. A shared
  `--project` (parent parser) + `_apply_project(args, project)` fill unset
  path/model/host/split-level args from the active project before dispatch (flags
  override; no project → `./output`). Add new capabilities as new subcommands, not
  as more flags.

### Conventions & gotchas

- Keep PyMuPDF (`fitz`) confined to `pdf_parser.py`; everything else depends only
  on `models.py`. That boundary is what will let a second source parser (HTML,
  Docling, ...) slot in without touching downstream code.
- All file I/O is UTF-8 with `ensure_ascii=False` — sample content is German and
  non-ASCII round-tripping is asserted in `tests/test_pdf_parser.py`.
- Table extraction uses `page.find_tables()` (heuristic; failures are caught
  per-page and logged, never raised).
- `conftest.py` at the repo root puts the root on `sys.path`, so `pytest` works
  from a bare checkout even without the editable install.
- PyMuPDF prints a one-line hint about the optional `pymupdf_layout` package for
  improved layout analysis — a candidate future upgrade for higher-fidelity
  structure, not currently a dependency.
- Semantic search needs a running **Ollama** server (default
  `http://localhost:11434`) with the model pulled. Default `bge-m3` (multilingual,
  1024-dim) is chosen because the corpus is German. Tests avoid the network with a
  `FakeEmbedder`; the one real Ollama test skips when the server/model is absent.
- The RAG chat model defaults to `qwen3:30b-a3b-instruct-2507-q4_K_M` (strong on German,
  already pulled). The agent is deliberately grounded — the system prompt forbids
  answering beyond the excerpts — so answer quality tracks retrieval quality
  (`-k`, chunk size). Agent tests use a `FakeChat`, so they stay offline too.
- The `chat` (editing) agent writes to `output/wiki/pages/` in place — once built,
  the wiki is the living artifact (re-running `build-wiki` would overwrite it).
  Tool calling uses Ollama's `/api/chat` `tools`; the default model supports it.
  The tool loop is tested offline with a `ScriptedChat`, and `WikiTools` guards
  writes (slug-validated paths inside `pages/`, unique-match `edit_page`,
  `--dry-run`).
- The web UI (`serve`) is stdlib-only; the SPA is no-build and renders Markdown
  client-side via the vendored `openwiki/web/static/marked.min.js`. Internal
  `*.md` links are intercepted to route within the SPA; after an agent write tool
  the open page + nav auto-refresh. The chat panel is hidden below a 1100px
  viewport (CSS breakpoint), and a `favicon.ico` 404 in the console is benign.
  `test_web.py` covers the app + a live-socket round-trip offline.
- Tutorial `run:` links: `marked` URL-encodes the arg (spaces → `%20`, umlauts →
  `%C3%A4`), so `wireRunActions()` in `app.js` `decodeURIComponent`s it before
  dispatching. To add a doc tab, drop a `.md` in `static/`, add a `.tab` button in
  `index.html`, and handle it in `renderActiveTab()`.
- `index` rebuilds the `Wiki` in memory from the source at `--split-level`; it
  does **not** read `output/wiki/`. `graph-build` does the same, so keep
  `--split-level` consistent across `index` and `graph-build` or the graph's page
  slugs won't match the index's chunk provenance.
- `cli.main()` reconfigures stdout/stderr to UTF-8 so umlauts render on Windows.
- **Kuzu:** the graph is a *mirror* — `SemanticIndex` stays the source of truth;
  embeddings are copied into `Chunk` nodes so vector search + traversal work in
  one Cypher query. Kuzu 0.11 stores the DB as a **single file** (+ `.wal`), not a
  directory — `GraphBuilder._remove_existing()` handles both on rebuild. Vector
  API: `CALL CREATE_VECTOR_INDEX(table, name, prop)` /
  `CALL QUERY_VECTOR_INDEX(table, name, $vec, k) RETURN node.*, distance` (the
  extension is statically linked — no `INSTALL`/`LOAD`). No Windows 3.14 wheel, so
  the project runs on 3.13. The Graph tab (`app.js` `drawGraph`) is hand-rolled
  SVG; graph tests use a `FakeEmbedder` and `pytest.importorskip("kuzu")`.
- `find_path` uses Kuzu's shortest-path syntax:
  `p = (a)-[:CHILD_OF|NEXT|SIMILAR_TO|REFERENCES* SHORTEST 1..N]-(b)` (restricted to
  Page↔Page rels so it never routes through `Chunk`), and reads results with
  `list_transform(nodes(p), x -> x.slug)` / `... x.title` and
  `list_transform(rels(p), x -> label(x))` — Kuzu has **no** `[n IN nodes(p) | ...]`
  list-comprehension syntax. String matching uses `contains(lower(x), lower($q))`.
- **Entities** (opt-in): `entities.py` does one LLM call per page with a **typed
  ontology that is configurable per project** — `DEFAULT_ENTITY_TYPES` (tuned to the
  synth sample) unless overridden by `[graph] entity_types` in `openwiki.toml` (or
  `graph-build --entity-types`); `coerce_types()` accepts a name list, `"Name: desc"`
  strings, or a dict, and `[graph] entity_max_chars` caps the text per call. It parses
  a JSON array and resolves by normalized
  name-within-type (`_normalize`: lowercase + strip German articles) so surface
  variants merge. `Entity`/`MENTIONS` tables are **always created** (empty without
  `--entities`), so store/agent code degrades gracefully; `has_entities()` gates
  the `shared_entity` edges, the `find_entity` tool, and the entity term in RAG
  expansion (`agent._EXPAND_RELS`). Extraction is slow (~1 call/page) — run it in
  the background; tests use a deterministic fake chat.
- **Incremental updates:** `serve`/`chat` open the graph **writable** (exclusive
  Kuzu lock) when an index is present and not `--dry-run`, passing `index.embedder`
  to `WikiTools`; edits then upsert into the graph live. Writable is exclusive, so
  one such process at a time (there's a read-only fallback if the lock is held).
  Only `SIMILAR_TO` is recomputed on upsert — CHILD_OF/NEXT, REFERENCES and
  entities still need a full `graph-build`. Kuzu's HNSW index supports incremental
- **Outline synthesis** (`outline.py`): when a source PDF has **no bookmarks**,
  `openwiki build` (with `[build] synthesize_outline`, default on) derives a flat
  section outline from **numbered running headers** (e.g. `10.1 Title` at the top of
  each page) — first-appearance page per distinct section — so the wiki splits a
  document into section pages instead of one page. Text-only heuristic; returns `[]`
  (→ keep the original outline) unless it finds ≥3 distinct sections. PDFs *with*
  bookmarks are untouched.
  insert/delete (verified), so no index rebuild; `DROP_VECTOR_INDEX` is buggy in
  0.11 — avoid it.
- **Cross-references:** `references.py` extracts two citation styles as Page→Page
  edges (unioned). **(a) Page numbers** — the manual cites *printed* page numbers but
  nodes are keyed by *physical* PDF pages. `detect_page_offset()` finds the constant
  offset (the mode of `physical - printed` over every integer in the page text — the
  true offset spikes because each page prints its own number; sample = 6), and
  resolves each "Seite N" to the page whose physical span contains `N + offset`.
  **(b) Section/chapter numbers** — "Abschnitt 1.6" / "Kapitel 2" (dominant in the
  informatik lecture corpus; the synth manual barely used page refs). `_section_page_map`
  reads the running headers (`N.M Title` / `Kapitel N` at the top of each page) into a
  *section-number → physical-page* map, and `_section_edges` resolves each ref against
  it. Note `outline.synthesize_outline` *drops* the section number from page titles, so
  this re-derives it from the headers at graph-build time. `graph-build` computes both
  from the `ParsedDocument` (not the `Wiki`, which lacks per-physical-page text); for a
  **merged** corpus `extract_references_multi` scopes the section map **per source
  window** so "Kapitel 2" never leaks between sources (informatik: 2 page-ref → 32 total
  edges). A page that is both a structural neighbor and a reference target shows as the
  structural rel (dedup order in `GraphStore.neighborhood`).

## Output

`output/` is gitignored. `ingest` writes `*.json` (the canonical artifact for
downstream features) and `*.md`; `build-wiki` writes `output/wiki/` (`index.md`,
`wiki.json`, `pages/*.md`); `index` writes `output/index/` (`embeddings.npy` +
`index.json`); `graph-build` writes `output/graph` (a single-file Kuzu DB);
`--images` additionally writes `output/images/`.
