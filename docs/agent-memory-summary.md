# Agent memory — what twelve systems teach OpenWiki

The summary of [`memory-systems-review.md`](memory-systems-review.md): twelve agent-memory systems, read from their
source code in October 2026 and compared with OpenWiki's memory tier (Path B, [`path-b-memory.md`](path-b-memory.md)),
plus one design report. What each system is, what separates them, where they agree, where OpenWiki stands — and what
we will do about it.

**In one paragraph.** The field agrees on more than it seems: lexical search next to dense search, the raw record kept
searchable, memory written while the conversation runs, richer units than atomic facts, separate memory types with
procedures among them, and interpretations derived over facts that stay immutable. OpenWiki is ahead on a real time
model, deterministic hygiene, policy-based forgetting, local-first operation and audited measurement — and behind on
exactly those convergent points, retrieval above all. The plan below takes the deterministic fixes first (credential
redaction, writes that land during a session, no memory for chore prompts, a portable export), then the retrieval
experiments we can measure on graphs we already have, then the changes only real sessions can judge.

## The systems at a glance

| # | System | What it is | Store | Who writes memory | Time | Reported result |
|---|---|---|---|---|---|---|
| 1 | [waku-agent](memory-systems-review.md#1-waku-agent) | a personal assistant written as a teaching blueprint | SQLite (facts, episodes, chat log) | extraction every six exchanges | none | memory evals proposed |
| 2 | [Zep / Graphiti](memory-systems-review.md#2-zep--graphiti) | a framework for temporal context graphs | Neo4j / FalkorDB / Neptune | an LLM pipeline per episode (5+ calls) | bi-temporal | DMR, LongMemEval; LoCoMo disputed (58–84 %) |
| 3 | [Mem0](memory-systems-review.md#3-mem0) | a memory layer with plugins for coding agents | ~20 vector stores + SQLite | one add-only extraction per batch | creation time (event time on the platform) | LoCoMo, LongMemEval, BEAM (platform, gpt-4o) |
| 4 | [Letta (MemGPT)](memory-systems-review.md#4-letta-memgpt) | a stateful agent harness whose agent owns its memory | Markdown files in git | the agent + a dreaming subagent | git history | LoCoMo 74.0 % (gpt-4o-mini) |
| 5 | [Cognee](memory-systems-review.md#5-cognee) | a knowledge and memory engine, a "company brain" | SQLite + LanceDB + LadybugDB | session lessons, then graph extraction | an event graph, query-time windows | BEAM 0.79 (100 K) |
| 6 | [LangMem](memory-systems-review.md#6-langmem) | memory primitives for LangGraph agents | LangGraph store | a model patches documents in the background | none (rewrites) | none |
| 7 | [Hindsight](memory-systems-review.md#7-hindsight) | a memory server; per-repository memory for coding agents | Postgres + pgvector | 5W facts extracted, observations derived | event + mention time | LongMemEval 83.6 % (20B open) / 91.4 %; LoCoMo 89.6 % |
| 8 | [MIRIX](memory-systems-review.md#8-mirix) | a personal assistant that watches the screen | Postgres + pgvector + BM25 | a router + six manager agents | timestamped episodes; newer wins, or both kept | LoCoMo 85.4 % |
| 9 | [AriGraph](memory-systems-review.md#9-arigraph) | the world model of a text-game agent | research code | triplets per observation; an LLM replaces outdated ones | current state + raw episodes | TextWorld; MuSiQue, HotpotQA |
| 10 | [Nemori](memory-systems-review.md#10-nemori) | topic-segmented episodes, predict–calibrate | Postgres + Qdrant | four model calls per segment | dated episodes | LoCoMo 0.83 (gpt-4.1-mini) |
| 11 | [memory-champ](memory-systems-review.md#11-memory-champ) | CoALA as an MCP service, without a model of its own | SQLite + sqlite-vec + FTS5 | the host agent, through tools | volatility classes | acceptance tests |
| 12 | [Hermes Agent](memory-systems-review.md#12-hermes-agent) | a self-hosted agent harness with a memory slot | two Markdown files + SQLite FTS5 | the agent + a background review every 10 turns | none | behavior probes |
| — | **OpenWiki** | the memory under a coding agent, next to a document wiki | Kuzu (archived upstream, R10) | a local 30B captures sessions; the agent writes via `wiki_remember` | bi-temporal | LoCoMo 60.7 % J (local 30B; judge ≈ 7 points generous) |

The thirteenth review is a [design report, not a system](memory-systems-review.md#13-a-design-report-predictive-world-models-through-transfer-entropy):
predictive world models built by transfer entropy — real methods, applied where their preconditions fail.

**What each one is best at — and its lesson for us**

| System | Signature idea | Lesson for OpenWiki |
|---|---|---|
| waku-agent | facts, episodes, skills and persona kept apart; a per-turn memory gate | typed memory; inject only when it helps |
| Zep / Graphiti | facts as edges between entities, each with a validity window; hybrid search with rerankers | our closest relative (B7 follows its bi-temporal idea); retrieval; it showed that Kuzu is archived |
| Mem0 | add-only memory — "nothing is overwritten"; rich 15–80-word memories | inject once and let the agent pull; credential redaction; test whether our write-time checks earn their place |
| Letta | memory is the agent's own context, files in git, curated by a dreaming subagent | a git-tracked Markdown export; context as an index plus a pull; searchable sessions |
| Cognee | session lessons with feedback counts; the COGX exchange format | the Kuzu → LadybugDB path already taken; COGX as our export target |
| LangMem | a clear taxonomy: profiles vs collections, hot path vs background | a small profile-like core; capture in the background when a conversation goes quiet |
| Hindsight | world facts apart from the agent's experience; evidence-backed observations; knowledge pages per repository | the largest distance to our numbers — mostly retrieval, partly richer facts |
| MIRIX | six purpose-typed memories, each with its own manager | per-type budgets; procedures from errors and fixes; keep both sides of an unresolved conflict |
| AriGraph | current state in a graph, history in episodes linked to their triplets | multi-hop recall by expansion; episodes ranked by overlap with the recalled facts |
| Nemori | store only what existing memory failed to predict; dated narrative episodes | episodes for temporal questions; capture the whole stream (fixed in v0.97) |
| memory-champ | per-type write gates; procedures behind a signed human approval; contradictions surfaced | volatility classes against stale facts; a human gate for always-present memory |
| Hermes Agent | small curated memory once per session; skills learned from corrections, kept by a curator | writes should land during a session; no memory for chore prompts; Unicode hardening of the policy |

## What separates them — the design space

| Axis | Options seen across the series | OpenWiki |
|---|---|---|
| Who writes memory | the agent through tools (Letta, memory-champ, Hermes, LangMem's tools) · an extraction pipeline per message or batch (Graphiti, Mem0, Cognee, Hindsight, MIRIX, Nemori, AriGraph, waku) | whole sessions, captured afterwards by a local 30B; new states from the agent via `wiki_remember` |
| Unit of memory | atomic triples (AriGraph; Graphiti's edges) · rich sentences (Mem0, Hindsight's 5W facts) · narrative episodes (Nemori, waku) · files and documents (Letta, Hermes, Cognee) | atomic facts with validity, cardinality and source |
| Time and contradictions | overwrite (Hermes, LangMem) · add-only, the reader resolves (Mem0) · surface, don't resolve (memory-champ; MIRIX when unsure) · invalidate and keep history (Graphiti; Letta through git) | a bi-temporal merge by valid time with a coexistence check — only Graphiti also models both times |
| Retrieval | lexical + dense fused (Graphiti, Mem0, Cognee, Hindsight, MIRIX, memory-champ) · lexical only (waku; Hermes' session search) · graph expansion (Graphiti, Hindsight, AriGraph, Cognee) · rerankers (Graphiti, Hindsight) | dense similarity × confidence × recency, single hop |
| When memory enters the context | every prompt (Hindsight, Cognee) · once per session + pull (Mem0, Hermes) · an always-present core + pull (Letta) · behind a gate (waku) | 16 facts on every prompt + the handoff brief at session start |
| Consolidation | derived layers over immutable facts (Hindsight's observations, Graphiti's summaries and communities) · curators and dreaming agents (Letta, Hermes) · lessons with feedback (Cognee) | themes over current facts, warm-started and incremental |
| Forgetting | none (Graphiti, Mem0's library) · archiving by disuse (Hermes, for skills) · volatility rechecks (memory-champ) | policy-based archiving of one-off events; decaying usage edges |
| Hygiene | credential redaction (Mem0's plugin, Cognee, Hindsight) · threat scans (Hermes) · human gates (memory-champ, Hermes) · secrets stored on purpose (MIRIX) | a source-independent policy against instructions and security-sensitive facts; credentials redacted before capture and storage (v0.99) |
| Procedural memory | skills (waku, Letta, Hermes; memory-champ behind approval) · procedures from errors and fixes (MIRIX, Mem0's plugin) · rewritten prompts (LangMem) | none — the host's skills are written by hand |
| Evaluation | LoCoMo, LongMemEval, BEAM, DMR · behavior probes, fixtures or nothing (Hermes, memory-champ, LangMem) | own cross-session sets + LoCoMo, with an audited judge |

## Where they agree

Patterns most of the twelve share — and OpenWiki does not, yet:
1. **Lexical search next to dense search.** Six fuse BM25 or FTS5 with embeddings, two more search lexically alone. Our
   memory recall is dense-only — while the wiki side's hybrid search won decisively on a code corpus.
2. **The raw record stays searchable.** Seven keep raw sessions or episodes next to the extracted memory (Graphiti,
   Letta, Mem0, Cognee, Hindsight, AriGraph, Hermes). Our transcripts stay outside the memory tier.
3. **Memory is written while the conversation runs.** Seven write during the session (waku, Mem0, Letta, Cognee,
   LangMem, Hindsight, Hermes). We capture at compaction and session end — and until v0.100 those writes landed only
   after the session ended, because its MCP server held the graph read-only (found by the v0.98 handoff).
4. **Richer units than atomic facts.** Six store more context per memory: waku's episodes, Graphiti's sagas, Mem0's
   rich memories, Cognee's lessons with reasons, Hindsight's 5W facts, Nemori's dated narratives. Our weakest LoCoMo
   category, temporal questions (45.8 %), is where dated narratives help most — measured in v0.106: 48.3 → 64.2 %
   with one narrative per session next to the facts (§13.22 of `path-b-memory.md`).
5. **Memory types, procedures among them.** Five keep memory types apart (waku, Letta, Hindsight, MIRIX, memory-champ);
   seven keep procedural memory (skills, error → fix pairs, rewritten prompts). We keep one fact store.
6. **Interpretation derived over immutable facts.** Hindsight's observations, Graphiti's summaries and our themes keep
   the facts and recompute what they mean — the one convergent pattern OpenWiki already follows.

**Where nobody agrees:** whether a model should judge memory at write time. Mem0 dropped it, Letta and Hermes rely on
strong models as curators, Hindsight confines judgments to a derived layer. Our own audits found a local model's
judgments of memory unreliable — a poisoning audit, a forgetting review, three staleness checks (`path-b-memory.md`
§13.1–13.5) — and kept the write path deterministic where it could be. The two narrow checks that remain in the merge
were then ablated: unnecessary where facts rarely change (LoCoMo), necessary where state changes (§13.21).

## Where OpenWiki stands

**Ahead** — what few or none of the twelve have:
- **a real time model** — valid and transaction time, merged by valid time, as-of and known-at views (only Graphiti
  matches it);
- **deterministic hygiene against poisoning**, independent of the claimed source (only Hermes has a comparable scan);
- **forgetting by policy**, checked against hand labels (0 of 232 keep-facts dropped);
- **local-first** on a 30B model, without cloud calls;
- **measurement** with an audited judge, and paired comparisons before anything is adopted;
- **a document wiki and the memory in one graph**, and a session handoff derived from both (v0.98).

**Behind** — where the series converges and OpenWiki does not:
- **retrieval** — dense-only, single-hop, no reranker, no time window: most of the distance to Hindsight is here;
- **the unit of memory** — atomic facts lose the narrative and the dates that temporal questions need;
- **procedural memory**;
- **portability** — memory lives only in a Kuzu file, and Kuzu is archived upstream (R10).

**On numbers.** LoCoMo results across the series range from 60.7 % (ours, local 30B answering and judging) to 89.6 %
(Hindsight). They do not compare directly — different answer models (gpt-4o, gpt-4.1-mini, a 20B open model, our 30B),
different judges and leniency — but a gap that size is not only the model: Hindsight reaches 83.6 % on LongMemEval with a
20B open model.

## What we will do — in order

The candidates from all twelve reviews, ranked: deterministic fixes first, then experiments we can measure on graphs we
already have, then changes that only real sessions can judge. Each names where it was seen; each fix names its check.

**Fixes — deterministic, no experiment needed**

1. **Credential redaction and policy hardening.** Redact secrets from transcripts before capture and from facts in
   `remember()`; fold Unicode (NFKC) and reject invisible characters in `is_unsafe_text` (Mem0, Cognee, Hindsight,
   Hermes). Check: the poisoning set stays at 0 leaks with 8 of 8 legitimate facts kept, and no real fact is scrubbed.
   **Done in v0.99:** 0 false positives on 1,486 real facts and 1.57 M characters of session text, the eval sets
   untouched (`path-b-memory.md` §13.14).
2. **Writes land during a session.** The MCP server folds the journal itself when idle: release the read connection,
   fold writable, reopen (Hermes, Letta). Check: in a live session the journal drains within minutes of idling, and no
   reader sees an error. **Done in v0.100**, differently: readers hold the graph per call and writers lock only to
   apply (two phases) — the write lock on the real queue fell from 276 s to 7.2 s with an identical result, and an
   agent write landed 7.6 s after the call in a live session (`path-b-memory.md` §13.15).
3. **No memory for chore prompts.** Skip the injection for "push", "push and tag …" and slash commands (Hermes). Check:
   16 % fewer injections on the dogfooding prompts, the cue-trigger set unchanged. **Done in v0.101:** 87 of 474
   prompts (18.4 %) skipped — git chores, a slash command, later acknowledgements — and no eval question affected
   (`path-b-memory.md` §13.16).
4. **Portable memory.** A COGX export and a git-tracked Markdown view written at `sleep`, together with the LadybugDB
   spike (Cognee, Letta; R10). Check: an export → import round trip keeps every fact and its validity, and the graph
   opens under LadybugDB. **Done in v0.102:** `owiki memory export` / `import` (COGX) and the view at `sleep`; on the
   dogfooding memory a `--full` round trip kept all 1,486 facts identical in every field, embeddings and 118 themes
   included, and both archives pass Cognee's own reader. LadybugDB 0.19 runs 643 of 645 tests through a `kuzu` shim
   and the dev graph migrates with identical results apart from approximate vector search; before a move: the vector
   extension, a fresh statement cache after DDL, and a lock of our own — LadybugDB doesn't keep a writer out while a
   reader is open (`path-b-memory.md` §13.17, R10).

**Experiments — paired LoCoMo re-answers on existing graphs**

5. **BM25 fused into recall** (six systems). **Done in v0.103:** as a recall aid within the dense top 2k (distinctive
   terms only, dense order kept) — LoCoMo overall J 60.7 → 61.6 % (+47 / −34, n.s.; +24 / −2 where it brought the
   answer in); on real coding prompts the facts it swapped in were judged helpful 21.9 % vs 12.0 % for those it
   displaced (26 / 9 prompts, p ≈ 0.006) → on by default. Plain fusion moved nothing (`path-b-memory.md` §13.18).
6. **The question's time window** — boost facts whose validity overlaps a window parsed from the question (Cognee,
   Hindsight, MIRIX). **Done in v0.104:** rule-based windows (days, months, seasons, years, relative expressions), a
   bonus for facts whose valid time starts inside, within the dense top 4k — the dated questions 46.2 → 56.2 % (+24 /
   −3), overall J 61.6 → 62.9 % (p ≈ 5·10⁻⁵), the first significant gain of these experiments (`path-b-memory.md`
   §13.19).
7. **Multi-hop expansion** through shared subjects and objects, thresholded, depth 2 (AriGraph, Graphiti, Hindsight).
   **Measured, not adopted:** offline, linked facts (shared entity, distinctive word or semantic neighbour) pushed out
   better ones when swapped in (coverage 0.456 → 0.431) and lost to the ranking's own next candidates when added
   (+6 facts: 0.468 vs 0.480) — the needed facts are linked by meaning or inference, not names (`path-b-memory.md`
   §13.20).
8. **An add-only ablation** — do the two LLM checks in the merge earn their place? (Mem0). **Measured in v0.105 —
   they stay:** on LoCoMo the merge hardly matters (add-only 63.8 % vs 62.9 %, n.s.), on the temporal set it does
   (checks 13 / 13, add-only 12 / 13 — no retraction —, tags alone 10 / 13) (`path-b-memory.md` §13.21).
9. **Episodes next to facts** — one dated narrative per session, retrieved with the facts (Nemori, Hindsight, waku);
   aimed at the temporal category. **Measured in v0.106 — the largest gain:** 3 episodes next to the facts took
   LoCoMo overall J 62.9 → 74.7 % (+226 / −45; temporal 48.3 → 64.2 %, single-hop 68.6 → 81.6 %), adversarial 83.9 →
   71.5 % (narratives hold both speakers' days). In the harness. **Not for the live path:** narratives of coding
   windows invent specifics at ten times the facts' rate (12 % vs 1 %), and a stricter prompt plus a grounding filter
   cannot catch invented framing (§13.23); verbatim excerpts are the next candidate. Measured there first
   (`path-b-memory.md` §13.22).

**Changes judged in real sessions**

10. **When to inject** — the first prompt and after compaction, an index of themes, facts on file reads — instead of
    every prompt (Mem0, Letta, Hermes, Cognee).
11. **Session search for agents** — raw sessions searchable, full text first (Hermes, Letta, Graphiti).
12. **Typed facts with a volatility class** — volatile facts marked "possibly outdated" after some weeks (MIRIX,
    memory-champ). First evidence: a volatile-phrasing rule caught 12 of the 14 labeled stale facts while flagging
    10 % of memory (written after seeing them — confirm on a fresh labeled set); co-change dependencies were at
    chance, since every stale fact had been stated only once (`memory-systems-review.md` §13).
13. **An approval step** — the journal as the staging area for agent writes and for an always-present core
    (memory-champ, Hermes).
14. **Procedural memory from errors** — failure → fix pairs, with Hermes' guardrails: no negative tool claims, no
    unresolved failure presented as a method (MIRIX, Mem0's plugin, Hermes).
15. **A use signal first** — git co-changes as edges for code corpora, before any learning-from-use method (the
    transfer-entropy report).

**Not planned** — argued or measured against in the reviews:
- extraction pipelines per message or segment — several model calls each is too slow on a local 30B (Graphiti, MIRIX,
  Nemori);
- the agent as the only writer — memory would depend on its diligence (Letta, memory-champ);
- model judgments of memory at write time on a local model (our audits);
- a few kilobytes of curated files as the whole memory (Hermes);
- secrets kept in memory (MIRIX);
- transfer entropy on interaction telemetry (§13).

## How the series was done

Each system was read from its repository at a fixed commit, together with its paper where one exists, and described
against one yardstick — purpose and runtime, store and data model, capture, retrieval, time and contradictions,
consolidation, forgetting and hygiene, identity and procedural memory, agent writes, surfaces and sharing, evaluation.
Where a lesson could be checked cheaply against OpenWiki's own data, it was checked, and the checks changed things: the
capture-coverage gap that Nemori exposed was fixed in v0.97 (at most 13 % of a long session had reached memory); the in-session
writes that never landed (Hermes) and the empty usage memory (the transfer-entropy report) were found by checking; two
of Hermes' rules — declarative facts, no negative tool claims — turned out to hold for our capture already.

Not yet reviewed from the [shortlist](memory-systems-review.md#candidates--world-models-and-coala): HippoRAG 2, the
Microsoft human-inspired memory architecture, *The Missing Knowledge Layer*, OpenCog Hyperon, MemOS and Generative
Agents.
