# 11. Risks and Technical Debt

> arc42 §11 — Known risks and technical debt, stated honestly. **Status: draft.**
> Cross-references the candid "open topics" in `docs/roadmap.md`.

## 11.1 Risks

| # | Risk | Impact | Likelihood | Mitigation / notes |
|---|---|---|---|---|
| R1 | **No authentication** on `serve` and MCP | High if exposed | Low (localhost default) | Assume trusted local host; **do not expose** without adding auth first. Documented in §3 scope + §7. |
| R2 | **Local model quality/latency** bounds answer quality | Medium | Medium | Grounding limits hallucination; models are swappable via ADR-2. |
| R3 | **Brute-force O(n) retrieval** won't scale | Medium | Medium (grows with corpus) | Fine at current scale (~thousands of chunks); Kuzu HNSW exists but retrieval doesn't use it yet (debt D3). |
| R4 | **Entity extraction is slow + non-deterministic** | Medium | Medium | ~1 LLM call/page; mitigated by greedy+seed determinism, output bounding, retry-on-empty — still opt-in and slow. |
| R5 | **Findings rest on small N / one corpus / one embedder** | Medium (generalization) | — | Two aligned signals (objective + judge); flagged as a caveat in `RAG-vs-GraphRAG.md`. |
| R6 | **Windows-primary, no CI** | Medium | Medium | Cross-platform is intended but unverified; no automated cross-OS test run. |

## 11.2 Technical Debt

| # | Debt | Why it exists | Cost to address |
|---|---|---|---|
| D1 | **Graph is a mirror, not authoritative** | Deliberate (ADR-3) for a document wiki | High — inverting it is the Path-B "agent memory" pivot (sessions/experiences in). |
| D2 | **Reinforcement only fires in writable contexts** | Kuzu exclusive-lock model (ADR-8) | Medium — needs a writable-safe concurrency model so plain `ask` can reinforce. |
| D3 | **Retrieval ignores Kuzu's HNSW; brute-force cosine** | Simplicity at small scale | Medium — back `SemanticIndex.search` with an ANN index when corpora grow. |
| D4 | **Incremental upsert recomputes only `SIMILAR_TO`** | Cheap live sync for edits | Medium — CHILD_OF/NEXT/REFERENCES/entities still need a full `graph-build`. |
| D5 | **Community precision is moderate / labels heuristic-then-LLM** | Broad thematic questions span themes | Low — acceptable; measured (precision 56.7% on the thematic set). |
| D6 | **No contradiction handling / time-versioning of facts** | Out of scope for a static wiki | High — the novel Path-B piece (supersede older facts). |
| D7 | **Minimal observability** (stderr logs only) | Local single-user tool | Low–Medium — add structured logging/metrics if it grows. |
| D8 | **Packaging is Windows/pipx-only** | Primary platform | Low — add PyPI/Docker/CI for portability. |

## 11.3 Debt that is *not* present (by design)

- No dual-write consistency problem (ADR-3: one authority).
- No feature-flag sprawl for optional layers (ADR-7: empty tables).
- No hidden cloud dependency or key management (ADR-2).

---
TODO (completion steps): assign owners/priorities; link each debt item to the roadmap
direction that addresses it; add a "risk we accept vs. risk we track" split.
