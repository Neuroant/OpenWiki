# 12. Glossary

> arc42 §12 — Domain and technical terms, so everyone means the same thing. **Status: complete.**

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
| **Remembered tier** | The authoritative Path B memory subgraph (`Session`/`Assertion` + `SUPERSEDES`, plus the `REINFORCES` overlay); additive, preserved across doc rebuilds (ADR-16); active only in Second Brain mode. |
| **Session / Assertion** | A captured "day" of experience (`Session`) and a reified subject·predicate·object fact under it (`Assertion`, with a mirrored embedding) — the memory data model (ADR-15). |
| **SUPERSEDES / supersession** | A newer `Assertion` supersedes an older one (same normalized subject+predicate, different object); "current" = no incoming `SUPERSEDES`; nothing deleted, so history stays queryable (ADR-18). |
| **Usage log** | The append-only `graph.usage.jsonl` sidecar a read-only `ask`/MCP writes to; the next writer folds it into `REINFORCES` edges — read-path reinforcement without the write lock (B1/ADR-17). |
| **Mode (Wiki / Second Brain)** | A per-project policy (`[memory] enabled`): Wiki = document tier only (default); Second Brain = document + remembered tiers (ADR-14). |
| **Path A / Path B** | A = the consolidation layer (communities / global search, done); B = agent-memory — **landed** the remembered tier (B0 authoritative graph, B1 read-path reinforcement, B4 contradiction versioning); B5 sleep consolidation + B6 full context-assembly next. |

## 12.4 Platform & tooling

| Term | Definition |
|---|---|
| **Ollama** | The local LLM/embedding server (`localhost:11434`) providing all inference. |
| **Kuzu** | The embedded graph + vector database (single file); pins the project to Python ≤3.13 on Windows. |
| **MCP** | Model Context Protocol — the stdio JSON-RPC interface exposing read-only `wiki_*` tools to coding agents. |
| **SPA** | The no-build vanilla-JS single-page app served by the stdlib web server. |
| **Fingerprint chain** | Per-stage input+param hashes in `.openwiki/state.json` enabling incremental builds. |
| **bge-m3 / qwen3** | Default embedding / chat models (multilingual, strong on the German corpora). |

## 12.5 Acronyms

| Acronym | Expansion |
|---|---|
| **ADR** | Architecture Decision Record (see §9) |
| **AGPL** | Affero General Public License (PyMuPDF's license; §2 LC1) |
| **ANN** | Approximate Nearest Neighbor (search — a scale option, §11 D3) |
| **arc42** | The architecture-documentation template these docs follow |
| **HNSW** | Hierarchical Navigable Small World (Kuzu's vector index; mirrored, not used for retrieval) |
| **HTTP / JSON** | HyperText Transfer Protocol / JavaScript Object Notation (the web API) |
| **IR** | Intermediate Representation (`ParsedDocument`) |
| **JSON-RPC** | JSON Remote Procedure Call (the MCP wire protocol, over stdio) |
| **KB** | Knowledge Base (a built wiki + index + graph) |
| **MCP** | Model Context Protocol (coding-agent tool interface) |
| **MoE** | Mixture of Experts (the default chat model's architecture) |
| **RAG / GraphRAG** | Retrieval-Augmented Generation / its graph-expanded variant |
| **SPA** | Single-Page Application (the no-build browser UI) |
| **TOML** | Tom's Obvious Minimal Language (`openwiki.toml`, config files) |

---
*Chapter complete. Path B terms have landed: Session/Assertion (ADR-15), SUPERSEDES/supersession
(ADR-18), the remembered tier + Wiki/Second-Brain mode (ADR-14/16), and the usage log (ADR-17).*
