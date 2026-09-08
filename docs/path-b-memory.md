# Path B — Agent Memory (design)

> **Status: IN PROGRESS.** First slices have landed: the remembered tier (`remember`/`recall`,
> v0.46), the cross-session eval that validated it (v0.47), **B0 — the authoritative-graph reframe**
> (v0.48): the memory tier now **survives document rebuilds**, a per-project **Wiki vs Second Brain
> mode** (`[memory] enabled`) gates it, and a **session source type** feeds it through `openwiki
> build`; and **B1 — read-path reinforcement** (v0.49): ordinary read-only `ask`/MCP now teach the
> graph via an append-only usage log a writer folds in. Still ahead: B4 (contradiction/time-
> versioning), B5 (sleep consolidation), and the full three-tier B6 assembly. This remains the living design base
> for Path B — turning OpenWiki's knowledge graph from a document **mirror** into agent **memory**.
> The roadmap-level overview lives in [`docs/roadmap.md`](roadmap.md#path-b--the-second-brain-memory-model);
> this document is the deep design (concepts → target architecture → data model → staged plan →
> evaluation → open decisions). It re-opens arc42 **ADR-3** and **ADR-8** and addresses debts
> **D1/D2/D6** (see [`docs/arc42/`](arc42/)).

## Contents
1. [Motivation & main idea](#1-motivation--main-idea)
2. [Conceptual model](#2-conceptual-model)
3. [Target architecture](#3-target-architecture)
4. [Proposed data model](#4-proposed-data-model)
5. [The core algorithm: session → world-model merge](#5-the-core-algorithm-session--world-model-merge)
6. [Staged delivery plan (B0–B6)](#6-staged-delivery-plan-b0b6)
7. [Evaluation strategy](#7-evaluation-strategy)
8. [Risks & open decisions](#8-risks--open-decisions)
9. [Relationship to the current code](#9-relationship-to-the-current-code)
10. [Recommended first slice](#10-recommended-first-slice)
11. [Prior art & learnings — "Cognitive Substrate"](#11-prior-art--learnings--cognitive-substrate)

---

## 1. Motivation & main idea

**In one sentence:** stop treating the graph as a disposable *mirror* of static documents, and
make it the agent's *living memory* — something that accumulates from what actually happens
(conversations/sessions), strengthens what gets used, forgets what doesn't, and reconciles new
facts against old.

Today OpenWiki ingests **documents** → builds a wiki → mirrors it into a graph that is a *pure
function of its inputs* (arc42 ADR-3). The graph is disposable and rebuildable; it has no memory
of use beyond the v0.43 `REINFORCES` overlay, and it can't hold anything that didn't come from a
source file. Path B inverts this: **experience** (sessions) becomes a first-class input, and the
graph becomes the **authoritative, evolving store** of what the agent has learned.

Why this is the frontier: Path A (communities / global search) and v0.43 (reinforce / decay)
already built the *attractor* tier and the Hebbian-plus-forgetting mechanics. What's missing is
(a) getting experience **in**, (b) an **authoritative** place to keep it, and (c) the two things
no off-the-shelf system ships — automatic **contradiction handling** and true **cross-session
consolidation**.

**The payoff — "concentrate, don't replay":** a new session's context is *assembled from memory*
(identity + the activated sub-graph + consolidated summaries) instead of by pasting prior chat
logs. The agent starts a new session already oriented, without re-reading everything.

## 2. Conceptual model

The North Star (from the memory-evolution discussion) mapped **literally** onto OpenWiki:

- **the graph = the cortex** — long-term structural memory;
- **a session = a day** — fresh experience in a bounded buffer (the context window ≈ hippocampus);
- **consolidation = sleep** — compress the day into structure, integrate it, forget the noise;
- held at the **edge of chaos** — dense enough to remember, decayed enough to stay plastic.

### 2.1 Three-tier memory (how a session's context is assembled)

| Tier | Biological analogue | Content | OpenWiki realization |
|---|---|---|---|
| **DNA / identity** | genome (stable code) | who the user/agent is; invariants; standing instructions | project manifest + a persistent identity doc |
| **Epigenetic / activation** | methylation (which genes are read now) | the sub-graph relevant to *this* query, weighted by decayed usage | GraphRAG expansion over `SIMILAR_TO`/`REINFORCES`, ranked by effective weight |
| **Attractor / consolidated** | stable cell state | compressed meta-nodes carrying the *structure* of past experience | Path A community summaries |

Retrieval = identity (always) + activated sub-graph (epigenetic) + relevant attractor summaries.
The transcript is **not** replayed; its *structure* is.

### 2.2 The engineering residue of the metaphor

The biology is inspiration, not an algorithm. What it concretely yields:

- **Attractors → meta-nodes** (already: communities).
- **Epigenetics → selective, decayed activation** (already: expansion + decay).
- **Edge of chaos → one operational knob**: keep the graph at useful density via decay + pruning.
- **Sleep → a scheduled consolidation job** (compress / integrate / forget).

Anything not reducible to those (e.g. "simulate the network until it self-organizes") is left as
metaphor and **not** built.

## 3. Target architecture

The pipeline gains a second inflow and the graph changes role:

```
documents ──parse_source──▶ ParsedDocument ──▶ wiki ──▶ index ──▶ graph  (derived-from-docs)
                                                                    ▲
sessions ──capture──▶ session sub-graph ──merge──▶  world-model graph  (remembered-from-experience)
                                                          │  (authoritative; survives doc rebuilds)
                                          sleep/consolidation (communities + decay + abstraction)
                                                          │
                                    context_for(query) = DNA + activation + attractors
```

Two invariants define the reframe:

1. **The graph is authoritative** for remembered content (re-opens ADR-3). A `graph-build` from
   documents may rebuild the *derived* subgraph but must **preserve** the *remembered* subgraph.
2. **The graph is read-mostly but continuously updated** (re-opens ADR-8). Ordinary reads
   (`ask`, MCP) must be able to record usage and, on consolidation, fold in new memory — without
   the exclusive-writer bottleneck.

### 3.1 Modes — Wiki and Second Brain (coexistence, not replacement)

Path B does **not** replace Path A; it adds a **remembered tier on top of** the authoritative
document tier, on **one substrate** (arc42 **ADR-14**). Path A and Path B are two *kinds of
knowledge* — authoritative documents vs evolving experience — with different trust and lifecycle,
so they coexist as tiers rather than compete. **"Mode" is a per-project policy** over which tiers
reads fuse and whether memory writes/consolidation run — not a fork in the code:

| | **Wiki Mode** (= today + Path A) | **Second Brain Mode** (Path B) |
|---|---|---|
| Substrate (tiers read) | document tier only (CANONICAL) | document **+** remembered tiers |
| Reads | RAG / GraphRAG / global over docs | fused over both, authority × confidence weighted |
| Writes | none (rebuild from sources) | capture → merge → decay → consolidate |
| Trust / lifecycle | authoritative, reproducible, shareable | interpreted, evolving, personal |
| Enabled by | default (memory tier empty) | `[memory] enabled` on the project |

Second Brain is a **superset** of Wiki: the remembered tiers are additive tables (ADR-7), so Wiki
Mode is literally "memory tier off," and the document tier is the ground-truth **anchor** memory
resolves and cites against. The payoff is using them **together** — global search spanning docs +
remembered summaries; the agent answering grounded in docs but *informed by* accumulated memory.

**Chosen defaults for the coexistence sub-decisions** (revisable):
- **Mode granularity** — per **project** (a manifest flag), with per-query tier filters for control.
- **Global search** — in Second Brain Mode, **fused** (docs + memory summaries), authority-weighted.
- **Sharing** — the document tier is shareable/publishable; the **memory tier stays private**
  (serves the retention/privacy concern in §8).
- **Trust weighting** — a read ranks by **authority tier × decayed confidence** (a canonical doc
  fact outranks a distilled memory fact outranks a low-confidence raw one).

Lifecycles are **independent**: `graph-build` rebuilds the document tier but **preserves** the
remembered tier (B0's exit criterion); consolidation touches memory, never documents.

## 4. Proposed data model

*(Design sketch — names/shapes will firm up during B0/B2. Additive, following ADR-7: new tables,
empty until used, so existing code degrades gracefully.)*

New node tables:

| Node | Purpose | Key fields (proposed) |
|---|---|---|
| `Session` | one "day" of experience | `id`, `started_at`, `source` (chat / event), `summary` |
| `Assertion` | a reified fact (so it can be versioned/contradicted) | `id`, `subject`, `predicate`, `object`, `valid_from`, `superseded_by` (nullable), `confidence`, `session_id` |

Reusing existing `Entity` nodes as the subjects/objects of assertions keeps entity resolution in
one place. New edges:

| Edge | From → To | Purpose |
|---|---|---|
| `FROM_SESSION` | Assertion / Entity → Session | provenance (which day produced this) |
| `ASSERTS` | Session → Assertion | what a session claimed |
| `ABOUT` | Assertion → Entity | link an assertion to its subject/object entities |
| `SUPERSEDES` | Assertion → Assertion | contradiction/versioning (newer over older) |

`REINFORCES(weight, last_seen)` (v0.43) is reused unchanged for usage-memory. A `MergeRun` /
`ConsolidationRun` audit node (per §11) records each merge/sleep pass (counts, model, cost,
provenance) so a run is traceable and reversible.

**Reified assertions** are the key design choice: representing a fact as a node (not a bare typed
edge) is what lets B4 mark it superseded, time-scope it, and carry provenance/confidence — the
price is an extra hop in queries. Two alternatives, both considered:
- typed `Entity→Entity` edges with validity props — lighter, but versioning/provenance are awkward;
- a **single `FACT` edge with `predicate` as a property** (Cognitive Substrate's choice — §11):
  one index set, no per-predicate migration, but "predicate is a filter not a traversal" and
  provenance can't attach to an edge (they add a separate run node). Our reified choice pays one
  hop to get native versioning + provenance — exactly what B4/B5 need. Revisit if traversal cost bites (§8).

## 5. The core algorithm: session → world-model merge

Each session becomes a small typed sub-graph, merged into the macro-graph in four phases. **Phases
3–4 are where Neo4j / Kùzu / Microsoft GraphRAG stop** — they're Path B's real contribution.

| Phase | What it does | Method |
|---|---|---|
| **1. Entity resolution** | anchor session nodes to existing ones | normalized name (`_normalize`, ADR-12) + `hybrid_search` vector match above a confidence threshold; else create |
| **2. Hebbian weighting + decay** | strengthen confirmed links, fade unused | `reinforce()` on confirmed edges; `decay()` ages the rest |
| **3. Contradiction harmonization** | newer facts supersede older | same `(subject, predicate)` with a different `object` and a later timestamp → mark the old `Assertion.superseded_by`; keep both (history preserved) |
| **4. Abstraction / compression** | collapse settled detail into meta-nodes | re-run community detection + summaries incrementally over the merged graph |

## 6. Staged delivery plan (B0–B6)

Ordered so each stage is shippable and measurable, with the riskiest reframes placed early enough
to matter but late enough to de-risk. Each stage lists an **exit criterion** (how we know it's done).

### B0 — Reframe: authoritative graph + a "session" source type
- **Goal:** make the graph a store of record with a **document tier and a memory tier** (§3.1); let
  experience flow in *alongside* documents, not replacing them.
- **Build:** add the **remembered tier alongside** the document tier — split *derived-from-docs* vs
  *remembered-from-experience*; make `graph-build` preserve the remembered subgraph; add a
  **session/experience** source type beside pdf/md/html/code; gate memory behind a per-project
  **mode** (`[memory] enabled` → Wiki vs Second Brain — §3.1, ADR-14).
- **Builds on:** the `parse_source` dispatch pattern; the project layer (a project now owns an
  evolving memory + its mode); the additive-table pattern (ADR-7 — Wiki Mode = memory tier off).
- **Re-opens:** ADR-3 (debt D1).
- **Hard part:** the derived-vs-remembered split; a wrong boundary means rebuilds destroy memory or
  accumulate garbage.
- **Exit:** rebuild the doc-graph and prove (test) the remembered subgraph survives untouched.
- **Learned (§11):** make the split a **tiered write-authority** model — CANONICAL (docs;
  authoritative, never pruned) vs SEMANTIC / PROCEDURAL / EPISODIC (remembered) — where a role can
  only write *its* tier, so the agent's guesses can't forge ground truth.
- **Landed (v0.48).** The exit criterion is met: `GraphBuilder` **snapshots the remembered tier**
  (Session/Assertion/ASSERTS, plus the `REINFORCES` usage overlay) before its destructive rebuild
  and **restores it** into the fresh schema (dropping only assertions whose embedding dim changed) —
  proven by a unit test *and* live (`build --only graph --force` keeps every remembered fact
  recallable). Mode gating shipped as **`[memory] enabled`** (`Project.memory_enabled`, default off =
  Wiki mode) — it gates `remember`/`recall` and the build memory stage, and shows in `status` /
  the Projekt tab. The **session source type** shipped too (`init --session` / `project add-source
  --session` → a `type = "session"` source, excluded from the doc pipeline), captured by a new
  **`memory` build stage** (`openwiki build`, off the doc fingerprint chain since a rebuild now
  preserves memory). **Still deferred within B0:** the full **tiered write-authority** model (roles
  writing only their tier) — the reframe landed the *lifecycle* (preserve-on-rebuild) and the *mode*,
  not yet the authority enforcement.

### B1 — Read-path reinforcement (writable-safe)
- **Goal:** ordinary use (`ask`, MCP `wiki_ask`) strengthens memory, not only `serve`/`chat`.
- **Build:** a concurrency model where reads record usage — most likely an append-only usage log a
  background writer folds in (sidesteps Kuzu's exclusive-writer lock).
- **Builds on:** v0.43 `reinforce()`/`decay()` (mechanics already exist + tested).
- **Re-opens:** ADR-8 (debt D2).
- **Hard part:** concurrent readers + a writer under Kuzu's exclusive lock.
- **Exit:** after a scripted set of asks, useful edges are measurably heavier than noise; no lock contention errors.
- **Landed (v0.49).** Exactly the append-only-log design: `RAGAgent._expand` records the seed→related
  pairs it retrieves via `GraphStore.record_usage` — reinforced immediately on a **writable** graph
  (serve/chat, unchanged), or appended to a **usage-log sidecar** (`graph.usage.jsonl`, `graph/usage.py`)
  on a **read-only** `ask`/MCP in Second Brain mode (`log_usage`), which never touches Kuzu's exclusive
  write lock. The next writer **folds it in** (`fold_usage` → `reinforce` each pair, then clear): serve/chat
  drain it on startup, and `openwiki decay` folds it *before* aging. Proven live — two read-only asks
  logged their pairs (no graph write), then `decay` folded them into `REINFORCES` edges (2 records → 2
  edges, log cleared). Reads now teach the graph, not just serve/chat. **Still deferred:** a true
  concurrent reader-*and*-writer model (the log defers writes rather than allowing simultaneous ones) —
  enough for the CLI/MCP usage pattern, where a writer runs between read sessions.

### B2 — Session capture → typed sub-graph
- **Goal:** turn a conversation into a small typed knowledge sub-graph (the day's trace).
- **Build:** an LLM pass (shape of `entities.py`/`community.py` — pure, chat-injected,
  fake-testable) → entities + typed relations + provenance + timestamp.
- **Builds on:** entity extraction (typed ontology, normalization); the pure-module + fake-chat pattern.
- **Hard part:** deciding *what's worth remembering* (signal vs chit-chat).
- **Exit:** precision/recall of extracted facts vs a hand-labeled session clears a set bar.
- **Learned (§11):** gate every captured fact — entity-dedup by vector similarity,
  controlled-vocabulary predicates, **source-support required** (a claim must be grounded in the
  turn — the anti-hallucination gate), anti-vagueness (reject "the system"). Trigger capture on the
  host `Stop` hook (end of turn), **fail-soft**.
- **First slice (v0.46):** landed a reduced form — `graph/memory.py` `capture_session()` extracts
  flat **subject–predicate–object** triples via one chat call (pure, fake-testable), stored as
  reified `Assertion` nodes under a `Session` (`ASSERTS`). No capture gating yet beyond key-dedup;
  typed relations/ontology + signal-vs-chit-chat filtering are still ahead.

### B3 — Merge operator (Phases 1–2)
- **Goal:** fold the session sub-graph into the world model without duplicating.
- **Build:** `merge(subgraph)` = entity resolution (name + vector, confidence-thresholded) +
  Hebbian reinforcement of confirmed links.
- **Builds on:** `_normalize` (ADR-12), `hybrid_search`, `reinforce()`.
- **Hard part:** resolution errors compound permanently → confidence threshold + provenance so a
  merge is auditable/reversible.
- **Exit:** merge precision on a curated set clears a set bar; every merge is traceable to a session.
- **Learned (§11):** record each merge as a reversible `MergeRun` audit node (counts, model, cost,
  provenance); rollback = close validity on its outputs (append-only, no destructive undo).
- **First slice (v0.46):** `GraphStore.remember()` folds a session in with **dedup-only merge** — a
  normalized `(subject, predicate, object)` key (`_normalize`, ADR-12) drops repeats within and
  across sessions (proven by a test). Vector entity-resolution, Hebbian reinforcement of confirmed
  links, and the `MergeRun` audit node are still ahead.

### B4 — Contradiction / time-versioning (Phase 3) — the novel piece
- **Goal:** a newer fact supersedes an older one without losing history.
- **Build:** `Assertion` validity (`valid_from`, `superseded_by`); conflict → mark old superseded;
  retrieval prefers the latest valid assertion.
- **Builds on:** the `REINFORCES.last_seen` timestamp pattern → validity intervals.
- **Addresses:** debt D6.
- **Hard part:** genuinely unshipped-anywhere; keep detection **boring & tractable** (same
  subject+predicate, different object, later timestamp → supersede), explicitly *not* a
  "simulate-for-chaos" scheme.
- **Exit:** on a "fact changed" scenario, the agent answers the current fact, not the stale one,
  and the superseded history is still queryable.
- **Learned (§11):** make append-only an **invariant on every remembered edge** (`valid_from` /
  `valid_to`), not only on conflict — any change closes the old + appends the new. Contradiction
  becomes one case, and time-travel + reversible consolidation come for free.

### B5 — Sleep: cross-session consolidation job (Phase 4)
- **Goal:** periodically compress accumulated memory into structure.
- **Build:** a scheduled job over the merged graph: re-detect communities + regenerate summaries,
  run `decay()`, collapse settled detail into higher meta-nodes — incrementally.
- **Builds on:** `communities` (Louvain + summaries) and `decay()` already exist — mostly
  *re-targeting* them at session-memory + scheduling.
- **Hard part:** incrementality (don't re-summarize the whole graph); density tuning ("edge of chaos").
- **Exit:** graph size stays bounded over many sessions while global-search quality holds.
- **Learned (§11):** **stability risk** — modularity clustering (our Louvain) admits many
  near-optimal partitions, so communities can *shift under incremental edits*; evaluate **k-core
  decomposition** (deterministic, stable nested hierarchy) for the evolving graph (open decision, §8).
  Also adopt **per-tier decay half-lives** and a **source-invalidation cascade** (a changed source
  flags everything `derived_from` it for re-validation).

### B6 — Three-tier context assembly
- **Goal:** the payoff — build a new session's context from memory, cheaply.
- **Build:** `context_for(query)` = identity (DNA) + decay-weighted activated sub-graph
  (epigenetic) + relevant attractor summaries; exposed via the agent + a new MCP capability.
- **Builds on:** GraphRAG expansion, community summaries, the project/identity doc.
- **Hard part:** budgeting three tiers into a fixed context window; beating "just paste the transcript."
- **Exit:** the cross-session metric (§7) shows assembled memory beats both cold-start and raw-log.
- **Learned (§11):** assemble + inject on the host `UserPromptSubmit` hook, rescue on `PreCompact`;
  weight the activation tier by decayed **confidence**; keep it **fail-soft** (degrade to
  no-memory, never block the session).
- **First slice (v0.46):** `GraphStore.recall()` + `format_memory()` assemble a session's memory as
  a **decay-weighted** cosine ranking over assertion embeddings (`effective_weight` with a half-life
  — the activation tier), exposed as the `recall` CLI. It proves the two-session loop: a fact stored
  in session 1 surfaces for a session-2 query. Full three-tier assembly (identity + activated
  sub-graph + attractor summaries) and host-hook injection are still ahead.

## 7. Evaluation strategy

Path B lives or dies by measurement, same as the graph did (arc42 ADR-9). The current eval sets
are **single-shot** (one question → pages/communities); Path B needs a **new axis**:

- **Per-stage objective checks** (reuse the harness shape): B2 fact P/R; B3 merge precision; B4 the
  "fact changed" scenario (current-vs-stale); B5 graph-size-vs-quality over N sessions.
- **The headline metric — cross-session task success:** a scripted **multi-session** scenario where
  session *k* establishes facts and session *k+1* depends on them. Compare three conditions:
  1. **cold** — no memory,
  2. **raw-log** — previous transcript pasted into context,
  3. **assembled** — B6 three-tier context.
  Assembled should beat cold (it remembers) *and* raw-log (it's concentrated, not noisy), judged by
  task success + an LLM judge, position-balanced like the existing answer eval.

If "assembled" doesn't beat "raw-log", Path B isn't paying for its complexity — and we'll know early.

**Landed (v0.47) — the headline metric now runs, and the first result is in.** `owiki eval
--cross-session` implements exactly the three-condition comparison above (`eval.run_cross_session_eval`
+ a `eval_cross_session.jsonl` scenario set: `{"name","setup":[transcript,…],"question","expected":[…]}`).
Each scenario is remembered into a **throwaway** graph (isolated per scenario via
`GraphStore.forget_all`), then the probe is answered cold / raw-log / assembled; objective
`task_success` = the answer contains the expected fact, and `--judge` adds the position-balanced
*assembled vs raw-log* verdict. On the first 7-scenario set (qwen3 capture + bge-m3 recall):

| condition | task success |
|---|---|
| **cold** (no memory) | **0.0%** — confirms every probe genuinely needs memory |
| **raw-log** (paste the transcript) | **85.7%** (6/7) |
| **assembled** (decay-weighted `recall`) | **100.0%** (7/7) |

Judge (assembled vs raw-log, position-balanced): **assembled 3 · raw-log 1 · tie 3**. So assembled
beats **cold** (it remembers) *and* **raw-log** (concentrated, not noisy) — the win shows up both
objectively and to the judge, and is largest on the multi-session scenarios where the raw log buries
the fact under later chatter. **Caveats:** small N (7), short transcripts (a regime where raw-log is
already a strong baseline — the gap should widen as sessions accumulate), and the objective check is a
substring proxy. But the direction is the one Path B needed: **memory helps the next session, and
concentrating it helps more than replaying it.** This is the green light for the harder stages
(B0 authoritative graph, B4 contradictions).

## 8. Risks & open decisions

**Decisions to make (genuine forks):**
- **Assertion representation** — reified `Assertion` nodes (versionable, provenance-rich, extra hop)
  vs typed `Entity→Entity` edges with validity props (lighter, harder to version) vs a single
  `FACT` edge with `predicate` as a property (Cognitive Substrate's choice — one index, no
  per-predicate migration, but provenance/versioning are awkward on an edge). *Leaning reified* (§4/§11).
- **Abstraction: communities vs k-core** — Louvain communities can *shift under incremental edits*
  (§11's critique); evaluate **k-core decomposition** (deterministic, stable nested hierarchy) for
  the evolving graph, or a stability-preserving community-update strategy. *Open — B5.*
- **Capture trigger** — end-of-session batch vs streaming during the turn loop. *Leaning batch (a
  "sleep" pass), matching the biology and the host `Stop` hook (§11); avoids write-on-every-turn.*
- **Identity (DNA) storage** — a manifest field vs a dedicated identity doc vs a special graph node.
- **Retention / privacy** — remembered content is sensitive; needs a forget/redact story and a
  clear on-disk location (a session may contain things the user doesn't want persisted).

**Principles (adopted from prior art — §11):**
- **Fail-soft** — memory hooks degrade to "no memory" on error, never block a session.
- **Cost governance** — a per-session budget cap checked before each paid LLM call; cheap model for
  capture/distil, expensive only for reasoning.
- **Real embeddings only** — session sub-graph vectors use the real embedder (bge-m3), never a stub.

**Risks:**
- **Memory poisoning / drift** — bad captures or wrong merges corrupt memory permanently → provenance
  + confidence + reversibility (B3) and superseding-not-deleting (B4) are mitigations.
- **Contradiction is belief revision** — a decades-old hard problem; scope it to the tractable rule
  above, don't chase generality. (Even Cognitive Substrate left the "fact-has-changed" trigger open.)
- **Community instability** — see the k-core decision above; matters more as the graph evolves.
- **Complexity vs payoff** — the honest kill-switch is §7: if assembled context doesn't beat a pasted
  transcript, stop.
- **Concurrency** — B1's writable-safe model is load-bearing; get it wrong and memory writes corrupt
  the store or serialize everything.

## 9. Relationship to the current code

| Path B needs | Current code to reuse | New work |
|---|---|---|
| usage memory (B1/B3/B5) | `GraphStore.reinforce()` / `decay()` (v0.43) | read-path safe writes |
| capture (B2) | `entities.py` pattern (typed, normalized, fake-testable) | session → sub-graph pass |
| entity resolution (B3) | `_normalize` (ADR-12) + `hybrid_search` | confidence-thresholded merge |
| abstraction / sleep (B5) | `community.py` (Louvain + summaries) | incremental, over merged graph |
| activation + attractors (B6) | GraphRAG expansion + `communities()` | three-tier `context_for` + MCP tool |
| everything measurable (B*) | the `eval.py` harness | a multi-session eval axis |
| session ingest (B0) | `sources.parse_source` dispatch shape | authoritative graph + session source |

**Genuinely new:** B0 (authoritative graph + session ingest), B4 (contradiction/versioning), and
B6's cross-session eval. Everything else is largely *re-wiring proven parts*.

## 10. Recommended first slice

To de-risk the whole path for the least spend, build a **thin vertical** before the load-bearing
reframes:

> **B2 (capture) → B3 (merge) → B6 (assemble)** on a tiny scripted **two-session** scenario, with
> the graph still doc-derived (defer B0's authoritative reframe) and no contradiction handling
> (defer B4).

This yields a measurable answer to *"does assembled memory help the next session?"* (§7) fastest.
If yes → commit to B0 (authoritative graph) and B4 (contradictions), the hard, high-value stages.
If no → we've learned it cheaply, before touching ADR-3.

> **Landed (v0.46).** This thin vertical now exists end-to-end: `openwiki remember <transcript>`
> (capture → dedup-merge) and `openwiki recall <query>` (decay-weighted assemble), backed by
> additive `Session`/`Assertion`/`ASSERTS` Kuzu tables (created empty by every `graph-build`, so old
> graphs upgrade lazily) and 9 offline tests including a two-session proof-of-loop. Verified live on
> the informatik KB (qwen3 capture → 4 clean facts; bge-m3 recall ranks the right fact top for each
> new query). At v0.46 this was still **doc-derived** (B0 not yet done: `graph-build` rebuilt the
> doc graph and dropped the memory tier) with **no contradiction handling** (B4 deferred).
>
> **Then (v0.47) the §7 headline metric landed too** — `owiki eval --cross-session` — and the first
> result came out in Path B's favour: assembled **100%** vs raw-log **85.7%** vs cold **0%** task
> success, judge **3–1** assembled over raw-log (see §7). That's the green light for the hard stages
> (B0 authoritative graph, B4 contradictions).
>
> **Then (v0.48) B0 landed** — the graph is now **authoritative for memory**: a doc rebuild
> **preserves** the remembered tier (+ the `REINFORCES` overlay), gated by a per-project **Wiki vs
> Second Brain mode** (`[memory] enabled`) and fed by a **session source type** captured through a
> new `openwiki build` memory stage (§6/B0). Path B is no longer doc-derived — experience persists
> across rebuilds. Still ahead: B1 (read-path reinforcement) and B4 (contradiction/versioning).

## 11. Prior art & learnings — "Cognitive Substrate"

A concurrent sibling project (**Cognitive Substrate** — a neuroscience-structured agent-memory
substrate for a cyber-physical / UI agent) independently converged on nearly this design and is
**further along** (a built four-tier graph substrate + consolidation service). Its founding problem
differs (grounding UI *locators*, not knowledge Q&A) and its stack is heavier (Neo4j +
MCP-everything + multiple orchestration strategies), so we take the **ideas, not the weight**
(cf. arc42 ADR-6). Analysis distilled below; the specific refinements are folded into the stages
above as **"Learned (§11)"** notes and into §4/§8.

**Validated (our bets, independently confirmed).** Capture/distil split; consolidation as a
standalone *service*; single-engine graph+vector with fused retrieval (they *retired* a dual-DB
design to reach it); append-only temporal for versioning; a confidence reinforce/decay/retire
lifecycle; "concentrate, don't replay" retrieval.

**Refinements folded in.**

| Learning | Where folded |
|---|---|
| **Tier write-authority** — a role can only write its tier; the agent's guesses can't forge ground truth. Their CANONICAL (authoritative, never pruned) ≈ our doc-derived graph; EPISODIC/SEMANTIC/PROCEDURAL ≈ remembered. | B0 |
| **Append-only for *every* remembered edge** (not just on conflict) → time-travel + reversible consolidation for free. | B4, §4 |
| **Write-time validation gates** — dedup, controlled-vocab predicates, source-support (anti-hallucination), anti-vagueness, near-dup merge, cross-tier contradiction rejection. | B2/B3 |
| **`ConsolidationRun`/`MergeRun` audit node**; rollback = close validity on its outputs. | B3/B5, §4 |
| **Tier-aware confidence** (per-tier half-lives; retrieval weighted by confidence; reset on re-emergence) + **source-invalidation cascade**. | B5, B6 |
| **Host-lifecycle triggers** — `UserPromptSubmit`→recall/inject, `Stop`→capture, `PreCompact`→flush — and **fail-soft** hooks. | B2/B6, §8 |
| **Cost governance** — per-session budget cap; cheap model for distil, expensive for reasoning. | §8 |
| **Real embeddings only** — a parallel project's hash-stub embeddings returned garbage; use bge-m3. | §8 |

**A real critique of Path A (→ B5, open).** They **reject modularity clustering** (our
Louvain/Leiden family) for hierarchical abstraction: on sparse graphs modularity has exponentially
many near-optimal partitions, so community assignments *shift between runs / under small edits* —
unstable "attractors" an agent can't navigate predictably. They use **k-core decomposition**
(deterministic, stable nested hierarchy). Our Louvain is deterministic *per run* but not *stable
across the continuous edits* Path B implies — so B5's incremental communities may churn. Tracked as
the communities-vs-k-core decision in §8.

**What deliberately does *not* transfer.** UI/locator grounding + executor self-check; sensor /
OPC-UA / PLC multi-modal fusion; the heavyweight Neo4j-plus-MCP-everything stack. Their honest risk
list is a caution, not a blocker: validated only on a **24-fact toy UI**; **2 of 5 consolidation
pipelines** built; k-core **blocked by missing tooling** — i.e. *consolidation is the perennially
unfinished part*, which is exactly why our plan front-loads a measurable thin vertical (§10).

*Source: `G:\Claude\Cognitive Substrate\docs\ARCHITECTURE.md` + `ROADMAP.md` (v3), read 2026-09.*

---

*Cross-refs: overview → [`docs/roadmap.md`](roadmap.md#path-b--the-second-brain-memory-model);
decisions this re-opens → arc42 [ADR-3, ADR-8](arc42/09-architecture-decisions.md); debts it
addresses → arc42 [§11 D1/D2/D6](arc42/11-risks-and-technical-debt.md); the project unit it extends
→ arc42 [§8.14](arc42/08-crosscutting-concepts.md); the memory concepts → arc42
[§8.1 (IR)](arc42/08-crosscutting-concepts.md).*
