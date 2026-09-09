# 1. Introduction and Goals

> arc42 §1 — What OpenWiki does, its top quality goals, its stakeholders, and its non-goals.
> **Status: complete.**

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
10. **Agent memory (Path B)** — an authoritative *remembered tier* (`Session`/`Assertion`) captured
    from session transcripts and recalled across sessions, with newer facts **superseding** older
    ones; gated by a per-project **Wiki vs Second Brain mode** (`[memory] enabled`).

Stages 1–9 run in **Wiki Mode** (documents only, the default); stage 10 adds the remembered tier in
**Second Brain Mode** — the two coexist as tiers of one substrate (§8.15, ADR-14).

These stages run inside a **project** — an `openwiki.toml` folder that is the top-level unit
grouping sources, artifacts, settings, and build state, so several knowledge bases coexist and
persist between commands (the key organizing concept — §8.14; deep design in `docs/projects.md`).

Core use cases (each maps to a runtime scenario in §6):

| # | Use case | Entry point | Runtime scenario |
|---|---|---|---|
| U1 | Build a knowledge base from one or more sources | `openwiki build` | §6.1 |
| U2 | Ask a grounded question with citations (RAG / GraphRAG) | `ask` | §6.2 |
| U3 | Ask a thematic, whole-corpus question | `ask --global` | §6.3 |
| U4 | Explore & edit the wiki in a browser | `serve` | §6.4 |
| U5 | Consult the wiki from a coding agent | MCP (`wiki_*`) | §6.2/§6.3 via MCP |
| U6 | Measure retrieval / answer / global quality | `eval [--answers/--global]` | §6 + `docs/RAG-vs-GraphRAG.md` |
| U7 | Remember a session & recall it in the next (agent memory) | `remember` / `recall` | §6.7 |

## 1.2 Quality Goals

The top architectural quality goals, in priority order (they drive most decisions in §9):

| # | Quality goal | Motivation / concrete meaning |
|---|---|---|
| Q1 | **Local-first / privacy** | No cloud APIs or keys; all inference on a local Ollama; data stays on disk. |
| Q2 | **Minimal dependencies** | Stdlib-leaning; heavy libs (PyMuPDF, Kuzu) isolated behind boundaries; the web server + MCP server + most parsers are stdlib-only. |
| Q3 | **Testability (offline)** | The whole suite runs offline with fakes for Ollama/Kuzu; pure logic separated from I/O. |
| Q4 | **Modularity / extensibility** | New source parsers, backends, and capabilities slot in behind stable boundaries (the IR, the `Embedder`/`ChatModel` protocols, `sources.parse_source`, CLI subcommands). |
| Q5 | **Measurability** | Design claims (esp. "is the graph worth it?") are backed by a reproducible eval harness, not opinion. |

See §10 for the quality tree + concrete quality scenarios.

## 1.3 Stakeholders

| Role | Expectations of the architecture |
|---|---|
| **Learner / maintainer** (primary) | Small, readable, well-boundaried code; each stage understandable in isolation; fast offline tests. |
| **End user** (CLI / browser) | Build a KB from their docs and get grounded answers + exploration, fully offline. |
| **AI coding agent** (via MCP) | Read-only, grounded access to the wiki as MCP tools (`wiki_ask`, `wiki_global`, …). |
| **Evaluator** | Reproduce the RAG-vs-GraphRAG-vs-Global findings on their own corpus. |
| **Future contributor** | Clear extension points and decision records (§9) to build on — e.g. Path B agent memory. |

## 1.4 Non-goals

OpenWiki deliberately does **not** aim to be (see §3.3 for the scope boundary and §11 for the
matching risks):

- a **hosted / multi-tenant service** — it targets one machine, one user; there is no auth;
- a **general-purpose vector database** — retrieval is brute-force over a small corpus (ADR-3);
- an **authentication / authorization** system — `serve` and MCP assume a trusted local host;
- a **source-document editor** — it edits the *derived* wiki, not the originals;
- a **cloud-AI application** — all inference is local by design (ADR-2).

---
*Chapter complete. The quality goals here are refined into the quality tree + scenarios in §10;
the decisions that realize them are recorded in §9.*
