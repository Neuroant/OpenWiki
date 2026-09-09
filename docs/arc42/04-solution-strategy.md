# 4. Solution Strategy

> arc42 §4 — The fundamental decisions and approaches that shape the architecture, and how
> they serve the quality goals. **Status: complete.**

A short summary; each strategy's full reasoning (context, alternatives, consequences) is the
ADR referenced in the last column — see [Architecture Decisions](09-architecture-decisions.md).

| Strategy | What it means | Serves | ADR |
|---|---|---|---|
| **IR-centric staged pipeline** | A single intermediate representation (`ParsedDocument` in `models.py`) sits between ingestion and everything downstream. Each stage is a pure-ish transform reading the previous stage's artifact. | Q4, Q3 | ADR-1 |
| **Local-first via Ollama + protocols** | All inference runs on a local Ollama; access is behind the `Embedder`/`ChatModel` protocols so backends are swappable and the corpus never leaves the machine. | Q1, Q4 | ADR-2 |
| **Stdlib-leaning, deps isolated** | Web server, MCP server, and 3 of 4 source parsers are stdlib-only; PyMuPDF and Kuzu are confined to single modules and imported lazily. | Q2 | ADR-4, ADR-5 |
| **Additive knowledge graph (a mirror)** | The Kuzu graph is layered *over* the wiki + index without mutating them; embeddings are mirrored in so traversal + vector search share one query. The index stays authoritative. | Q4 | ADR-3 |
| **Optional layers as empty-by-default tables** | Entities, communities, and reinforcement edges are always-created (possibly empty) tables, so all store/agent code works with or without them. | Q4 | ADR-7 |
| **Read-mostly graph, writable for edits** | Read-only by default (concurrent readers); writable (exclusive) only for `serve`/`chat` edits + usage-memory. On the read path, usage is logged and folded in by a writer, so reads reinforce without the lock. | correctness | ADR-8, ADR-17 |
| **Coexisting document + remembered tiers (Path B)** | In Second Brain mode the graph adds an *authoritative* remembered tier — sessions → reified `Assertion`s, newer facts superseding older — preserved across document rebuilds. Wiki Mode = memory off. | Q4 | ADR-14/15/16/18 |
| **Borrow GraphRAG ideas, not the library** | Community detection + summaries + global search reimplemented natively/locally. | Q2, Q5 | ADR-6 |
| **Project manifest + settings precedence** | `openwiki.toml` groups a KB; unset settings resolve `flag > manifest > ~/.openwiki config > built-in default`. | usability | ADR-10 |
| **Incremental, fingerprinted builds** | Per-stage input+param fingerprints skip unchanged stages. | performance | ADR-11 |
| **Evaluation-driven design** | A backend-agnostic eval harness turns "is the graph worth it?" into measured findings (RAG vs GraphRAG vs Global). | Q5 | ADR-9 |
| **Grounded agents** | RAG/editing agents answer only from provided excerpts and cite provenance; the graph adds context, never ungrounded claims. | correctness, trust | §8.4 |

## 4.1 How the strategy maps to the top quality goals

```mermaid
flowchart LR
  Q1["Q1 Local-first"] --- S_local["Ollama + protocols"]
  Q2["Q2 Minimal deps"] --- S_std["stdlib server/MCP; deps isolated"]
  Q3["Q3 Testability"] --- S_pure["pure logic + fakes"]
  Q4["Q4 Modularity"] --- S_ir["IR + dispatch + additive layers"]
  Q5["Q5 Measurability"] --- S_eval["eval harness"]
```

## 4.2 Architecture at a glance

```
source ──parse_source──▶ ParsedDocument (IR) ──▶ JSON / Markdown
                               │
                        WikiBuilder ──▶ Wiki ──▶ output/wiki/
                               │
                   chunk_wiki + Embedder ──▶ SemanticIndex ──▶ output/index/
                               │
                RAGAgent + ChatModel ──▶ cited answer      (RAG / GraphRAG)
                               │
              WikiAgent + WikiTools ⇄ ChatModel ──▶ edits pages/*.md
                               │
                    GraphBuilder ──▶ Kuzu graph ──▶ GraphStore
                               │     (+ communities, + REINFORCES memory)
       capture_session + GraphStore.remember ──▶ remembered tier   (Path B, Second Brain)
                               │     (Session/Assertion + SUPERSEDES; recall)
                    WikiWebApp (http.server) ──▶ browser SPA
                    MCPStdioServer ──▶ coding agents
```

---
*Chapter complete. Each strategy row links to its ADR in §9 (full context/alternatives/
consequences there); the strategy→quality-goal mapping refines the goals from §1.2.*
