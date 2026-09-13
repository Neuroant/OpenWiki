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
    Note over G: rebuild preserves the remembered tier (B0/ADR-16)
    opt memory stage (Second Brain mode, session sources)
      CLI->>G: capture_session then remember(facts) as Assertions (supersede contradictions)
    end
  end
  CLI->>P: write .openwiki/state.json
```

The pipeline stages are **ingest → wiki → index → graph → memory**; the `memory` stage runs only
when `[memory] enabled` and `type = "session"` sources are declared, and stands *off* the document
fingerprint chain (a doc rebuild preserves memory, so it needn't re-capture).

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
    A->>GS: record_usage(seed, related)
    Note over A,GS: read-only default (serve/chat/ask/MCP) appends usage to the journal for a later writer to fold in (B1/ADR-19)
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

No model calls — pure maintenance: open the graph writable and first **fold in** any pending
read-path usage (`fold_usage` → `reinforce` each logged pair, then clear the log — B1/ADR-17), then
read every `REINFORCES` edge, recompute its `effective_weight(weight, last_seen, now, half_life)`,
and **persist** the decayed weight (reset `last_seen = now`) or **delete** the edge if it fell below
the floor. Returns `{edges, decayed, pruned}` (plus the folded-in count).

## 6.7 Scenario: Remember & recall a session (Path B)

The remembered tier's write→read loop. `remember` captures a transcript into facts and merges them
(dedup + **contradiction supersession**); `recall` returns the *current* facts most relevant to a
query in a later session. Both require Second Brain mode (`[memory] enabled`, ADR-14).

```mermaid
sequenceDiagram
  participant U as CLI (remember / recall)
  participant M as memory.capture_session
  participant C as OllamaChat
  participant GS as GraphStore
  participant E as OllamaEmbedder

  Note over U,GS: remember (writable graph)
  U->>M: capture_session(chat, transcript)
  M->>C: chat(CAPTURE_SYSTEM, transcript)
  C-->>M: JSON facts as MemoryFacts
  U->>GS: remember(session_id, facts, embedder)
  GS->>E: embed_documents(fact texts)
  Note over GS: dedup vs current, then a new fact with the same subject and predicate but a different object supersedes the old one (SUPERSEDES, B4)

  Note over U,GS: recall (read-only graph, a later session)
  U->>GS: recall(query, embedder, k)
  GS->>E: embed_query(query)
  GS-->>U: current facts by decay-weighted cosine (superseded hidden by default)
```

A doc rebuild preserves these assertions + their `SUPERSEDES` edges (B0/ADR-16), so memory survives
re-ingesting sources. The cross-session eval (`eval --cross-session`) measures whether this assembled
memory beats a cold start and a raw-log paste (`docs/path-b-memory.md` §7).

## 6.8 Cross-cutting runtime aspects

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

### Concurrency & the write-ahead journal (B1 / ADR-19)
Kuzu 0.11 is **reader-XOR-writer** (measured): a writable connection blocks all readers, and readers
block a writer — no simultaneous read+write. So `serve`/`chat` open the graph **read-only by default**,
which lets many readers (`ask`/MCP/`recall`/`context`, a second `serve`) run **concurrently** while
serving. Writes never take the lock on these paths: reinforcement **appends** to `graph.usage.jsonl`
(B1/ADR-17), and `remember` / host-`capture` / a chat-edit's graph re-sync **queue** to
`graph.journal.jsonl` as `remember`/`reindex` ops. A writer **folds** both (`fold_usage` +
`fold_journal`) at `serve`/`chat` start+shutdown (`_transient_fold`), in `openwiki decay`, or on the
next `remember`; writable opens use **retry-with-backoff** for transient contention. A chat-edit still
writes its page file live — only the *graph* re-sync is deferred. `--sync` opts `serve`/`chat` back into
a held-**writable** connection (live edit-sync + immediate reinforcement, exclusive lock — blocks other
graph access); if that writable open is refused, `_open_graph` falls back to read-only (a note is printed).

- The web layer is a `ThreadingHTTPServer`: requests run on separate threads that share **one**
  `GraphStore` connection, serialized by the store's re-entrant `RLock` (an `upsert` holds it
  across a batch).
- The stateful `WikiAgent` (mutable history) is serialized by a lock in `WikiWebApp`.
- Writable graph access is process-exclusive → at most one `--sync` (or `remember`/`decay`) writer at a
  time; the read-only default + journal is how concurrent access is achieved.

---
*Chapter complete. Cross-refs: block interfaces → §5; grounding/provenance, concurrency,
error handling as concepts → §8; the read-only-write trade-off → ADR-8 (§9).*
