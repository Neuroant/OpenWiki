# 5. Building Block View

> arc42 §5 — The static decomposition into building blocks (modules/packages), top-down:
> Level 1 (overall system) → Level 2 (`graph/`, `web/`) → Level 3 (`GraphStore`, `RAGAgent`).
> **Status: complete.** `CLAUDE.md` remains the exhaustive per-module reference; this chapter
> is the structural view with the interfaces that matter at each boundary.

## 5.1 Whitebox — Overall System (Level 1)

### Decomposition rationale

The system is decomposed along **data-flow stages** (ingest → wiki → index → retrieve →
graph → deliver), and the decomposition is held stable by three boundaries:

- **The IR** (`models.ParsedDocument`) — every parser produces it; every downstream stage
  consumes only it. (ADR-1)
- **Backend protocols** (`Embedder`, `ChatModel`) — retrieval/agents depend on the protocol,
  not on Ollama. (ADR-2)
- **Additive graph layers** — optional tables (entities, communities, reinforcement) that
  store/agent code treats as best-effort. (ADR-7)

Only two modules touch heavy native deps: `pdf_parser.py` imports **`fitz`** (PyMuPDF);
`graph/builder.py` + `graph/store.py` import **`kuzu`**. Everything else is stdlib + NumPy,
which is what makes most of the system unit-testable offline (ADR / §8.9).

### Level-1 diagram

```mermaid
flowchart TB
  cli["cli.py\n(argparse subcommands, project resolution)"]

  subgraph ingest["Ingestion"]
    sources["sources.py\n(parse_source dispatch)"]
    pdf["pdf_parser.py\n(PyMuPDF)"]
    md["markdown_parser.py"]
    html["html_parser.py"]
    code["code_parser.py"]
    models["models.py\n(ParsedDocument IR)"]
    sources --> pdf & md & html & code --> models
  end

  subgraph wikigen["Wiki + retrieval"]
    wiki["wiki.py (WikiBuilder)"]
    chunking["chunking.py"]
    embeddings["embeddings.py\n(Embedder protocol)"]
    search["search.py (SemanticIndex)"]
    models --> wiki --> chunking --> search
    embeddings --> search
  end

  subgraph agents["Agents"]
    llm["llm.py (ChatModel)"]
    agent["agent.py (RAGAgent)"]
    tools["tools.py (WikiTools)"]
    chat_agent["chat_agent.py (WikiAgent)"]
    search --> agent
    llm --> agent & chat_agent
    tools --> chat_agent
  end

  subgraph graphpkg["graph/ (Kuzu layer)"]
    builder["builder.py (GraphBuilder)"]
    store["store.py (GraphStore)"]
    references["references.py"]
    entities["entities.py"]
    community["community.py"]
    decay["decay.py"]
    memory["memory.py (Path B capture)"]
    usage["usage.py (Path B usage log)"]
    builder --> store
  end

  subgraph delivery["Delivery"]
    web["web/ (WikiWebApp + SPA)"]
    mcp["mcp_server.py"]
    evalm["eval.py"]
  end

  cli --> ingest & wikigen & agents & graphpkg & delivery
  search --> builder
  store --> agent & tools & web & mcp & evalm
```

### Contained building blocks

`kind` = **pure** (stdlib/NumPy only, unit-testable without native deps) or **I/O** (touches
files, network/Ollama, or Kuzu).

**Ingestion**

| Block | kind | Responsibility & key interface |
|---|---|---|
| `models.py` | pure | The IR. `ParsedDocument` = `DocumentMetadata` + `OutlineItem[]` + `Page[]`. `to_dict()` / `from_dict()` / `to_markdown()`. |
| `sources.py` | I/O (lazy) | Dispatch: `parse_source(src, …) -> ParsedDocument`; `source_type`, `is_url`, `is_supported`, `source_exists`, `source_stem`, `SUPPORTED_SUFFIXES`. |
| `pdf_parser.py` | I/O (fitz) | `PDFParser.parse(path, max_pages) -> ParsedDocument`. **Only `fitz` importer.** |
| `markdown_parser.py` · `html_parser.py` · `code_parser.py` | pure | `*.parse()` → IR (headings/pages), stdlib only (`html.parser`, `os.walk`, urllib for URLs). |
| `merge.py` | pure | `combine_documents(docs, names) -> ParsedDocument` (multi-source corpus). |

**Wiki + retrieval**

| Block | kind | Responsibility & key interface |
|---|---|---|
| `wiki.py` | pure | `WikiBuilder(split_level).build(doc) -> Wiki`; `write_wiki(wiki, out_dir)`; `Wiki`/`WikiPage`, `slugify`. |
| `chunking.py` | pure | `chunk_wiki(wiki, size_words, overlap_words) -> list[Chunk]`; `chunk_text`, `normalize_text`. |
| `embeddings.py` | I/O (Ollama) | `Embedder` protocol (`embed_documents`, `embed_query`, `name`) + `OllamaEmbedder`; `get_embedder`. |
| `search.py` | I/O (Ollama via embedder) | `SemanticIndex.build/save/load`; `search(query, k) -> [SearchResult]`; `best_chunk_per_page(query, slugs)`. Normalized NumPy matrix, brute-force cosine. |

**Agents**

| Block | kind | Responsibility & key interface |
|---|---|---|
| `llm.py` | I/O (Ollama) | `ChatModel` protocol (`chat`, `chat_raw`, `name`) + `OllamaChat` (`/api/chat`, tool calls). |
| `agent.py` | I/O (via injected deps) | `RAGAgent(index, chat, top_k, graph, expand_k)`; `retrieve(q) -> [Source]`; `answer(q) -> RAGAnswer`. RAG + GraphRAG + memory reinforcement. |
| `tools.py` | I/O (files/graph) | `WikiTools`: `read_page`, `list_pages`, `search_wiki`, `edit_page`, `append_section`, `create_page`, `graph_neighbors`, `find_path`, `find_entity`, `schemas()`, `dispatch(name, args)`. |
| `chat_agent.py` | I/O (via tools/chat) | `WikiAgent(chat, tools).send(msg) -> AgentTurn`; `summarize_wiki(dir)`. Multi-turn tool loop. |

**Graph layer** (`graph/` — see §5.2)

**Delivery & support**

| Block | kind | Responsibility & key interface |
|---|---|---|
| `web/server.py` | I/O (http, Kuzu) | `WikiWebApp` (state + methods) + `make_handler(app)` + `serve(app, host, port)`. See §5.3. |
| `mcp_server.py` | I/O (stdio) | `build_server(wiki_dir, index, graph, agent) -> MCPStdioServer`; `.handle(msg)` is pure. |
| `eval.py` | pure (drivers inject I/O) | Metrics (`reciprocal_rank`, `hit_at_k`, `recall_at_k`, `grounding`, `community_grounding`, `judge_pairwise`, `task_success`) + drivers (`evaluate`, `make_retrievers`, `run_answer_eval`, `run_global_eval`, `run_cross_session_eval` — the Path B headline metric). |
| `project.py` | I/O (files) | `Project.load/find/resolve`; `out_dir`/`wiki_dir`/`index_dir`/`graph_path`; `setting(section, key)`; `render_manifest`. |
| `pipeline.py` | pure | `compute_fingerprints`, `stale_stages`, `BuildState` (incremental build state). |
| `userconfig.py` | I/O (files) | `UserConfig` + `Registry` under `~/.openwiki/`. |
| `cli.py` | I/O | argparse subcommands; wires stages; `_apply_project` settings precedence; `_DISPATCH`. |

### Key interfaces (the boundaries)

| Interface | Signature (essence) | Purpose |
|---|---|---|
| **IR** | `ParsedDocument.from_dict/to_dict/to_markdown` | Decouple parsers from downstream. |
| **Embedder** | `embed_documents(texts)->ndarray`, `embed_query(text)->ndarray` | Swap embedding backend. |
| **ChatModel** | `chat(messages)->str`, `chat_raw(messages, tools)->msg` | Swap chat backend; tool calling. |
| **GraphStore query API** | `neighborhood`, `explore`, `hybrid_search`, `find_path`, `communities`, … | The graph's read surface used by agent/UI/MCP/eval. |
| **WikiTools** | `dispatch(name, args)->str` + `schemas()` | The agent's action surface (advertised by availability). |
| **Web API / MCP** | HTTP+JSON / JSON-RPC over stdio | External delivery (see §3). |

## 5.2 Level 2 — the `graph/` subpackage

An additive Kuzu layer over the wiki + index. Only `builder`/`store` import `kuzu`;
`references`, `entities`, `community`, `decay`, `memory`, `usage` are **pure**
(unit-testable without a DB).

```mermaid
flowchart TB
  wiki2["Wiki"] --> builder2
  index2["SemanticIndex"] --> builder2
  refs2["references.py\n(cross-refs, pure)"] --> builder2
  ents2["entities.py\n(LLM entities, pure)"] --> builder2
  builder2["builder.py\nGraphBuilder.build()\n(preserves memory tier — B0)"] -->|writes| kuzudb[("Kuzu DB\n(single file)")]
  kuzudb --> store2["store.py\nGraphStore (read + writable)"]
  comm2["community.py\n(Louvain + summaries, pure)"] --> store2
  decay2["decay.py\n(decay math, pure)"] --> store2
  mem2["memory.py\n(session capture, pure)"] --> store2
  use2["usage.py\n(usage log, pure)"] --> store2
  store2 --> consumers["agent · tools · web · mcp · eval"]
```

| Block | kind | Key interface | Notes |
|---|---|---|---|
| `builder.py` | I/O (kuzu) | `GraphBuilder(db_path, similar_k).build(wiki, index, references, entities) -> stats` | Clean rebuild of the *derived* tier; mirrors embeddings into `Chunk`; creates all tables (some empty). **Snapshots + restores the remembered tier** across a rebuild (B0, ADR-16). |
| `store.py` | I/O (kuzu) | see §5.4 | Read-only by default; writable for edits/memory. |
| `references.py` | pure | `extract_references(doc, wiki)`, `extract_references_multi(doc, wiki, meta)`, `detect_page_offset(doc)` | Page + section/chapter cross-refs → `REFERENCES` edges. |
| `entities.py` | pure (injected chat) | `extract_entities(wiki, chat, types, …) -> [Entity]`; `coerce_types`; `DEFAULT_ENTITY_TYPES` | LLM per page + normalization; opt-in. |
| `community.py` | pure (injected chat) | `detect_communities(edges, nodes)`; `summarize_community(chat, members)`; `summarize_facts(chat, facts)` (B5 memory themes); `answer_global(chat, q, communities)`; `parse_summary` | Consolidation layer / global search (docs **and**, via B5, memory). |
| `decay.py` | pure | `effective_weight(w, last_seen, now, half_life)`; `reinforced_weight(w, boost, cap)` | Usage-memory math. |
| `memory.py` | pure (injected chat) | `capture_session(chat, transcript) -> [MemoryFact]`; `parse_facts`; `format_memory(recalled)` | Path B: session → subject–predicate–object facts + context formatting. |
| `usage.py` | pure | `usage_log_path(db)`; `append_usage(path, pairs)`; `read_usage`; `clear_usage` | Path B (B1): the append-only read-path usage-log sidecar. |

## 5.3 Level 2 — the `web/` subpackage

`web/server.py` holds application state + logic (`WikiWebApp`); a thin
`ThreadingHTTPServer` handler (`make_handler`) maps HTTP routes to `WikiWebApp` methods and
serves static files; `web/static/` is a no-build vanilla-JS SPA.

| Block | kind | Key interface |
|---|---|---|
| `WikiWebApp` | I/O (Kuzu/Ollama/files) | `manifest`, `get_page`, `search`, `chat`, `graph_explore`/`graph_expand`/`graph_neighborhood`, `project_info`, `communities`, `ask_global`, `run_eval`, `compare`, `health_stats`, `start_answer_eval`/`answer_eval_status`. |
| `make_handler(app)` / `serve(app, host, port)` | I/O (http) | JSON API + static file serving on `ThreadingHTTPServer`. |
| `web/static/{index.html, app.js, style.css, marked.min.js}` | — | SPA: 6 tabs (Projekt / Wiki / Graph / Evaluation / Tutorial / Hilfe); client-side Markdown; hand-rolled force-directed graph explorer. |

Concurrency: the threaded server shares **one** `GraphStore` connection, guarded by the
store's `RLock` (§8.6).

## 5.4 Level 3 — `GraphStore` (whitebox)

The graph's read/write surface. Opened read-only by default; writable (exclusive Kuzu lock)
for edits + memory. Responsibilities group as:

| Group | Methods |
|---|---|
| **Stats / health** | `stats()`, `health(hub_limit)`, `has_entities()`, `has_communities()` |
| **Neighborhood / paths** | `neighborhood(slug, similar_k)`, `find_path(a, b, max_hops)` |
| **Explorer (UI)** | `explore(slug)`, `expand(type, id)`, `expand_page`, `expand_entity` |
| **Entities** | `entities_for_page(slug)`, `pages_for_entity(query)` |
| **Communities** | `communities()`, `community_members()`, `page_graph()`, `page_snippet()`, `upsert_communities(assignment, summaries, labels)` |
| **Usage-memory** | `reinforce(from, to, now, boost)`, `decay(now, half_life, floor)`, `record_usage(pairs)` (writable → reinforce / read-only → log), `fold_usage(now)`, `pending_usage()` |
| **Remembered tier (Path B)** | `remember(session_id, facts, embedder)` (dedup + **supersede** contradictions), `recall(query, embedder, k, include_superseded)` (current-only by default), `has_memory()`, `forget_all()` |
| **Memory consolidation (Path B / B5)** | `assertion_graph(similar_k)` (similarity over current facts), `upsert_memory_concepts(assignment, summaries, labels)` (the "sleep" pass → `MemoryConcept` themes), `memory_concepts()`, `has_memory_concepts()` |
| **Context assembly (Path B / B6)** | `relevant_concepts(assertion_ids, limit)` (attractor themes for the activated facts), `context_for(query, embedder, identity, k, max_themes)` (the three-tier assembly → one context string, read-only + fail-soft) |
| **Incremental update** | `upsert_page(slug, text, …, embedder)` (MERGE page, replace chunks, recompute `SIMILAR_TO`) |
| **Hybrid retrieval** | `hybrid_search(vector, k)` (vector k-NN → owning page) |

`neighborhood()` returns a typed node/edge set including a `reinforced` group ranked by
time-decayed weight (§8, ADR-8). Optional-layer queries are best-effort (try/except → empty)
so the store works on graphs built before a layer existed.

## 5.5 Level 3 — `RAGAgent` (whitebox)

The retrieval→answer pipeline (`answer(question)` = `retrieve` + one chat call):

```mermaid
flowchart LR
  q["question"] --> s["index.search(top_k)\nseed Sources"]
  s --> g{"graph and expand_k>0?"}
  g -- no --> prompt
  g -- yes --> nb["graph.neighborhood(seeds)\ncandidates (_EXPAND_RELS)"]
  nb --> rr["index.best_chunk_per_page(q, candidates)\nrelated Sources"]
  rr --> rec["graph.record_usage(seed to related)\nwritable: reinforce now · read-only: log (B1)"]
  rec --> prompt["build_messages(grounded)\nchat.chat()"]
  prompt --> ans["RAGAnswer\n(answer + Sources + cited_markers)"]
```

- `_EXPAND_RELS = (references, referenced_by, similar, shared_entity, reinforced)` — the
  edge kinds GraphRAG expands along.
- Grounding is enforced by the system prompt; `Source` carries provenance so `[n]` citations
  resolve to pages. `record_usage` reinforces immediately on a writable graph (serve/chat) or
  appends to the usage log on a read-only `ask`/MCP (B1, ADR-17) — best-effort, never blocking retrieval.

---
*Chapter complete. Cross-refs: interfaces → §8 (concepts), decisions → §9, runtime flows →
§6. Path B **landed** its blocks: `graph/memory.py` + `graph/usage.py` (§5.2), the remembered-tier
+ usage-log methods on `GraphStore` (§5.4), and read-path `record_usage` in `RAGAgent` (§5.5) —
authoritative graph (B0/ADR-16), read-path reinforcement (B1/ADR-17), contradiction versioning (B4/ADR-18).*
