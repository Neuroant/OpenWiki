# 1. Introduction and Goals

> arc42 §1 — What OpenWiki does, its top quality goals, and its stakeholders.
> **Status: draft.**

## 1.1 Requirements Overview

**OpenWiki** is a learning project for building **agentic wikis** — pipelines that turn
source documents into structured, machine-navigable knowledge bases, then let both humans
and AI agents query, explore, and edit them. It runs **fully locally** (a local Ollama
server for embeddings + chat; an embedded Kuzu graph DB; otherwise stdlib-leaning).

The system is a straight, staged pipeline built around an intermediate representation (IR):

1. **Ingestion** — parse a source (PDF, Markdown/text, HTML/URL, or a code repo) into a
   structured `ParsedDocument` (IR).
2. **Wiki generation** — split the IR along its outline into a tree of linked wiki pages.
3. **Semantic search** — chunk the pages, embed them (bge-m3 via Ollama), query by meaning.
4. **RAG agent** — retrieve top chunks and answer a question with citations back to pages.
5. **Editing agent** — a multi-turn, tool-using agent that searches, reads, and edits pages.
6. **Web UI** — a zero-dependency browser SPA to browse, search, chat/edit, and explore.
7. **Knowledge graph** — an additive Kuzu layer (pages/chunks/entities + structural,
   similarity, reference, and usage-memory edges) with GraphRAG and a graph explorer.
8. **Consolidation layer** — LLM community summaries over the graph → **global search**.
9. **Usage-memory** — decaying `REINFORCES` edges that learn which connections are used.

Core use cases (see [Runtime View](06-runtime-view.md) for detail):

- **U1 Build a knowledge base** from one or more sources (`openwiki build`).
- **U2 Ask a grounded question** with citations (`ask`, RAG / GraphRAG).
- **U3 Ask a thematic question** across the whole corpus (`ask --global`).
- **U4 Explore & edit** the wiki in a browser (`serve`).
- **U5 Consult the wiki from a coding agent** via MCP tools.
- **U6 Measure retrieval/answer quality** (`eval`, RAG vs GraphRAG vs Global).

## 1.2 Quality Goals

The top architectural quality goals, in priority order (drives most decisions):

| # | Quality goal | Motivation / concrete meaning |
|---|---|---|
| Q1 | **Local-first / privacy** | No cloud APIs or keys; all inference on a local Ollama; data stays on disk. |
| Q2 | **Minimal dependencies** | Stdlib-leaning; heavy libs (PyMuPDF, Kuzu) isolated behind boundaries; the web server + MCP server + most parsers are stdlib-only. |
| Q3 | **Testability (offline)** | The whole suite runs offline with fakes for Ollama/Kuzu; pure logic separated from I/O. |
| Q4 | **Modularity / extensibility** | New source parsers, backends, and capabilities slot in behind stable boundaries (the IR, the `Embedder`/`ChatModel` protocols, `sources.parse_source`, CLI subcommands). |
| Q5 | **Measurability** | Design claims (esp. "is the graph worth it?") are backed by a reproducible eval harness, not opinion. |

See [Quality Requirements](10-quality-requirements.md) for the quality tree + scenarios.

## 1.3 Stakeholders

| Role | Expectations of the architecture |
|---|---|
| **Learner / maintainer** (primary) | Small, readable, well-boundaried code; each stage understandable in isolation; fast offline tests. |
| **End user** (CLI / browser) | Build a KB from their docs and get grounded answers + exploration, fully offline. |
| **AI coding agent** (via MCP) | Read-only, grounded access to the wiki as MCP tools (`wiki_ask`, `wiki_global`, …). |
| **Evaluator** | Reproduce the RAG-vs-GraphRAG-vs-Global findings on their own corpus. |
| **Future contributor** | Clear extension points and decision records to build on (e.g. Path B agent memory). |

---
TODO (completion steps): tighten U-case list to trace into §6 scenarios; add an explicit
"non-goals" subsection (not a hosted/multi-tenant service; not a general vector DB); confirm
stakeholder list with the project owner.
