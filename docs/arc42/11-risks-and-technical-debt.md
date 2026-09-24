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
| R6 | **Windows-primary; CI Linux-only** | Low–Medium | Low | ✅ **Partly addressed (v0.64, ADR-24)** — GitHub Actions runs the offline suite (Py 3.11–3.13) + the Docker build on `ubuntu-latest` every push, so Linux is now *verified* (not just claimed). Remaining: no Windows/macOS CI leg. |
| R8 | **LLM-dependent memory semantics** — the capture model decides a fact's stated date + cardinality, and a coexistence verdict can keep a stale value current | Medium (wrong current fact) | Low–Medium | Dates are taken only when *stated* (never guessed) and validated; the coexistence check is **veto-only** (it can keep a fact, never hide one), deterministic (temperature 0), and skipped for explicit `--correct`; measured on `eval_temporal.jsonl` (ADR-27). |
| R7 | **Licensing before redistribution** | High if redistributed | Low (not yet redistributed) | PyMuPDF is **AGPL-3.0** and there is **no project LICENSE** yet (§2 LC1/LC3). Packaging is publish-*ready* (ADR-24) but deliberately **gated**: a `Private :: Do Not Upload` classifier + a manual-only publish workflow block release until a (AGPL-compatible) license is chosen — or a non-AGPL PDF backend is swapped in behind the parser boundary. |

## 11.2 Technical Debt

| # | Debt | Why it exists | Cost to address |
|---|---|---|---|
| D1 | **Graph is a mirror, not authoritative** | Deliberate (ADR-3) for a document wiki | ✅ **Addressed (v0.48, B0 / ADR-16)** — a remembered tier is now preserved across doc rebuilds; the doc tier stays a rebuildable mirror. |
| D2 | **Reinforcement only fires in writable contexts** | Kuzu exclusive-lock model (ADR-8) | ✅ **Addressed (v0.49, B1 / ADR-17; generalized v0.57 / ADR-19)** — read-only `ask`/MCP append usage to a log a writer folds in; ADR-19 then makes `serve`/`chat` read-only too so readers run concurrently and *all* writes queue to a lock-free journal (Kuzu is reader-XOR-writer — no simultaneous read+write). |
| D3 | **Retrieval ignores Kuzu's HNSW; brute-force cosine** | Simplicity at small scale | Medium — back `SemanticIndex.search` with an ANN index when corpora grow. |
| D4 | **Incremental upsert recomputes only `SIMILAR_TO`** | Cheap live sync for edits | Medium — CHILD_OF/NEXT/REFERENCES/entities still need a full `graph-build`. |
| D5 | **Community precision is moderate / labels heuristic-then-LLM** | Broad thematic questions span themes | Low — acceptable; measured (precision 56.7% on the thematic set). |
| D6 | **No contradiction handling / time-versioning of facts** | Out of scope for a static wiki | ✅ **Addressed (v0.50, B4 / ADR-18; bi-temporal since v0.81, B7 / ADR-27)** — facts carry valid + transaction time and merge by valid time; `recall` returns the current fact, `--as-of`/`--known-at`/`--timeline` read the history. |
| D7 | **Minimal observability** (stderr logs only) | Local single-user tool | ✅ **Addressed (v0.58/v0.60, ADR-20)** — an in-process metrics collector captures per-call LLM/embed latency + tokens (and per-build-stage), surfaced in the CLI, the **System** tab, and Projekt. |
| D9 | **B7 limits** — a multi-valued fact ends only via `--correct` (no negation capture: "we no longer use X"); `known_at` is approximate when an interval is re-closed later (rows are updated in place, not versioned) | Kept the model minimal (ADR-27) | Low–Medium — capture negations as an explicit end date; row versioning if exact belief-history ever matters. |
| D10 | **Inline citations stay plain text** — "Abschnitt 1.6 / Seite 42" in the prose is not a link (the targets appear only in the "Verwandte Seiten" panel) | The reference label wasn't stored on `REFERENCES` (ADR-28) | ✅ **Addressed (v0.83)** — `REFERENCES.labels` + client-side inline linking; `openwiki references` refreshes an older graph in place (informatik: 33/33 phrases linked). |
| D8 | **Packaging is Windows/pipx-only** | Primary platform | ✅ **Partly addressed (v0.64/v0.65, ADR-24)** — a Docker image + compose, CI, and a clean build as the `owiki` distribution; the actual PyPI publish remains (license-gated, R7). |

## 11.3 Debt that is *not* present (by design)

- No dual-write consistency problem (ADR-3: one authority).
- No feature-flag sprawl for optional layers (ADR-7: empty tables).
- No hidden cloud dependency or key management (ADR-2).

## 11.4 Risk posture — accept vs. track

For a single-user, local, learning project the sensible posture is:

- **Accepted (by design, given the local-first scope):** R1 (no auth — mitigated by
  localhost-only, §7.4), R2 (local-model quality), D5 (community precision). These are consequences
  of decisions in §9, not defects.
- **Addressed (Path B landed):** D1 / D2 / D6 — B0 / B1 / B4 (ADR-16 / 17 / 18); the memory tier is
  authoritative, reads reinforce, and facts are time-versioned — **bi-temporally** since B7 (ADR-27). See
  `docs/path-b-memory.md`.
- **Addressed (post-Path-B):** D7 observability (ADR-20), D8 packaging + R6 CI *partly* (ADR-24 — Docker +
  Linux CI landed; PyPI publish + Windows/macOS CI remain).
- **Tracked (address if the project's goals expand):**
  - *Scale* → R3 / D3 (brute-force retrieval) — back `SemanticIndex.search` with an ANN index.
  - *Redistribution* → **R7** (licensing) — the gating item before any PyPI release (packaging is ready).
  - *Portability* → R6 (a Windows/macOS CI leg, now that Linux is covered).
  - *Memory fidelity* → R8 / D9 (LLM-decided dates + coexistence; negation capture, exact belief history).
  - *Wiki linking* → D10 ✅ (inline citation links, v0.83).

## 11.5 Debt → roadmap direction

| Debt | Addressed by (see `docs/roadmap.md` "Future directions") |
|---|---|
| ✅ D1 authoritative graph (B0), D2 read-path reinforcement (B1), D6 contradiction versioning (B4) | Path B — **landed** (v0.48–v0.50); sessions/experiences in + time-versioned facts |
| D3 brute-force retrieval | Direction A — hybrid landed (ADR-21); ANN still open |
| D4 partial incremental upsert | Direction E — full incremental graph |
| ✅ D7 observability (v0.58/v0.60, ADR-20) | Direction F — metrics collector + System tab + build timings |
| D9 B7 limits | Path B+ — B7 follow-ups (negation capture; row versioning only if needed) |
| ✅ D10 inline citation links (v0.83) | The wiki-linking track (#1) — `REFERENCES.labels` + `openwiki references` |
| ✅ D8 packaging *partly* (v0.64/v0.65, ADR-24) | Direction F — Docker + CI + `owiki` build; PyPI publish license-gated |

---
*Chapter complete. R7/D-items are honest and specific rather than reassuring; the Path-B debts
(D1, D2, D6) that ADR-3/ADR-8 flagged "revisit for Path B" have now been **designed out and shipped**
(B0/B1/B4, ADR-16/17/18) — see `docs/path-b-memory.md`.*
