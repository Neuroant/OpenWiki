# 6. Runtime View

> arc42 §6 — How the building blocks collaborate at runtime, via the key scenarios, plus the
> cross-cutting runtime aspects (errors, fallback, concurrency). **Status: complete.**

Scenarios map to the use cases in §1 and the blocks in §5.

## 6.1 Scenario: Build a knowledge base (`openwiki build`)

Incremental — a per-stage fingerprint chain (`pipeline.py`) skips stages whose inputs+params
are unchanged; `--force` / `--only STAGES` override.

```mermaid
sequenceDiagram
  participant U as CLI user
  participant CLI as cli._cmd_build
  participant P as pipeline (fingerprints)
  participant S as sources.parse_source
  participant W as WikiBuilder
  participant IX as SemanticIndex
  participant E as OllamaEmbedder
  participant G as GraphBuilder

  U->>CLI: openwiki build
  CLI->>P: stale_stages(state, fingerprints)
  loop each stale stage
    CLI->>S: parse_source(source) → ParsedDocument
    CLI->>W: build(doc) → Wiki → write_wiki
    CLI->>IX: build(wiki, embedder)
    IX->>E: embed_documents(chunks)  (HTTP → Ollama)
    CLI->>G: build(wiki, index [, refs, entities]) → Kuzu DB
  end
  CLI->>P: write .openwiki/state.json
```

## 6.2 Scenario: Ask a grounded question (`ask`, RAG / GraphRAG)

```mermaid
sequenceDiagram
  participant U as user
  participant A as RAGAgent
  participant IX as SemanticIndex
  participant GS as GraphStore
  participant C as OllamaChat

  U->>A: answer(question)
  A->>IX: search(question, top_k) → seed chunks
  opt graph present (GraphRAG)
    A->>GS: neighborhood(seed slugs) → candidates
    A->>IX: best_chunk_per_page(question, candidates) → related
    opt graph writable (serve/chat)
      A->>GS: reinforce(seed → related)  [usage memory]
    end
  end
  A->>C: chat(grounded prompt + numbered excerpts)
  C-->>A: answer with [n] citations
  A-->>U: RAGAnswer (answer + Sources)
```

The chat model is instructed to answer **only** from the excerpts; every excerpt carries
provenance (page slug, PDF pages), so `[n]` citations are traceable (`cited_markers()`).

## 6.3 Scenario: Ask a thematic question (`ask --global` / `wiki_global`)

Chunk-RAG can't answer whole-corpus questions; global search synthesizes over the community
summaries instead. Same flow behind the CLI, the web box, and the MCP tool.

```mermaid
sequenceDiagram
  participant U as caller (CLI / web / MCP)
  participant GS as GraphStore
  participant CM as community.answer_global
  participant C as OllamaChat

  U->>GS: communities()  (label + summary, size-desc)
  Note over U: guard: no communities → "run openwiki communities first"
  U->>CM: answer_global(chat, question, [(label, summary), …])
  CM->>C: chat(GLOBAL_SYSTEM + summaries + question)
  C-->>CM: answer citing communities [n]
  CM-->>U: answer  (caller parses [n] → cited communities)
```

## 6.4 Scenario: Chat + edit a page, with live graph sync (`chat` / `serve`)

`WikiAgent` runs a bounded tool loop; a successful write is reflected in the graph
incrementally when the graph is writable and an embedder is present.

```mermaid
sequenceDiagram
  participant U as user
  participant WA as WikiAgent
  participant C as OllamaChat
  participant WT as WikiTools
  participant FS as pages/*.md
  participant GS as GraphStore

  U->>WA: send(message)
  loop until a plain reply (max_iterations = 6)
    WA->>C: chat_raw(messages, tools = schemas())
    C-->>WA: assistant message
    alt has tool_calls
      loop each tool call
        WA->>WT: dispatch(name, args)
        opt write tool (edit_page / append_section / create_page)
          WT->>FS: write_text(content)
          opt embedder present and graph writable
            WT->>GS: upsert_page(slug, content, embedder)
            Note over WT,GS: re-chunk, refresh embeddings,<br>recompute SIMILAR_TO (a graph hiccup never fails the edit)
          end
        end
        WT-->>WA: result string
      end
    else plain reply
      WA-->>U: AgentTurn(reply, tool_calls)
    end
  end
```

`--dry-run` makes writes preview-only (no file write, no graph sync). Read-only tools
(`search_wiki`, `read_page`, `graph_neighbors`, `find_path`, `find_entity`) are advertised
only when their backing artifact (index / graph / entities) is present.

## 6.5 Scenario: Consolidate communities (`openwiki communities`)

The "sleep pass" — re-runnable over the built graph.

```mermaid
sequenceDiagram
  participant U as CLI (owiki communities)
  participant GS as GraphStore (writable)
  participant CD as community.detect_communities
  participant C as OllamaChat

  U->>GS: page_graph()  (SIMILAR_TO ∪ REFERENCES ∪ shared-entity)
  U->>CD: detect_communities(edges)  (weighted Louvain)
  CD-->>U: {slug: community_id}
  loop each community
    U->>C: summarize_community(members)  (one call)
    C-->>U: (label, summary)
  end
  U->>GS: upsert_communities(assignment, summaries, labels)
```

## 6.6 Scenario: Decay the usage-memory (`openwiki decay`)

No model calls — pure maintenance: open the graph writable, read every `REINFORCES` edge,
recompute its `effective_weight(weight, last_seen, now, half_life)`, then **persist** the
decayed weight (reset `last_seen = now`) or **delete** the edge if it fell below the floor.
Returns `{edges, decayed, pruned}`.

## 6.7 Cross-cutting runtime aspects

### Error / timeout handling (Ollama unreachable)
Any embed or chat call goes through `urllib` to Ollama; on failure `OllamaEmbedder` /
`OllamaChat` raise a `RuntimeError` with a clear hint. It surfaces per entry point:

| Entry point | Behaviour on Ollama failure |
|---|---|
| CLI (`ask`, `build`, `communities`, `eval …`) | Error printed to stderr; non-zero exit. |
| Web API | `RuntimeError` → HTTP **503** (`do_POST`/`do_GET` handler). |
| Editing agent | `WikiTools.dispatch` catches per-tool exceptions → an `ERROR: …` string the model can react to; the loop stays alive. |

Note: even plain RAG retrieval needs the embedder (to embed the *query*), so a down Ollama
fails retrieval, not just generation.

### Read-only-graph fallback
`serve`/`chat` request the graph **writable** (exclusive Kuzu lock) to enable edits +
reinforcement. If the lock is already held, `_open_graph` falls back to **read-only** (a note
is printed); reinforcement and `upsert_page` then become guarded no-ops (`writable=False`),
so reads still work — the wiki simply doesn't self-update in that process.

### Concurrency
- The web layer is a `ThreadingHTTPServer`: requests run on separate threads that share **one**
  `GraphStore` connection, serialized by the store's re-entrant `RLock` (an `upsert` holds it
  across a batch).
- The stateful `WikiAgent` (mutable history) is serialized by a lock in `WikiWebApp`.
- Writable graph access is process-exclusive → at most one writable `serve`/`chat` at a time,
  with the read-only fallback above.

---
*Chapter complete. Cross-refs: block interfaces → §5; grounding/provenance, concurrency,
error handling as concepts → §8; the read-only-write trade-off → ADR-8 (§9).*
