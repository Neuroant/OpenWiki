# OpenWiki

Learning to build **agentic wikis** — pipelines that turn source documents into
structured, machine-navigable knowledge bases.

It starts with **ingestion**: `openwiki ingest` turns a source — a **PDF** (via
PyMuPDF), a **Markdown / plain-text** file, a **web page** (an `http(s)` URL or a
local `.html`), or a **source-code repository** (a directory), the latter three on
the stdlib alone — into a structured document model (text, tables, outline, and
optionally images), then writes it as **JSON** (for downstream agents / retrieval)
and **Markdown** (for humans). New source types slot in behind
`sources.parse_source` without touching the rest of the pipeline.

Then `openwiki build-wiki` splits that model into a tree of linked Markdown wiki
pages along the document's outline — an `index.md`, one file per section, and a
`wiki.json` manifest.

Then `openwiki index` + `openwiki search` add **semantic search**: the wiki pages
are chunked, embedded with a local Ollama model, and queried by meaning.

Then `openwiki ask` puts a **RAG agent** on top: it retrieves the most relevant
chunks and has a local Ollama chat model answer your question with citations back
to the wiki pages.

Then `openwiki chat` is a **multi-turn agent** that can not only answer but also
**edit** the wiki — searching, reading, and writing pages through tool calls.

Then `openwiki graph-build` adds a **knowledge-graph layer** (Kuzu, an embedded
graph + vector DB): pages, hierarchy, reading order, provenance, semantic-
similarity and cross-reference edges, plus an opt-in LLM-extracted **entity
layer** — an additional level of abstraction over the wiki that never modifies it.

Finally, `openwiki serve` puts it all in the browser: a **web UI** to browse
pages, search, chat with the agent (including its editing tools), and **explore
the graph** interactively.

The sample document is `301357_NAUTILUS_OG_G1.pdf` — the German Korg NAUTILUS
synthesizer manual (269 pages, 228 outline entries).

## Quickstart

Requires Python **3.10–3.13** — **not 3.14** (the Kuzu graph + vector DB has no
3.14 wheel yet). If `py --list` shows **3.14 as your default** (`*`), create the
venv with 3.13 explicitly or you'll hit *"no valid python found"* on install:

```bash
py -3.13 -m venv .venv                                 # Windows: force 3.13
.venv\Scripts\python -m pip install -e ".[dev]"
# macOS/Linux:  python3.13 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

> Only the **venv** needs to be 3.13 — your system default can stay 3.14. Verify
> with `.venv\Scripts\python --version`; set `PY_PYTHON=3.13` to make `py` default
> to it. (`uv` users: `uv venv --python 3.13`.)

Activate the environment (`.venv\Scripts\activate` on Windows,
`source .venv/bin/activate` elsewhere), then ingest the sample PDF:

```bash
openwiki ingest 301357_NAUTILUS_OG_G1.pdf
# equivalently: python -m openwiki ingest 301357_NAUTILUS_OG_G1.pdf
```

Results land in `./output/`:

- `301357_NAUTILUS_OG_G1.json` — full structured extraction
- `301357_NAUTILUS_OG_G1.md` — readable Markdown

### Options

| Flag | Effect |
| --- | --- |
| `--out DIR` | Output directory (default `./output`) |
| `--no-tables` | Skip table detection (much faster) |
| `--images` | Extract embedded images to `<out>/images/` |
| `--max-pages N` | Parse only the first N pages |
| `-v` | Verbose progress logging |

Quick check on the first few pages:

```bash
openwiki ingest 301357_NAUTILUS_OG_G1.pdf --max-pages 5 -v
```

### Build a wiki from the extracted content

```bash
openwiki build-wiki output/301357_NAUTILUS_OG_G1.json
# ...or straight from the PDF:  openwiki build-wiki 301357_NAUTILUS_OG_G1.pdf
```

This writes `output/wiki/`:

```
output/wiki/
  index.md        # nested table of contents linking to every page
  wiki.json       # manifest: page tree, titles, PDF page ranges
  pages/
    000-front-matter.md
    003-vorstellung-des-nautilus.md
    ...
```

Every page gets breadcrumbs, a subpage list, an in-page contents list, the
section text and tables, and prev/next navigation. Control granularity with
`--split-level N` (default `2` = chapters + major sections; `1` = chapters only).

### Semantic search

Semantic search needs a running [Ollama](https://ollama.com) with the embedding
model pulled:

```bash
ollama pull bge-m3        # multilingual, 1024-dim (good for the German content)
```

Build the index, then query by meaning:

```bash
openwiki index output/301357_NAUTILUS_OG_G1.json
openwiki search "Wie stelle ich die Lautstärke ein?" -k 5
```

Results are ranked by cosine similarity and point back to the wiki page and PDF
page range:

```
1. [0.703] Sampeln im SEQUENCER-Modus  ·  PDF p.154–156
    pages/030-sampeln-im-sequencer-modus.md   (030-sampeln-im-sequencer-modus#4)
    REC- und SAMPLING START/STOP-Button, um die Aufnahmebereitschaft zu …
```

Swap models with `--model` (e.g. `--model nomic-embed-text`); the backend is
pluggable via the `Embedder` protocol in `openwiki/embeddings.py`.

### Ask questions (RAG)

`ask` retrieves the top chunks and has a local chat model answer with citations.
Pull a chat model first (any instruct model works; this is the default):

```bash
ollama pull qwen3:30b-a3b-instruct-2507-q4_K_M
```

```bash
openwiki ask "Was ist Smooth Sound Transitions (SST) und wozu dient es?"
```

```
Smooth Sound Transitions (SST) sorgt dafür, dass Sounds beim Wechsel zwischen
Programmen, Kombinationen oder Songs natürlich ausklingen statt abgeschnitten zu
werden [1][3].

Sources  (* = cited):
 *[1] Smooth Sound Transitions (SST)  ·  PDF p.127–128  ·  pages/025-…md  (0.729)
  [2] Inhalt  ·  PDF p.3–6  ·  pages/002-inhalt.md  (0.635)
 *[3] Set Lists  ·  PDF p.119–119  ·  pages/022-set-lists.md  (0.631)
```

Options: `--model` (default `qwen3:30b-a3b-instruct-2507-q4_K_M`), `-k` (chunks to
retrieve), `--temperature`, `--show-context` (print the excerpts too). The system
prompt keeps the model **grounded** — it answers only from the retrieved excerpts
and says so when the answer isn't there.

**Graph-augmented retrieval:** if a knowledge graph is present (`graph-build`),
`ask` expands the semantic seeds along graph edges (references, similar, and —
with `--entities` — shared concepts), adds the most query-relevant *connected*
pages, and marks them `+` in the sources — surfacing context that pure top-k
cosine misses. Control with `--expand-k N` (default 3; `0` or `--no-graph`
disables).

### Chat + edit the wiki (agent)

`chat` is a multi-turn agent that can search, read, and **edit** wiki pages via
tool calls. Script turns with `-m` (repeatable, one session), or omit `-m` for an
interactive REPL:

```bash
openwiki chat --show-tools -m "Erstelle eine Seite 'glossar' mit den Begriffen Programm und Kombination." -m "Ergänze das Glossar um den Begriff 'Arpeggiator'."
```

```
  · create_page(slug=glossar, title=Glossar, …) → OK: wrote 'glossar' (338 chars).
  · append_section(slug=glossar, heading=Arpeggiator, …) → OK: wrote 'glossar' (508 chars).

assistant> Der Begriff 'Arpeggiator' wurde dem Glossar hinzugefügt.
```

Tools: `search_wiki`, `list_pages`, `read_page`, `edit_page`, `append_section`,
`create_page`. Edits are written into `output/wiki/pages/` — use `--dry-run` to
preview them first. File access is confined to the pages directory.

If a knowledge graph is present (and not `--dry-run`), edits update it
**incrementally**: a created or edited page is upserted into the graph — chunked,
embedded, and linked by `SIMILAR_TO` — so it joins the Graph tab and the agent's
graph tools right away, without a full `graph-build`. (Structural/reference/entity
edges still come from a full rebuild.)

If a knowledge graph is present (`graph-build`), the agent also gets
**`graph_neighbors`** (a page's related pages) and **`find_path`** (the shortest
relationship chain between two pages) — so it can answer "what's related to X?"
and "how are X and Y connected?" by traversing the graph, not just searching text.
When the graph includes the entity layer (`--entities`), it additionally gets
**`find_entity`** — every page that mentions a named concept.

### Knowledge graph

`graph-build` adds an **additive graph layer** in [Kuzu](https://kuzudb.com) (an
embedded graph + vector database) — it reads the wiki and the existing index and
writes a graph to `output/graph/`, **without modifying the wiki**. The NumPy
index stays the source of truth; the chunk embeddings are *mirrored* into the
graph so vector search and graph traversal work together.

```bash
openwiki graph-build output/301357_NAUTILUS_OG_G1.json
```

Graph model (all edges deterministic or vector-derived — no LLM):

- `(Page)-[:CHILD_OF]->(Page)` — outline hierarchy
- `(Page)-[:NEXT]->(Page)` — reading order
- `(Chunk)-[:PART_OF]->(Page)` — provenance
- `(Page)-[:SIMILAR_TO {score}]->(Page)` — top-k semantic neighbors
- `(Page)-[:REFERENCES]->(Page)` — the manual's *"siehe Seite N"* cross-references
- `(Page)-[:MENTIONS]->(Entity)` — typed entities (opt-in; see below)
- plus an HNSW vector index on `Chunk.emb` for hybrid vector→graph queries.

The `REFERENCES` edges resolve the manual's **printed** page numbers to the
**physical** PDF pages our nodes use, by auto-detecting the constant offset
(`--no-references` skips this). On the sample: offset 6 → 122 cross-reference edges.

Explore it in the browser via the **Graph** tab (below). Requires Python
3.10–3.13 (`pip install kuzu`).

**Entity layer (semantic graph, opt-in).** `graph-build --entities` runs one
local LLM call per page to extract typed entities — `Mode`, `SoundObject`,
`Effect`, `Feature`, `Parameter`, `Hardware` — and links them with
`(Page)-[:MENTIONS]->(Entity)`. This connects pages that discuss the same concept
even when they neither cross-reference nor are cosine-similar. It's slow (~one
call per page), so it's off by default:

```bash
openwiki graph-build output/301357_NAUTILUS_OG_G1.json --entities -v
```

With entities present, the Graph tab shows **"Gemeinsame Begriffe"** (shared-
concept) edges, the agent gains a **`find_entity`** tool, and graph-augmented
`ask` also expands along shared concepts.

The type **ontology is configurable per project** — the default is tuned to the
synth sample, so for another domain set `[graph] entity_types` in `openwiki.toml`
(or `graph-build --entity-types "Concept,Theorem,Algorithm,…"`). Don't want to
write it by hand? **`openwiki ontology`** samples your corpus and proposes a fitting
one (`--write` drops it straight into the manifest).

### Web UI

`serve` starts a local web UI (stdlib `http.server`, no extra dependencies) that
combines everything: browse the page tree, run semantic search, chat with the
agent — including asking it to edit pages, which updates the open page live — and
explore the knowledge graph.

```bash
openwiki serve --port 8137
# then open http://127.0.0.1:8137
```

Left pane: search + nav tree. Center: the rendered page. Right: the agent chat
(tool calls are shown; write tools flagged with ✎). Use `--dry-run` to let the
agent preview edits without writing. Needs the wiki (`build-wiki`) and — for
search/chat — the index (`index`) and a running Ollama.

The center pane has four tabs:

- **Wiki** — the rendered pages (the default view).
- **Hilfe** — an extensive reference (interface, search, agent + its tools,
  privacy, troubleshooting).
- **Tutorial** — a guided tour where each step has a **▶ Ausprobieren** button
  that runs the real action (open a page, run a search, ask the agent, create a
  page), so you learn by doing.
- **Graph** — an interactive **force-directed explorer** around the current page.
  Two node types (pages as circles, entities as diamonds) with colour-coded edges
  (hierarchy, sequence, similar, cross-references, shared concepts, mentions).
  **Click a node to expand** it (pull in its neighbours / an entity's pages) and
  **double-click to collapse** the subtree it opened — so you build up *and* tear
  down the graph. **Drag** nodes to arrange, toggle **edge-type filters**, and
  "Seite öffnen" opens a page. The clicked node's subtree is highlighted (heavier
  edges, accent-ringed anchor) while the rest dims, so the active subgraph stands
  out. Labels auto-declutter (culled names appear on hover). Needs `graph-build`
  (entities with `--entities`).

The Help/Tutorial content lives in `openwiki/web/static/{help,tutorial}.md` and is
rendered client-side; tutorial buttons are `run:<kind>:<arg>` links wired to the
live UI.

### Use from coding agents (MCP)

`openwiki mcp` exposes the wiki's **RAG + GraphRAG** as an
[MCP](https://modelcontextprotocol.io) server (dependency-free, stdio), so coding
agents — **Claude Code**, **OpenCode**, Cursor, … — can query it as tools:
`wiki_ask` (grounded, cited answers), `wiki_search`, `wiki_read_page`,
`wiki_graph_neighbors`, `wiki_find_path`, `wiki_find_entity`.

```bash
openwiki mcp --wiki output/wiki --index output/index --graph output/graph
```

Setup and copy-paste config/commands/skills for Claude Code and OpenCode are in
**[docs/coding-agents.md](docs/coding-agents.md)** (examples under
[`examples/coding-agents/`](examples/coding-agents/)).

## Projects — persist state, jump between wikis

Instead of managing `output/` by hand and re-typing paths and flags, group a
knowledge base into a **project**: a folder with an `openwiki.toml` manifest and
its own outputs. Every command run inside a project (discovered from the working
directory, or via `--project DIR`) reads its settings from the manifest — explicit
flags still win, and outside a project the old `./output` defaults apply.

```bash
openwiki init my-manual --source path/to/manual.pdf   # scaffold openwiki.toml + sources/
cd my-manual
openwiki build                                        # ingest → wiki → index → graph, per the manifest
openwiki status                                       # sources, settings, per-stage build state
openwiki serve --port 8137                            # serves THIS project's wiki/index/graph
```

`openwiki init` writes a commented `openwiki.toml` you edit to taste:

```toml
[project]
name = "my-manual"

[[sources]]                 # one or more; all merge into a single corpus
type = "pdf"
path = "sources/manual.pdf"

[build]
split_level = 2             # shared by index & graph, so their slugs can't drift
chunk_size = 180
overlap = 30

[models]
host  = "http://localhost:11434"
embed = "bge-m3"
chat  = "qwen3:30b-a3b-instruct-2507-q4_K_M"

[graph]
similar_k = 6
references = true
entities = false
```

- **`openwiki build`** runs the whole pipeline into the project's `output/`,
  **incrementally** — a per-stage fingerprint chain in `.openwiki/state.json` skips
  stages whose inputs and settings are unchanged (`--only ingest,wiki,index,graph`,
  `--force`). No more keeping `--split-level` in sync between `index` and `graph-build`.
- **Multiple `[[sources]]` of any type** merge into one corpus — each becomes a
  top-level wiki section. A source can be a **file** (pdf/md/txt/html, copied into
  `sources/`), an **`http(s)` URL**, or a **code-repo directory** (both referenced in
  place):
  ```bash
  openwiki project add-source path/to/other.pdf          # a file
  openwiki project add-source https://example.com/page   # a web page
  openwiki project add-source ../my-service --repo        # a code repository
  ```
  Point `--source` / `add-source` at a **folder** (without `--repo`) to register all
  its files at once, or a glob: `openwiki init proj --source "C:\docs\*.pdf"`.
- **Registry** — register projects and switch by name from anywhere:
  ```bash
  openwiki project add my-manual .     # register (name → path)
  openwiki project use my-manual       # set the active project
  openwiki project list                # * marks the active one
  ```
  Location always wins: inside a project folder you get *that* project; the active
  registry entry is only a fallback for when you're not in one.
- **Global defaults** — put cross-project settings (e.g. your Ollama host/models)
  in `~/.openwiki/config.toml`; they apply below a project's manifest and above the
  built-in defaults.

Version everything except the artifacts: commit `openwiki.toml` + `sources/`,
gitignore `output/` and `.openwiki/`, and anyone can `openwiki build` to regenerate
the whole knowledge base. Full design + roadmap:
**[docs/projects.md](docs/projects.md)**.

## Create a new project

You install OpenWiki **once** (the `openwiki` command, in a Python **3.13** venv) and
then create **as many projects as you like**. A project is just a *data folder* with
an `openwiki.toml`, its sources, and its outputs — you do **not** clone or copy this
repository, and you do **not** create a new virtual environment, per project.

| | What it is | How many |
| --- | --- | --- |
| **The install** | the `openwiki` package in a Python 3.13 venv | **once** |
| **A project** | any folder with an `openwiki.toml` (+ `sources/`, `output/`) | **as many as you want, anywhere** |

**1. Make the `openwiki` command reachable** — pick one:

```bash
.venv\Scripts\activate                       # activate the venv (Windows) → `openwiki` on PATH
# …or call it by full path, no activation:   <repo>\.venv\Scripts\openwiki …
# …or install a global CLI via pipx (isolated 3.13 venv) — use the helper script:
#   .\install-openwiki.ps1        # Windows   ·   ./install-openwiki.sh   # macOS/Linux
```

> The package installs **two identical commands**: `openwiki` and **`owiki`**. If
> another tool on your PATH already provides an `openwiki` command, just use
> **`owiki`** — same tool, collision-free.

**2. Scaffold → build → serve** — in any folder you like:

```bash
openwiki init C:\wikis\my-manual --source C:\docs\my-manual.pdf
cd C:\wikis\my-manual
openwiki build                               # ingest → wiki → index → graph (needs Ollama running)
openwiki serve --port 8137                   # then http://127.0.0.1:8137
```

That folder is now **self-contained** (`openwiki.toml`, `sources/`, `output/`).
Repeat `openwiki init` elsewhere for more projects, add another source with
`openwiki project add-source other.pdf`, and switch between projects by `cd` — or by
name after registering them (`openwiki project add` / `openwiki project use`).
Cross-project defaults (your Ollama host/models) live in `~/.openwiki/config.toml`.
See **Projects** above for the manifest and registry details.

**3. (optional) Add a local OpenCode agent** — give the project its own
[OpenCode](https://opencode.ai) setup: an `openwiki` agent running on your local
model, with this project's wiki wired in as an MCP server.

```bash
openwiki opencode                            # inside the project → writes opencode.json + .opencode/
# …or fold it into init:  openwiki init C:\wikis\my-manual --source … --opencode
cd C:\wikis\my-manual && opencode            # the 'openwiki' agent now queries THIS project
```

The config is generated from the project's own model/host and uses `owiki mcp` with
project discovery, so the agent is always scoped to the folder it lives in — no
hardcoded paths, and no drifting to another project's corpus. Slash commands
`/openwiki-help` and `/openwiki-tutorial` come with it.

> You only clone this repo and `pip install -e .` if you want to **modify OpenWiki
> itself**. To just *use* it for building wikis, one install plus project folders is
> all you need — and that venv must be Python **3.13**, not 3.14 (see Quickstart).

## Deployment

OpenWiki runs locally with no cloud services — "deploying" it means standing up the
Python app plus a local Ollama on a host, then serving the browser UI (and/or the
MCP server).

1. **Prerequisites** — Python **3.10–3.13** (**not 3.14** — no Kuzu wheel yet; build
   the venv with `py -3.13`) and [Ollama](https://ollama.com). Pull
   the models once, and make sure Ollama is running (`ollama serve` or the desktop app):
   ```bash
   ollama pull bge-m3
   ollama pull qwen3:30b-a3b-instruct-2507-q4_K_M
   ```
   For longer documents raise Ollama's context window — set
   `OLLAMA_CONTEXT_LENGTH=16384` (or higher) in the environment `ollama serve` starts from.

2. **Install** — clone, create a venv, install:
   ```bash
   git clone https://github.com/Neuroant/OpenWiki.git && cd OpenWiki
   py -3.13 -m venv .venv && .venv\Scripts\python -m pip install -e .   # Windows (force 3.13)
   # macOS/Linux:  python3.13 -m venv .venv && .venv/bin/pip install -e .
   ```

3. **Build a knowledge base** (as a project, recommended):
   ```bash
   openwiki init kb --source path/to/document.pdf
   cd kb && openwiki build
   ```

4. **Serve** — bind to the host so others on the network can reach it, and keep it
   running under your OS service manager (systemd, Windows Task Scheduler / NSSM,
   `tmux`, or a container):
   ```bash
   openwiki serve --bind 0.0.0.0 --port 8137     # then browse http://<host>:8137
   ```
   To expose it to a coding agent instead of/along with the browser, run `openwiki
   mcp` (see above).

> **Security:** the web UI and MCP server have **no authentication**, and the chat
> agent can **edit wiki pages**. Only bind `0.0.0.0` on a trusted network; put a
> reverse proxy with auth in front for anything wider; use `--dry-run` to let the
> agent preview edits without writing. All processing (embeddings + LLM) is local
> via Ollama — nothing is sent to third parties.

## Use as a library

```python
from openwiki import PDFParser

doc = PDFParser().parse("301357_NAUTILUS_OG_G1.pdf", max_pages=10)
print(doc.metadata.title, doc.metadata.page_count)

for item in doc.outline:                    # the table-of-contents tree
    print("  " * (item.level - 1), item.title)

print(doc.pages[0].text)
```

## Tests

```bash
python -m pytest
```

## How it fits together

```
PDF ──PDFParser──▶ ParsedDocument ──▶ JSON / Markdown
                        │  (metadata, outline, pages[text, tables, images])
                 WikiBuilder ──▶ Wiki ──▶ output/wiki/ (index.md, pages/*.md, wiki.json)
                        │
              chunk_wiki + Embedder ──▶ SemanticIndex ──▶ output/index/
                        │
                RAGAgent + ChatModel ──▶ cited answer
                        │
             WikiAgent + WikiTools ──▶ edits pages/*.md
                        │
              GraphBuilder ──▶ Kuzu graph (output/graph/) ──▶ GraphStore
                        │
              WikiWebApp (http.server) ──▶ browser UI (Wiki · Hilfe · Tutorial · Graph)
```

- `openwiki/models.py` — the structured document model shared by everything downstream
- `openwiki/pdf_parser.py` — PyMuPDF-based extraction (the only place PDF internals live)
- `openwiki/wiki.py` — splits the model into linked wiki pages along the outline
- `openwiki/chunking.py` — cuts page text into overlapping chunks with provenance
- `openwiki/embeddings.py` — pluggable embedding backends (`OllamaEmbedder`)
- `openwiki/search.py` — the semantic index (embed, persist, cosine query)
- `openwiki/llm.py` — pluggable chat backends (`OllamaChat`)
- `openwiki/agent.py` — the RAG agent (retrieve → grounded prompt → cited answer)
- `openwiki/tools.py` — the read/write tools the editing agent calls
- `openwiki/chat_agent.py` — the multi-turn editing agent (tool loop + history)
- `openwiki/graph/` — the Kuzu graph layer (`builder.py` writes it, `store.py` queries **and incrementally upserts** it, `references.py` extracts cross-references, `entities.py` extracts typed entities via an LLM)
- `openwiki/web/` — stdlib web server + vanilla-JS SPA (browse, search, chat/edit, graph)
- `openwiki/mcp_server.py` — stdio MCP server exposing the wiki as tools for coding agents
- `openwiki/project.py` · `pipeline.py` · `userconfig.py` · `merge.py` — the **project** layer: the `openwiki.toml` model + resolution, the `openwiki build` fingerprint/staleness state, the `~/.openwiki/` global config + registry, and multi-source merge
- `openwiki/outline.py` — synthesizes a section outline from heading text when a PDF has no bookmarks (finer wiki pages)
- `openwiki/ontology.py` — proposes a domain entity ontology from the corpus (one LLM call) for `openwiki ontology`
- `openwiki/cli.py` — the `openwiki` command line (`init`, `build`, `status`, `project`, `ontology`, `ingest`, `build-wiki`, `index`, `search`, `ask`, `chat`, `graph-build`, `serve`, `mcp`)

## Roadmap

- [x] **Ingestion** — PDF → structured model (JSON + Markdown)
- [x] **Wiki generation** — split into linked pages along the outline tree
- [x] **Semantic search** — chunk + embed (local Ollama) with cosine retrieval
- [x] **RAG agent** — grounded, cited answers via a local Ollama chat model
- [x] **Editing agent** — multi-turn, tool-using agent that edits wiki pages (write-back)
- [x] **Web UI** — browse, search, and chat/edit in the browser (`openwiki serve`)
- [x] **Knowledge graph** — Kuzu graph + vector layer with an interactive Graph tab
- [x] **Cross-references** — `REFERENCES` edges from the manual's "siehe Seite N" (printed→physical offset detection)
- [x] **Graph-aware agent tools** — `graph_neighbors` and `find_path` let the agent traverse the graph (multi-hop, "how are X and Y connected?")
- [x] **Graph-augmented `ask`** — RAG retrieval expands along graph edges (GraphRAG): semantic seeds + query-re-ranked connected pages
- [x] **Entity layer** — LLM-extracted typed entities + `MENTIONS` edges (`--entities`), a `find_entity` tool, and shared-concept edges/expansion
- [x] **Incremental graph updates** — agent edits upsert the page into the graph live (chunks + embeddings + `SIMILAR_TO`), no rebuild needed
- [x] **Project workspaces** — `openwiki.toml` projects with `openwiki init`/`build` (incremental) + `status`, a `~/.openwiki/` registry + global config, and multi-source merge ([docs/projects.md](docs/projects.md))
