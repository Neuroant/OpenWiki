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
   never mutates them.

The sample input is `301357_NAUTILUS_OG_G1.pdf`, the German Korg NAUTILUS
synthesizer manual (269 pages, 228 outline entries → a 51-page wiki → 815
embedded chunks → a graph of 51 pages / 815 chunks / 306 SIMILAR_TO + 122
REFERENCES edges).

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
wiki), `--similar-k N`, `--no-references` (skip the "siehe Seite N" edges), `-v`.

**Web UI** — browse + search + chat/edit + graph in the browser (stdlib server):
```
.venv\Scripts\python -m openwiki serve --port 8137        # http://127.0.0.1:8137
```
Options: `--wiki DIR`, `-i/--index DIR`, `--graph DIR`, `--bind ADDR`, `--port N`,
`--model NAME`, `--host URL`, `--temperature T`, `--dry-run`. The graph tab lights
up automatically if `--graph` (default `output/graph`) exists.

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
  and **`find_path`** — advertised in `schemas()` only when a graph is present.
- **`openwiki/chat_agent.py`** — `WikiAgent`: the multi-turn tool loop (model →
  tool calls → results → model …) with persistent history. Uses `chat_raw()`
  (tool calling) rather than `chat()`.
- **`openwiki/graph/`** — the Kuzu graph layer. `builder.py` (`GraphBuilder`)
  reads a `Wiki` + `SemanticIndex` and writes a property graph to `output/graph/`
  (Page/Chunk nodes; CHILD_OF/NEXT/PART_OF/SIMILAR_TO/REFERENCES edges; an HNSW
  index on `Chunk.emb` — embeddings **mirrored** from the index, which stays
  untouched). `references.py` extracts the manual's "siehe Seite N" cross-refs
  (see the offset note below); `store.py` (`GraphStore`) answers
  `neighborhood(slug)` (Graph tab), `find_path(a, b)` (shortest page-to-page path,
  powering the agent's `find_path` tool), and `hybrid_search(vec)` (vector→graph).
  Opened **read-only** and guarded by a lock (the threaded web server shares one
  connection). Only `builder`/`store` import `kuzu`; `references` does not.
- **`openwiki/web/`** — the web UI. `server.py` = `WikiWebApp` (state) + a
  `ThreadingHTTPServer` handler exposing a JSON API (`/api/wiki`,
  `/api/pages/{slug}`, `/api/search`, `/api/chat`, `/api/graph/{slug}`) plus
  static files; `serve()` runs it. `static/` = a no-build vanilla-JS SPA with client-side Markdown via a
  vendored `marked.min.js`. The center pane has three tabs (**Wiki / Hilfe /
  Tutorial**); Help & Tutorial are Markdown docs (`static/help.md`,
  `static/tutorial.md`) served as static files and rendered client-side. Tutorial
  actions are `run:<kind>:<arg>` links (`page`/`search`/`ask`) that `app.js`
  intercepts and drives against the live UI. Reuses
  `WikiTools`/`WikiAgent`/`SemanticIndex`.
- **`openwiki/cli.py`** — argparse CLI with `ingest`, `build-wiki`, `index`,
  `search`, `ask`, `chat`, `graph-build`, and `serve` subcommands. Add new
  capabilities as new subcommands, not as more flags.

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
  list-comprehension syntax.
- **Cross-references:** the manual cites *printed* page numbers but nodes are keyed
  by *physical* PDF pages. `references.detect_page_offset()` finds the constant
  offset (the mode of `physical - printed` over every integer in the page text —
  the true offset spikes because each page prints its own number; sample = 6),
  and `extract_references()` resolves each "Seite N" to the page whose physical
  span contains `N + offset`. `graph-build` computes these from the
  `ParsedDocument` (not the `Wiki`, which lacks per-physical-page text) and passes
  them to the builder. A page that is both a structural neighbor and a reference
  target shows as the structural rel (dedup order in `GraphStore.neighborhood`).

## Output

`output/` is gitignored. `ingest` writes `*.json` (the canonical artifact for
downstream features) and `*.md`; `build-wiki` writes `output/wiki/` (`index.md`,
`wiki.json`, `pages/*.md`); `index` writes `output/index/` (`embeddings.npy` +
`index.json`); `graph-build` writes `output/graph` (a single-file Kuzu DB);
`--images` additionally writes `output/images/`.
