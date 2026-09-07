# 11. Risks and Technical Debt

> arc42 §11 — Known risks and technical debt, stated honestly. **Status: complete.**
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
| R7 | **Licensing before redistribution** | High if redistributed | Low (not yet redistributed) | PyMuPDF is **AGPL-3.0** and there is **no project LICENSE** yet (§2 LC1/LC3). Must pick an AGPL-compatible license — or swap to a non-AGPL PDF backend behind the parser boundary — before any release. |

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

## 11.4 Risk posture — accept vs. track

For a single-user, local, learning project the sensible posture is:

- **Accepted (by design, given the local-first scope):** R1 (no auth — mitigated by
  localhost-only, §7.4), R2 (local-model quality), D1 (graph-as-mirror, ADR-3), D5 (community
  precision). These are consequences of decisions in §9, not defects.
- **Tracked (address if the project's goals expand):**
  - *Scale* → R3 / D3 (brute-force retrieval) — back `SemanticIndex.search` with an ANN index.
  - *Redistribution* → **R7** (licensing) — the gating item before any release.
  - *Portability* → R6 / D8 (Windows-only, no CI) — add PyPI/Docker/cross-OS CI.
  - *Agent memory (Path B)* → D1 / D2 / D6 — the deliberate re-opening of ADR-3 / ADR-8.

## 11.5 Debt → roadmap direction

| Debt | Addressed by (see `docs/roadmap.md` "Future directions") |
|---|---|
| D1 authoritative graph, D6 contradiction versioning, D2 read-path reinforcement | Path B — invert to sessions/experiences + time-versioned edges |
| D3 brute-force retrieval | Direction A — hybrid retrieval / ANN / re-ranking |
| D4 partial incremental upsert | Direction E — full incremental graph |
| D7 observability | Direction F — deployment/observability |
| D8 packaging | Direction F — PyPI/Docker/CI |

---
*Chapter complete. R7/D-items are honest and specific rather than reassuring; the Path-B debts
(D1, D2, D6) trace directly back to the ADRs that flagged themselves "revisit for Path B" and
are designed out in `docs/path-b-memory.md`.*
