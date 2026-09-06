# 12. Glossary

> arc42 §12 — Domain and technical terms, so everyone means the same thing. **Status: draft.**

## 12.1 Domain terms

| Term | Definition |
|---|---|
| **Agentic wiki** | A knowledge base a pipeline builds from sources and that both humans and AI agents can query, explore, and edit. |
| **IR (Intermediate Representation)** | `ParsedDocument`: the format-neutral model (metadata + outline + pages) every source parses into and every downstream stage reads. |
| **Outline** | The document's TOC tree (`OutlineItem`, with a `level`); the wiki's page hierarchy derives from it. |
| **Wiki / WikiPage** | The split output: a tree of linked Markdown pages (`index.md`, `pages/*.md`, `wiki.json`). |
| **Chunk** | An overlapping word-window slice of a page's clean text, carrying provenance (page slug, PDF pages) — the unit of retrieval. |
| **Slug** | A filesystem-safe page id (e.g. `037-datenstrukturen`); the join key across wiki/index/graph. |
| **Project** | A folder with `openwiki.toml` grouping sources + built artifacts + settings so state persists. |
| **Community** | A cluster of topically related pages (Louvain over the page graph) with an LLM summary; the unit of **global search**. |

## 12.2 Retrieval & agents

| Term | Definition |
|---|---|
| **RAG** | Retrieval-Augmented Generation: retrieve top chunks → grounded prompt → cited answer. |
| **GraphRAG** | RAG plus graph expansion: seed pages' graph neighbors are pulled in and re-ranked by the query. |
| **Global search** | Answering a whole-corpus/thematic question from the community summaries (not chunk retrieval). |
| **Grounding** | The constraint (and metric) that answers use/cite only the provided excerpts. |
| **Provenance** | The source trail carried by chunks/answers (page slug, PDF page range) enabling citations. |
| **Embedder / ChatModel** | The pluggable backend protocols (implemented by `OllamaEmbedder` / `OllamaChat`). |

## 12.3 Graph & memory

| Term | Definition |
|---|---|
| **GraphStore / GraphBuilder** | The Kuzu read (query) and write (build) sides of the graph layer. |
| **SIMILAR_TO / REFERENCES / MENTIONS** | Vector-similarity, cross-reference, and page→entity edges. |
| **Entity layer** | Opt-in LLM-extracted typed entities (`Entity` + `MENTIONS`), normalized to merge surface variants. |
| **REINFORCES** | A usage-memory edge (weight + last_seen) strengthened when a connection is used and decayed over time. |
| **Reinforcement / decay** | Hebbian "strengthen on use" (`reinforce`) and time-based "forget" (`decay`, exponential half-life). |
| **Path A / Path B** | A = the consolidation layer (communities/global search, done); B = agent-memory (usage-memory started; sessions/contradiction next). |

## 12.4 Platform & tooling

| Term | Definition |
|---|---|
| **Ollama** | The local LLM/embedding server (`localhost:11434`) providing all inference. |
| **Kuzu** | The embedded graph + vector database (single file); pins the project to Python ≤3.13 on Windows. |
| **MCP** | Model Context Protocol — the stdio JSON-RPC interface exposing read-only `wiki_*` tools to coding agents. |
| **SPA** | The no-build vanilla-JS single-page app served by the stdlib web server. |
| **Fingerprint chain** | Per-stage input+param hashes in `.openwiki/state.json` enabling incremental builds. |
| **bge-m3 / qwen3** | Default embedding / chat models (multilingual, strong on the German corpora). |

---
TODO (completion steps): keep in sync as Path B lands new terms (session, sub-graph merge,
contradiction/versioned edge); add acronym expansions (HNSW, ANN, ADR).
