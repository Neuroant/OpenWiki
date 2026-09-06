# 6. Runtime View

> arc42 §6 — How the building blocks collaborate at runtime, via a few key scenarios.
> **Status: draft** (scenarios sketched; sequence detail to be completed).

## 6.1 Scenario: Build a knowledge base (`openwiki build`)

Incremental: a per-stage fingerprint chain (`pipeline.py`) skips stages whose inputs+params
are unchanged.

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
      A->>GS: reinforce(seed → related)   %% usage memory
    end
  end
  A->>C: chat(grounded prompt + numbered excerpts)
  C-->>A: answer with [n] citations
  A-->>U: RAGAnswer (answer + Sources)
```

Key point: the chat model is instructed to answer **only** from the excerpts; every excerpt
carries provenance (page slug, PDF pages), so citations are traceable.

## 6.3 Scenario: Ask a thematic question (`ask --global` / `wiki_global`)

Chunk-RAG can't answer whole-corpus questions. Global search instead synthesizes over the
**community summaries**: `GraphStore.communities()` → `community.answer_global(chat, question,
summaries)` → an answer citing communities `[n]`. Requires `openwiki communities` to have run.

## 6.4 Scenario: Chat + edit a page (`chat` / `serve`)

`WikiAgent` runs a multi-turn tool loop: model → tool calls (`search_wiki`, `read_page`,
`edit_page`, …) → results → model. On a successful write, `WikiTools` writes `pages/*.md`
and, with a writable graph + embedder, calls `GraphStore.upsert_page` (re-chunk, refresh
embeddings, recompute `SIMILAR_TO`) so the graph stays in sync live.

## 6.5 Scenario: Consolidate + decay (maintenance)

- `openwiki communities`: read the page graph → Louvain → one LLM summary per community →
  write `Community` + `IN_COMMUNITY` (the "sleep pass").
- `openwiki decay`: age every `REINFORCES` edge to now (persist decayed weight) and prune
  those below the floor (forgetting).

---
TODO (completion steps): add sequence diagrams for §6.3–§6.5; add an error/timeout path
(Ollama unreachable) and the read-only-graph fallback; note threading (the web server shares
one RLock-guarded Kuzu connection).
