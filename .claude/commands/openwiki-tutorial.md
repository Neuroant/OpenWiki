---
description: Interactive, hands-on tutorial covering every OpenWiki feature (ingest → wiki → index → semantic search → RAG → knowledge graph)
argument-hint: "[module number/name, or 'all' — omit to start at the beginning]"
---
You are running the **OpenWiki interactive tutorial**. Teach the user *every*
feature of OpenWiki, hands-on, one module at a time, until they can build a wiki +
knowledge graph from their own documents and query it with semantic search, RAG,
and graph traversal.

Requested starting point: **$ARGUMENTS** (empty = start at Module 0, in order).

## How to run it
- Teach **one module at a time**. For each: (1) explain the idea in 2–4 sentences,
  (2) show the exact command(s) or tool call, (3) run a **live demo** when you have
  the tools, (4) give a short **"Your turn"** exercise, (5) then STOP and wait for
  the user to say continue ("weiter" / "next") before the next module. If
  `$ARGUMENTS` names a module, jump straight to it; if it's `all`, run every module
  without stopping but still show the demos.
- **Live demo vs. terminal step.** The pipeline commands (`ingest`, `build-wiki`,
  `index`, `graph-build`, `serve`) run in a *terminal*. If you have a shell tool,
  offer to run them and show real output; if you only have the `wiki_*` MCP tools,
  present the command for the user to run, then continue. The search / RAG / graph
  features you can always demo live through the `wiki_*` tools.
- **Detect what exists:** call `wiki_list_pages` first — if it works, the wiki +
  index are live and you can demo search/RAG. Try `wiki_graph_neighbors` on a slug;
  if it works, the graph is built (Modules 8 & 10 are live). If a tool is missing,
  tell the user which artifact to build first, then keep teaching.
- Be concrete and **cite the page slugs** the tools return. The sample wiki is the
  German Korg NAUTILUS manual, so its content is German — answer in the user's
  language but keep German titles/slugs verbatim. End each module with a one-line
  recap and the command(s) to remember.

## Fact sheet (ground truth — use these exact commands)
Interpreter: `.venv\Scripts\python` (Windows) or `.venv/bin/python` (macOS/Linux);
every command is `<python> -m openwiki <subcommand>`. Needs a running **Ollama**
with `bge-m3` (embeddings) and `qwen3:30b-a3b-instruct-2507-q4_K_M` (chat) pulled.

- **Setup:** `py -m venv .venv` → `.venv\Scripts\python -m pip install -e ".[dev]"`.
- **ingest** `<pdf>` → `output/<stem>.json` (canonical IR) + `.md`. `--out DIR`,
  `--no-tables`, `--images`, `--max-pages N`, `-v`. (Fast: `--max-pages 5 --no-tables`.)
- **build-wiki** `<pdf|json>` → `output/wiki/` (`index.md`, `wiki.json`,
  `pages/*.md`). `--split-level N` = outline depth that becomes its own page (default 2).
- **index** `<pdf|json>` → `output/index/` (`embeddings.npy` + `index.json`).
  `--model bge-m3`, `--split-level N`, `--chunk-size W`, `--overlap W`.
- **search** `"query"` — semantic search. `-k N`, `--full`.
- **ask** `"question"` — RAG: retrieve + local chat model + citations. `-k N`,
  `--show-context`; with a graph, `--graph DIR` / `--expand-k N` / `--no-graph` for
  **graph-augmented** retrieval (seeds expanded along references + similar edges;
  those sources are marked `+`).
- **chat** — multi-turn editing agent (search/read/edit pages via tools). `-m "msg"`
  (repeatable) or a REPL; `--dry-run`, `--show-tools`. Edits update the graph incrementally.
- **graph-build** `<pdf|json>` → `output/graph/` (single-file Kuzu DB). Nodes
  Page/Chunk; edges CHILD_OF/NEXT/PART_OF/SIMILAR_TO/REFERENCES; an HNSW vector index
  (embeddings mirrored from the index). `--similar-k N`, `--no-references`,
  `--entities` (LLM-extract typed `Entity` + `MENTIONS`; slow, ~1 call/page). Keep
  `--split-level` the SAME as `index` or slugs won't match.
- **serve** `--port 8137` — web UI (Wiki / Hilfe / Tutorial tabs + a force-directed
  Graph explorer). `--graph DIR` lights up the Graph tab.
- **mcp** — the server exposing the `wiki_*` tools you're using now.

MCP tools you can call live: `wiki_ask` (RAG+GraphRAG, cited), `wiki_search`,
`wiki_read_page`, `wiki_list_pages`, `wiki_graph_neighbors`, `wiki_find_path`,
`wiki_find_entity`.

## Curriculum
0. **Orientation** — what OpenWiki is and the pipeline PDF → IR → wiki → index →
   RAG → graph. Prove the sample wiki is live with `wiki_list_pages`.
1. **Project init** — venv, editable install, Ollama models, a source PDF. (Terminal.)
2. **Ingestion** — `ingest`; the `ParsedDocument` IR (metadata, outline, pages,
   tables, images) and the JSON/MD it writes. (Terminal.)
3. **Wiki generation** — `build-wiki`, `--split-level`, the `pages/*.md` tree +
   `wiki.json`. Demo `wiki_read_page` on a slug.
4. **Semantic index** — `index`; overlapping word-window chunks (with provenance) +
   `bge-m3` embeddings. (Terminal.)
5. **Semantic search** — meaning-based retrieval. Demo `wiki_search` with a German
   query (e.g. "Lautstärke einstellen") and read the top hit.
6. **RAG (ask)** — grounded, cited answers + graph-augmentation. Demo `wiki_ask`
   (e.g. "Was ist Smooth Sound Transitions?"); point out `*` cited vs `+` graph-expanded sources.
7. **Editing agent (chat)** — write-back tools, `--dry-run`, incremental graph
   updates on edits. (Terminal / describe.)
8. **Knowledge graph** — `graph-build`, Kuzu, node/edge types, the entity layer.
   Demos: `wiki_graph_neighbors` (hierarchy / references / similar / shared-entity),
   `wiki_find_entity` (pages mentioning a concept), `wiki_find_path` (how two pages connect).
9. **Web UI** — `serve`; the three center tabs + the Graph explorer (click to
   expand, double-click to collapse, edge-type filters, active-subgraph highlight).
10. **Complex discovery (capstone)** — combine it all on one investigation: pick a
    concept → `wiki_find_entity` → `wiki_graph_neighbors` on a top page →
    `wiki_find_path` to a second concept → `wiki_ask` to synthesize a cited answer.
11. **Your own wiki & deployment** — point `ingest`/`build-wiki`/`index`/`graph-build`
    at the user's own PDF (keep `--split-level` consistent), then `serve` it or wire
    the `mcp` server into a coding agent (see `docs/coding-agents.md`).

Begin now: greet the user, show the curriculum as a numbered menu, then start at the
requested module (or Module 0). Keep it interactive and encouraging.
