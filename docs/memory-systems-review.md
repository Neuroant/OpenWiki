# Agent-memory systems — a comparative review

OpenWiki's memory tier (Path B, [`path-b-memory.md`](path-b-memory.md)) compared with other agent-memory
projects, one at a time: what each one does, how it differs from ours, what we can learn from it — and,
where a lesson can be checked cheaply against our own data, the check. Each project is read from its source
code and docs, not its marketing, and measured against the same yardstick.

**The yardstick:** purpose and runtime · store and data model · capture (write path) · retrieval (read
path) · time and contradictions · consolidation · forgetting and hygiene · identity and procedural memory ·
agent writes · surfaces and sharing · evaluation.

**The summary** of the series — all twelve at a glance, what separates them, where they agree, where OpenWiki
stands, and the plan — is in [`agent-memory-summary.md`](agent-memory-summary.md).

## Contents
1. [waku-agent](#1-waku-agent) (reviewed 2026-10-03)
2. [Zep / Graphiti](#2-zep--graphiti) (reviewed 2026-10-03)
3. [Mem0](#3-mem0) (reviewed 2026-10-03)
4. [Letta (MemGPT)](#4-letta-memgpt) (reviewed 2026-10-03)
5. [Cognee](#5-cognee) (reviewed 2026-10-03)
6. [LangMem](#6-langmem) (reviewed 2026-10-03)
7. [Hindsight](#7-hindsight) (reviewed 2026-10-03 — first of the world-model shortlist)
8. [MIRIX](#8-mirix) (reviewed 2026-10-03)
9. [AriGraph](#9-arigraph) (reviewed 2026-10-03)
10. [Nemori](#10-nemori) (reviewed 2026-10-03)
11. [memory-champ](#11-memory-champ) (reviewed 2026-10-03)
12. [Hermes Agent](#12-hermes-agent) (reviewed 2026-10-04)
13. [A design report: predictive world models through transfer entropy](#13-a-design-report-predictive-world-models-through-transfer-entropy)
    (reviewed 2026-10-04 — a proposal, not a system)
- [Across the series — what it suggests for OpenWiki](#across-the-series--what-it-suggests-for-openwiki)
- [Candidates — world models and CoALA](#candidates--world-models-and-coala)

---

## 1. waku-agent

*Source: [github.com/ShenSeanChen/waku-agent](https://github.com/ShenSeanChen/waku-agent) at `24b4cbb`
(2026-10-02). MIT, except `hosted/` (Elastic License 2.0). Python ≥ 3.11, `pip install waku-agent`.*

**What it is.** A personal assistant (terminal, a browser dashboard, a voice wake word, Telegram / Discord /
WhatsApp) written as a readable teaching blueprint around four pillars — harness, loop (~95 lines),
memory, eval/LLM-ops. It uses cloud models by default (Anthropic, OpenAI, Gemini, OpenRouter, … one key)
and keeps its state in one SQLite file, `~/.waku/state.db`. A companion hosted service, **Waku Memory**
(waku.one), shares one memory across Claude Code, Codex, Grok Bot and Waku over MCP.

### How its memory works
- **Semantic:** a `facts` table — a subject plus one self-contained sentence — searched with SQLite FTS5
  (BM25 keyword ranking, no embeddings), top 4. A `FactStore` protocol swaps in Supabase pgvector, Mem0,
  Zep or LangMem, all held to one conformance suite.
- **Episodic:** one dated sentence per consolidation ("2026-07-10: planned the Acme demo with Alex"),
  ranked by keyword relevance, then recency.
- **Procedural:** `SKILL.md` files (the Anthropic Agent Skills format), matched by keyword overlap with each
  skill's name and description; a skill's body enters the prompt only on a match. The agent can write new
  skills (`create_skill`), and `waku skill export` carries them to Claude Code and Codex.
- **Identity:** a `SOUL.md` persona plus the current local time in every prompt; `update_soul` lets the
  agent append "learned rules" (append-only for the agent; a human rewrites in the dashboard).
- **Retrieval gate:** before a turn touches memory, a small model answers "does this message need the
  user's memory?" and writes the search query. It fails open (any error → retrieve).
- **Slot gate** (opt-in, an external classifier, Jev): scores each retrieved fact on "how much does leaving
  it out change the answer?" (kept at ≥ 0.5) and each proposed fact on "would a later answer need it?".
- **Consolidation:** every 6 exchanges, one small-model call distills the unconsolidated chat log into facts
  and one episode. Loss-safe: if the call fails, the log stays unconsolidated for next time.
- **Agent edits:** `manage_memory` searches, updates and deletes facts and episodes in place.
- **Mirrors:** `MEMORY.md` is regenerated after every turn, and each fact is also written as its own file
  (Claude Code's memory layout), then synced to Waku Memory under a scope (`global` or `project:…`).
- **Evaluation:** deterministic (0/1) and LLM-judge suites kept strictly apart, and a release gate that needs
  100 % of the first and a threshold on the second; every call's tokens go to a permanent spend ledger.
  Memory-specific evals are still "proposed" (recall across sessions; the gate must *not* fire on an
  unrelated question). A lab runs Mem0, Zep, LangMem and pgvector each on its own terms with three
  sentences, the third contradicting the second ("our launch is in May" → "actually, it moved to June"),
  then asks exactly, paraphrased and in Chinese.

### Side by side

| | waku-agent | OpenWiki (Path B) |
|---|---|---|
| Purpose | a personal assistant (calendar, notes, messages) behind many gateways | the memory under a coding agent (Claude Code), next to a document wiki |
| Models | cloud by default, any provider | local only (Ollama: a 30B chat model + bge-m3) |
| Store | SQLite: facts, episodes, chat log | Kuzu graph: subject–predicate–object assertions under sessions, themes, the wiki graph |
| Capture | every 6 exchanges: facts as sentences + one episode | at session end / compaction, or backfilled: facts with valid-from date, cardinality and source |
| Retrieval | gated; keyword top 4 (+ optional slot gate) | every prompt; embedding similarity × confidence × recency, 16 facts in 3,000 chars |
| Time and contradictions | none in its own store — both launch dates stay retrievable (its docs say Zep and LangMem do better) | valid + transaction time, supersession, as-of / known-at, attribute resolution, a coexistence check |
| Consolidation | chat log → facts (that *is* its capture) | facts → summarized themes, incremental |
| Forgetting and hygiene | deletion by agent or human; no poisoning defense in the memory code | policy-based forgetting (`sleep`), source tags, a source-independent unsafe-instruction policy |
| Identity | `SOUL.md`, rules the agent can append | the project's identity text |
| Procedural memory | `SKILL.md` skills, written by the agent, exportable | out of scope (the host owns skills) |
| Agent writes | in-place update / delete | `wiki_remember`: the new state + the facts it replaces, closed, not deleted |
| Sharing | one hosted memory across agents (MCP), scoped | per project, local |
| Human view | `MEMORY.md`, one file per fact, dashboard tabs | the Gedächtnis tab, Analyse → Dynamik |
| Evaluation | deterministic + judge suites, a release gate; memory evals proposed | cross-session, temporal, poisoning and cue-trigger sets; LoCoMo (overall J 60.7 %) |

### What we learn
1. **A memory gate — skip memory when a turn doesn't need it.** The idea most relevant to us: our hook
   injects ≈ 710 tokens into every prompt, and in the v0.96 data 16 of 80 judged prompts got no helpful fact
   among the top 16 (§13.12). We checked the cheap version offline on those 80 prompts — skip the context
   when the best recall score is below a threshold — and **it does not work**: the top scores of prompts
   with nothing helpful (0.53–0.63) overlap those with helpful facts (from 0.54, median 0.62). At a threshold
   of 0.58 it skips 24 prompts, only 9 of them useless, and loses 64 helpful facts. A per-fact cutoff below
   the top score fares no better: within 0.08 of the top it keeps 70 % of the facts and 74 % of the helpful
   ones. Similarity scores in bge-m3's crowded space can't make this call; it needs a semantic judgment, as
   waku makes with a small model. On our side that is a local LLM call per prompt — the cost that keeps
   constraint probes off by default — or a small dedicated classifier. Open; worth measuring only with a
   small, fast model.
2. **Episodic summaries.** One dated sentence per session gives "what happened when" a home that
   subject–predicate–object facts lack, and LoCoMo's temporal category (45.8 %) is our weakest. Cheap to
   test: one sentence per existing session, added to the assembled context, the temporal questions
   re-answered paired. (Not the same as the D14 episodic *capture style* — more facts per session — which
   was measured and not adopted.)
3. **A readable mirror.** `MEMORY.md` and one file per fact make memory greppable, diffable and portable
   to other agents. An `owiki memory export` (current facts with their validity, plus themes) would be cheap
   and would let the memory travel into git or another agent's memory.
4. **Standing user rules in the identity tier.** `update_soul` keeps a user's standing instructions in the
   persona, which is always in context; ours are ordinary facts that must win recall to appear. A small slot
   for user-stated conventions next to the identity would fix that — but it is exactly the persistence path
   the P0 policy guards (standing authorizations), so it would have to pass the poisoning set first.
5. **The three-sentence contradiction probe.** A compact, vivid check of what B7 does (May → June closes
   May; asked exactly, paraphrased and in another language). Our temporal set covers it more thoroughly; the
   probe makes a good demo.
6. **Eval hygiene.** Deterministic and judged evals kept apart, and a release gate over both: we do this
   informally; a recorded gate (the test suite + the regression sets before a version bump) would make it a
   rule.

### What we would not adopt
- **Keyword-only retrieval as the default.** Its docs name the weakness themselves (paraphrases, other
  languages); dense recall with bge-m3 handles German and paraphrase.
- **In-place edits of facts by the agent.** They lose history; `wiki_remember` closes the old fact instead.
- **A row store without contradiction handling** — the gap waku's own lab demonstrates.
- **Cloud models and a hosted shared memory by default.** OpenWiki stays local-first. Sharing one memory
  across agents is the one capability we lack, and its privacy price is why.
- **Skills inside the memory store** — procedural memory belongs to the host.

**In short.** OpenWiki is further along on time, contradictions and their history, on hygiene against
poisoning, on themes, and on measured results against an external benchmark. waku is further along on
product surface (gateways, voice), on sharing memory across agents, on procedural memory, on the per-turn
memory gate, and on keeping memory types apart (facts, episodes, skills, persona) where we fold everything
into one fact store.


---

## 2. Zep / Graphiti

*Sources: [github.com/getzep/graphiti](https://github.com/getzep/graphiti) at `3c42764` (2026-09-30),
`graphiti-core` 0.30.2, Apache-2.0, Python ≥ 3.10; the paper "Zep: A Temporal Knowledge Graph Architecture for
Agent Memory" (Rasmussen et al., [arXiv 2501.13956](https://arxiv.org/abs/2501.13956), January 2025). Zep is the
managed commercial service built on Graphiti (its own graph engine, users and threads, sub-200 ms retrieval);
Graphiti is the open-source framework reviewed here.*

**What it is.** A framework that builds a temporal "context graph" from a stream of episodes (messages, text,
JSON): entities as nodes with summaries that evolve, facts as edges between two entities with a validity window,
the raw episodes as provenance, and communities. Backends: Neo4j, FalkorDB, Amazon Neptune, and Kuzu
(deprecated, below). It works with any model but is built for models with structured output (OpenAI by default);
Ollama works through an OpenAI-compatible client, with the warning that small models break the JSON schemas.
Usage telemetry is on by default (opt-out).

### How its memory works
- **Episodes:** the raw input, stored verbatim with a reference time (`valid_at`); every derived entity and fact
  lists the episodes it came from. Optional *sagas* chain episodes into threads, each with an incrementally
  merged "knowledge brief" in which newer facts win.
- **Write path, per episode:** several LLM calls. (1) Extract entities, with the last 10 episodes as context and
  optional Pydantic entity types as a prescribed ontology. (2) Resolve them against existing entities — exact and
  MinHash-LSH fuzzy matching on names, an LLM for the rest. (3) Extract facts between entity pairs: a relation
  type, a paraphrased fact sentence, and `valid_at` / `invalid_at` resolved from relative expressions against the
  reference time. (4) **One call per new fact**, shown two candidate lists found by hybrid search — existing facts
  between the same two entities (duplicates?) and the most similar facts anywhere (contradicted?) — returning both
  verdicts. (5) Update the entity summaries.
- **Time:** bi-temporal — `valid_at` / `invalid_at` in the world, `created_at` / `expired_at` in the system. A
  contradicted fact is closed at the new fact's `valid_at`; a new fact older than a contradicting one is closed
  itself, so out-of-order ingestion lands in history. Nothing is deleted.
- **Retrieval:** hybrid search over facts, entities, episodes and communities — cosine + BM25 + breadth-first
  search from given entities — reranked by reciprocal rank fusion, MMR, a cross-encoder, graph distance to a
  center entity, or episode-mention counts; 10 facts by default. All four timestamps can be filtered, but the
  default search does **not** exclude invalidated facts: it returns them with their window, and the context
  template tells the model a fact holds only between its dates.
- **Communities:** label propagation over the entity graph with map-reduce summaries; a new entity joins the
  community most of its neighbors belong to, and a full rebuild re-clusters.
- **Agent access:** an MCP server (`add_memory`, `search_nodes`, `search_memory_facts`, `delete_entity_edge`,
  `delete_episode`, `add_triplet`, `build_communities`, `summarize_saga`, `clear_graph`, …) and a REST service.
- **Evaluation:** a graph-building eval on the LongMemEval oracle set (one episode per message). The paper reports
  DMR 94.8 % vs MemGPT 93.4 %, and on LongMemEval accuracy up to +18.5 % with 90 % lower latency than a
  full-context baseline. LoCoMo is publicly disputed: Zep blogged 84 %; Mem0's CTO showed that adversarial
  questions had been counted in the numerator but not the denominator and recomputed **58.44 % ± 0.20**
  ([zep-papers #5](https://github.com/getzep/zep-papers/issues/5), 2025-05-08, still open); Zep reported
  **75.14 % ± 0.17** J against Mem0's 66.88 % (68.44 % with its graph) and ≈ 73 % for full context, and criticized
  LoCoMo itself — it fits in a context window, tests no knowledge updates, and has data-quality errors
  ([Zep blog](https://www.getzep.com/blog/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/),
  2025-05-06).

### Side by side

| | Graphiti / Zep | OpenWiki (Path B) |
|---|---|---|
| Purpose | a framework (and a managed service) for agent context graphs, partitioned by `group_id` | the memory under a coding agent, next to a document wiki, per project |
| Models | hosted models with structured output by default; Ollama possible, small models fragile | local only (a 30B chat model + bge-m3) |
| Store | Neo4j / FalkorDB / Neptune (Kuzu deprecated): entities, facts as edges, episodes, communities, sagas | Kuzu: reified subject–predicate–object assertions under sessions, themes |
| Unit of memory | a fact *between two entity nodes*, plus per-entity summaries | a self-contained fact; subject and object are strings, not linked entities |
| Capture | per episode (often per message): five or more LLM calls, one per new fact | per session: one capture call + lazy merge checks (attribute resolution, coexistence) |
| Time | bi-temporal; invalidation when an LLM finds a contradiction among similar facts | bi-temporal; invalidation by a valid-time plan within an attribute group, a coexistence check |
| Retrieval | hybrid (cosine + BM25 + graph search), five rerankers; history included, with windows | cosine × confidence × recency; current facts only (as-of / timeline on request); a budgeted three-tier context |
| Consolidation | entity summaries, label-propagation communities, saga briefs | warm-start Louvain themes over facts |
| Forgetting and hygiene | none — facts are invalidated, never forgotten; no injection defense found | policy-based forgetting (`sleep`), source tags, an unsafe-instruction policy, decay |
| Provenance | raw episodes stored; each fact lists its episodes | a session id per fact; transcripts stay outside the graph |
| Ontology | prescribed (Pydantic entity and edge types) or learned | free-form facts (the wiki side has a configurable entity ontology) |
| Agent writes | MCP `add_memory`, `add_triplet`, deletes | `wiki_remember` (closes, never deletes) |
| Evaluation | LongMemEval graph-building eval; DMR + LongMemEval in the paper; LoCoMo disputed (58–84 %) | cross-session sets; LoCoMo 60.7 % J (local judge, audited ≈ 7 points generous) |

### What we learn
1. **Our graph database is archived upstream.** Graphiti deprecates its Kuzu driver because "the upstream Kuzu
   project is no longer maintained": Kùzu Inc. archived the repository on 2025-10-10, and its team later joined
   Apple ([gdotv](https://gdotv.com/blog/kuzu-legacy-embedded-graph-database-landscape/),
   [HN](https://news.ycombinator.com/item?id=45560036)). OpenWiki runs on Kuzu 0.11. It works, but there will be no
   fixes, no security updates and no new wheels — the missing Python 3.14 wheel on Windows fits that picture.
   Successors include **LadybugDB**, a community fork of Kuzu that is actively developed, and **FalkorDBLite**,
   an embedded, Python-first engine of a different lineage. This belongs in the arc42 risk list, with a spike:
   does LadybugDB run our schema and Cypher (the vector index, shortest paths, `list_transform`)?
2. **Show history, not only the present.** Graphiti hands the answering model superseded facts with their
   validity window; we filter to current facts. For time questions ("when did X change", "what did Y use before")
   the history may be exactly what is needed. Cheap to test on LoCoMo's temporal questions (45.8 %, our weakest
   category): re-answer with recall that includes closed facts, marked with their intervals, paired on the same
   graphs — plus the temporal set. On the live path stale facts would be distractors, so perhaps only for
   time-shaped questions.
3. **Keep the specifics in each fact.** Graphiti's extraction prompt is visibly tuned on LoCoMo-style dialogue
   ("Nate plays games on a Gamecube"): never generalize "Gamecube" to "gaming console", keep every proper noun,
   number, brand and color. That is a sharper form of D14's goal that does not double the facts — our durable
   prompt may paraphrase specifics away. A capture-only probe first (do the gold answers survive in the facts?),
   then a paired LoCoMo run.
4. **Entities as first-class nodes.** Every Graphiti fact hangs between two resolved entities, each with a
   summary that evolves, which enables graph search (breadth-first from the entities in a query, reranking by
   graph distance) and "everything about X" in one hop. Our memory facts carry subjects as strings: attribute
   resolution (B9) groups paraphrases of one property and themes group topics, but nothing joins "Kuzu" across
   facts. A larger design step, close to B8 (spreading activation); worth keeping in mind, not starting now.
5. **Contradiction candidates by similarity across all facts.** One call decides duplicates and contradictions
   among the most similar facts anywhere, not only within an attribute key — fewer kinds of calls than our
   resolve-then-coexist path, but exactly the kind of local-model judgment that failed us before; with a 30B it
   would need its own measurement.
6. **A full-context baseline.** Zep reports ≈ 73 % for full context on LoCoMo (GPT-4o-mini) — the reference every
   memory system claims to beat. Our LoCoMo harness has no full-context condition. Adding one (each conversation
   is 16–26 k tokens, so it needs a 32 k context on the local model) would tell whether our memory beats simply
   pasting the conversation, with our model.
7. **Benchmark numbers swing by 15–25 points with harness choices.** The Zep / Mem0 dispute (84 → 58.44 vs
   75.14) came from category handling, prompts and judge setup. Our practice — categories 1–4, paired comparisons,
   a judge audit — is the right one; vendor numbers are not comparable with ours and should never sit next to
   them without that caveat.
8. **Episode threads with merged briefs (sagas)** make two reviewed projects with an episodic summary tier
   (waku: one dated sentence per session), which strengthens the case for testing episode summaries (§1,
   lesson 2).

### What we would not adopt
- **Several LLM calls per message, one per new fact.** Affordable with a hosted model, not with a local 30B on a
  12 GB GPU; one capture call per session with lazy checks is the local-first trade-off.
- **Unfiltered retrieval as the default.** A superseded fact returned without a filter (waku's lab saw exactly
  that with Zep) is a risk in a context injected into every prompt; history on request (as-of, timeline), or
  only for time-shaped questions.
- **A graph server** (Neo4j, FalkorDB): OpenWiki stays embedded. **Telemetry on by default.**
- **No forgetting and no poisoning defense** — both proved necessary on real data (31 % junk in the injected
  context before `sleep`; injections that launder their own provenance).

**In short.** Graphiti is the closest relative of our memory tier: B7 was designed after the same bi-temporal
idea, and both keep history instead of deleting it. Graphiti is further along on the entity graph and its
summaries, on hybrid retrieval with graph search and rerankers, on prescribed ontologies, on provenance back to
raw episodes, and on production scale. OpenWiki is further along on cost per session with a local model, on
forgetting and hygiene against poisoning, on bounded context assembly, and on audited measurement. And its README
surfaced the most urgent finding of this review series so far: Kuzu, our graph database, is archived.


---

## 3. Mem0

*Sources: [github.com/mem0ai/mem0](https://github.com/mem0ai/mem0) at `abb81c8` (2026-10-01), `mem0ai` 2.2.1,
Apache-2.0, Python ≥ 3.10; the paper "Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory"
(Chhikara et al., [arXiv 2504.19413](https://arxiv.org/abs/2504.19413), April 2025); the
[research page](https://mem0.ai/research) and the [benchmark repository](https://github.com/mem0ai/memory-benchmarks)
for the April 2026 algorithm. Mem0 ships as an open-source library, a self-hosted server and a hosted platform; this
review reads the library and its Claude Code plugin.*

**What it is.** A memory layer for assistants and agents — user, session and agent memories behind `add` and
`search` — with some twenty vector-store backends, a CLI and plugins for Claude Code, Codex, Cursor, OpenCode and
others. It defaults to OpenAI models (`gpt-5-mini`, `text-embedding-3-small`). In April 2026 it replaced its
best-known design — an LLM deciding ADD / UPDATE / DELETE / NONE for each new fact against similar old ones — with
**single-pass, add-only extraction**: "memories accumulate; nothing is overwritten".

### How its memory works
- **Write path, one LLM call:** the new messages, the last 10 messages of the session, the 10 most similar
  existing memories (for deduplication and links) and an *observation date* go into one extraction call. Out come
  **self-contained, contextually rich memories of 15–80 words**: motivations, feelings and who was present kept in;
  relative dates resolved to absolute ones ("the week of May 15, 2023"); numbers exact; facts mentioned in passing
  inside a request extracted too; recommendations the assistant gave; photo descriptions. A change is one memory
  with both states: "User switched from almond milk to oat milk lattes after developing an almond sensitivity". Then
  an MD5 hash drops exact duplicates, each memory is embedded and lemmatized for BM25, a history table logs the ADD,
  and spaCy named entities are linked across memories through an entity store (exact or ≥ 0.95 similar names).
  The pipeline never updates or deletes; an explicit `update` / `delete` API and a per-memory `expiration_date`
  exist.
- **Retrieval:** semantic search over-fetches (max(4 × limit, 60)), BM25 over the lemmatized texts (sigmoid-
  normalized, adapted to query length), and an entity boost for memories linked to entities named in the query
  (≤ 0.5, smaller for entities linked to many memories); the three signals add up, after a threshold on the semantic
  score.
- **Time:** in the open-source library a memory carries only `created_at`. Event time on `add` (`timestamp`), a
  reference date on `search` and search-time decay are **platform-only** — the library raises an error for the
  first two and points to the hosted platform. There are no validity intervals and no supersession: a change lives
  in the wording of a transition memory, and the answering model sorts it out.
- **Procedural memory:** a verbatim summary of an agent's execution history, for long-running agents.
- **Claude Code plugin:** hooks record prompts, answers, changed file paths and failed commands locally without a
  model call; a detached worker sends a batch every 5 exchanges (sooner for large ones, after 5 idle minutes, and
  at session end) to the hosted platform. Extraction sorts memories into **shared project memory** (per repository:
  conventions, decisions, constraints, working commands, failed commands with their fixes) and **personal memory**
  (preferences), with categories (`project_knowledge`, `decisions_and_constraints`, `workflows`,
  `problems_and_fixes`, `results`). **Recall is automatic only on a session's first prompt** (≥ 20 characters, up to
  5 memories); after that Claude searches through an MCP tool (3 results by default, ≤ 4,000 characters).
  Credential-shaped values (bearer tokens, `sk-…` keys, AWS and GitHub tokens, private keys, JSON secrets) are
  redacted before anything is sent. Telemetry goes out under the account's email address.
- **Evaluation:** the 2025 paper reports LoCoMo J 66.88 % (68.44 % with graph memory), a 26 % relative gain over
  OpenAI's memory, and 91 % lower p95 latency and > 90 % fewer tokens than full context. The April 2026 algorithm
  reports **LoCoMo 92.5** (single-hop 94.6, multi-hop 95.4, open-domain 82.3, temporal 92.5; 1,540 questions, i.e.
  categories 1–4) at a top-200 retrieval budget of ~6,900 tokens, LongMemEval 94.4 and BEAM 64.1 (1 M tokens) /
  48.6 (10 M) — with gpt-4o answering and judging and gpt-4o-mini extracting, on the hosted platform, which the
  README says "includes proprietary optimizations not available in the open-source SDK". The benchmark repository
  reports each score at top 10 / 20 / 50 / 200.

### Side by side

| | Mem0 | OpenWiki (Path B) |
|---|---|---|
| Purpose | a memory layer for assistants and agents (library, server, platform), plugins for many coding agents | the memory under a coding agent, next to a document wiki, per project |
| Models | OpenAI by default, any provider | local only (a 30B chat model + bge-m3) |
| Store | any of ~20 vector stores + SQLite history + an entity store | Kuzu: reified subject–predicate–object assertions under sessions, themes |
| Unit of memory | a contextually rich sentence (15–80 words) | an atomic fact with validity interval, cardinality, source |
| Capture | one add-only call per batch of messages, with similar memories as context | one call per session, then a valid-time merge with lazy checks |
| Time | `created_at` only in the library; event time, reference dates and decay are platform features | bi-temporal: valid + transaction time, supersession, as-of / known-at |
| Contradictions | none — transitions are written into the memory text; the reader resolves | an older value is closed when a newer one replaces it (history kept) |
| Retrieval | semantic + BM25 + entity boost, additive | cosine × confidence × recency (dense only) |
| Live injection (Claude Code) | up to 5 memories on a session's first prompt, then agent-driven search | 16 facts (≈ 710 tokens) on every prompt |
| Hygiene | credential redaction in the plugin; no poisoning or forgetting policy in the library | an unsafe-instruction policy, policy-based forgetting, source tags; no credential redaction |
| Scope | shared project memory vs personal memory, categories | one memory per project |
| Evaluation | LoCoMo, LongMemEval, BEAM (open harness, platform numbers, gpt-4o) | cross-session sets; LoCoMo 60.7 % J (local 30B, audited ≈ 7 points generous) |

### What we learn
1. **Inject once per session, then let the agent pull.** Mem0's Claude Code plugin gives up to 5 memories on the
   first prompt and leaves the rest to an on-demand search tool; our hook injects ≈ 710 tokens into every prompt.
   That is a third answer to the same cost, after waku's model-judged gate and the score threshold that failed us
   (§1) — a structural one that needs no judgment. Cheap to offer as a project setting (inject on every prompt, or
   on the first prompt and after compaction, with `wiki_memory` for the rest). The token saving is certain; whether
   the agent pulls memory when it needs it can only be observed in real sessions.
2. **Redact credentials before capture.** The plugin strips credential-shaped values before anything leaves the
   machine. Our capture is local, but a key pasted into a session could become a fact that is injected into every
   later prompt and listed in the Gedächtnis tab: the P0 policy blocks instructions to *send* secrets, not secret
   values themselves. Pure, deterministic and cheap — the same patterns applied to the transcript before capture
   and to facts in `remember()` as the last line of defense. A clear gap.
3. **Add-only extraction, reasoning at read time.** Mem0 dropped its model's UPDATE / DELETE decisions; the README
   credits the new algorithm with +21 points on LoCoMo (alongside other changes). It matches our experience that
   model judgments of memory hurt, yet we still make two at write time (attribute resolution, the coexistence
   check). Testable as an ablation: replay the captured LoCoMo facts we already saved into fresh graphs with every
   fact kept current, re-answer, paired — does our write-time temporal logic help or hurt there?
4. **Rich memories, one transition per memory.** The old state, the new state and the reason in one sentence.
   Our triples are atomic and drop motivations, feelings and who was present — what open-domain questions ask
   (45.8 %). This is *richer* facts, not more of them (more, D14, did not pay off): a capture probe on gold-answer
   coverage, then a paired LoCoMo run.
5. **Hybrid recall.** BM25 and entity matches next to the embedding catch names, titles and exact terms that dense
   recall blurs. Our own wiki search showed it (hybrid wins on identifier-heavy text, `RAG-vs-GraphRAG.md`
   Finding 4), yet memory recall is dense-only. Cheap: `lexical.BM25` over the fact texts fused with the dense ranking;
   testable by re-answering LoCoMo, paired.
6. **Bigger retrieval budgets on the benchmark.** Mem0 reports scores at top 10 / 20 / 50 / 200 and headlines
   top-200. Our k 10 → 20 gave +5.7 points; k 40–50 is a cheap re-answer on LoCoMo (the live path keeps its budget).
7. **Project and personal scope, and categories.** Shared repository memory (conventions, decisions, failed commands
   with their fixes) apart from personal preferences, each memory tagged with a category. "Problems and fixes" is a
   kind of memory our capture may underweight, and categories would let the context put decisions and constraints
   first.
8. **Incremental capture.** A batch every 5 exchanges or after an idle spell, so memory lands during a long session
   and survives a crash; ours waits for session end or compaction.

### What we would not adopt
- **The platform split.** Event time, reference dates, decay and the headline numbers belong to the hosted platform;
  the open library has none of them. Telemetry under the account's email.
- **No validity model at all.** With gpt-4o reading, transition sentences may suffice; with a local 30B and memory
  injected into every prompt, keeping stale states out matters more — but lesson 3 is how to find out.
- **spaCy as a dependency** for entity matching — BM25 over the fact texts first.

**In short.** Mem0 is the most benchmark-driven of the projects reviewed so far: its open-source library is a
deliberately simple add-only vector memory with hybrid retrieval, while its temporal features and headline numbers
live in the hosted platform. Its numbers (gpt-4o answering and judging) are not comparable with ours. What it
teaches is concrete and cheap to test: inject once and let the agent pull, redact credentials, fuse BM25 into
recall, try larger benchmark budgets, write richer facts, and check whether our write-time temporal logic earns its
place.


---

## 4. Letta (MemGPT)

*Sources: [github.com/letta-ai/letta](https://github.com/letta-ai/letta) — now a landing page: the Letta V1 Python
server is retired, unsupported and kept on an `archive` branch; current development is
[github.com/letta-ai/letta-code](https://github.com/letta-ai/letta-code) at `0828add` (2026-10-02),
`@letta-ai/letta-code` 0.34.2, Apache-2.0, TypeScript (Node ≥ 22.19). Background: "MemGPT: Towards LLMs as
Operating Systems" (Packer et al., [arXiv 2310.08560](https://arxiv.org/abs/2310.08560), October 2023);
"Sleep-time Compute" (Lin et al., [arXiv 2504.13171](https://arxiv.org/abs/2504.13171), April 2025); the Letta blog
post ["Benchmarking AI Agent Memory: Is a Filesystem All You Need?"](https://www.letta.com/blog/benchmarking-ai-agent-memory/)
(2025-08-12).*

**What it is.** MemGPT (2023) treated the LLM as an operating system for its own context: a small main context
with editable memory blocks (persona, human), plus external *recall* storage (the conversation history) and
*archival* storage (a vector store) that the agent pages in and out through function calls; it also introduced the
DMR benchmark. Letta, the company behind it, has since turned it into **Letta Code**: a stateful agent harness
(CLI, desktop app, browser, Slack / Telegram / Discord) whose agents keep memory, identity and skills across
sessions and machines, rewrite their own context, and "dream" in the background. It runs against Letta Cloud by
default or a local backend, with the user's own model keys.

### How its memory works
- **MemFS — memory as a git repository of Markdown files.** A root `MEMORY.md` index; *core* files at the root,
  always in the system prompt (each with `name` and `description` frontmatter); *deferred* memory in child
  directories, which the agent sees only as a tree with descriptions and reads on demand; `skills/` as procedural
  memory; `ARCHIVE.md` for retired context with dated entries. The repository can sync to GitHub. A pre-commit
  validator enforces caps per file and for all core memory, a maximum depth, and read-only files.
- **Writes.** The primary agent edits its memory files directly. A background *memory* subagent applies requested
  updates in a private git worktree whose commits the harness merges (conflicts resolved by reading both sides).
  The *reflection* subagent — "dreaming", the successor of sleep-time agents — runs every N steps or at context
  compaction (off by default on native Windows). It reads recent transcripts (or several, with already-reflected
  "replay" slices for cross-session patterns) and ranks learnings: **mistakes and corrections first**, then
  preferences, new facts, contradictions, reusable procedures. It filters out ephemeral details, converts relative
  dates to absolute ones, **fixes a contradicted entry at its source** instead of appending the new version,
  archives retired context, maintains skills (update / extend / deprecate / split / create, preferring "none"),
  and commits with a structured message. Its changes apply automatically, or wait for the agent's review.
- **Retrieval.** No vector recall of facts: core memory is always in context; deferred files and skills are opened
  by the agent from their names and descriptions; a message search covers all conversations, including other
  agents'.
- **Time.** Git history is the record of every memory change; facts themselves carry no validity intervals.
- **Hygiene.** The prompts forbid persisting secrets and ephemeral logs; read-only files protect what must not
  change; a Secrets feature (cloud) hides secret values from the context.
- **Evaluation.** A gpt-4o-mini agent given the raw LoCoMo conversations as files, with `grep`, semantic file
  search, `open` and `close`, searching iteratively, scored **74.0 %** — above Mem0's 68.5 % — from which Letta
  concludes that memory "is more about how agents manage context than the exact retrieval mechanism". The
  sleep-time compute paper reports ~5× less test-time compute at equal accuracy and up to +13 % / +18 % accuracy on
  stateful math benchmarks when a model works through a context before the questions arrive.

### Side by side

| | Letta Code | OpenWiki (Path B) |
|---|---|---|
| Purpose | a stateful agent harness (a coding agent and always-on assistant) whose agent owns its memory | the memory under someone else's coding agent, next to a document wiki |
| Models | the user's models (frontier by default), Letta Cloud or local backend | local only (a 30B chat model + bge-m3) |
| Store | Markdown files in git (core, deferred, skills, archive) | Kuzu: subject–predicate–object assertions under sessions, themes |
| Who curates | the agent itself + a dreaming subagent with shell and edit tools | a capture prompt + deterministic merge and hygiene rules |
| Capture trigger | every N steps or at compaction (+ on request) | session end / compaction (+ backfill, `wiki_remember`) |
| Contradictions | the stale entry is edited at its source; git keeps the old version | the old fact is closed (valid-time interval); the graph keeps it |
| History | git log (who, when, why) | bi-temporal: valid + transaction time, as-of / known-at |
| Retrieval | core always in context; the agent opens deferred files; message search | similarity recall, 16 facts + themes injected per prompt |
| Raw conversations | searchable (recall memory) | not kept in the memory tier |
| Procedural memory | skills maintained by reflection | out of scope |
| Limits | caps enforced by a pre-commit hook | a char budget at context assembly |
| Evaluation | DMR (MemGPT); LoCoMo 74.0 % with an iterative filesystem agent (gpt-4o-mini) | cross-session sets; LoCoMo 60.7 % J (local 30B, audited ≈ 7 points generous) |

### What we learn
1. **Git as the history and export layer.** Letta keeps all memory as Markdown in git: readable, diffable,
   portable, every change a commit with its reason — so a contradiction can be fixed in place, because git keeps
   the old version. For us that is the natural shape of the memory export that risk R10 calls for: render the
   current facts (per theme or subject, with their validity) as Markdown into a git repository at every `sleep`,
   so history and portability stop depending on Kuzu, and a person can review, diff or revert. (waku's
   `MEMORY.md` mirror, §1 lesson 3, is the same idea.)
2. **An index instead of a fixed dose.** Letta's agent always sees a small curated core plus a tree of what else
   exists, and opens the rest itself. That is a fourth answer to the per-prompt cost, after waku's model gate, the
   score threshold that failed us and Mem0's first-prompt injection: inject the identity and the **theme labels as
   a table of contents**, and let the agent call `wiki_memory` for details. Our themes already form that index;
   today their summaries are injected only when recalled facts hit them.
3. **Iterative search beat extracted memory on LoCoMo.** Letta's 74.0 % came from an agent searching the raw
   conversations again and again, not from extracted memories. Two consequences: (a) keep the raw sessions
   searchable, as MemGPT's recall memory and Graphiti's episodes do — a session search tool would let an agent find
   what capture dropped; (b) an *agentic* answer condition in our LoCoMo harness — the local 30B with recall and
   transcript search as tools, a few iterations — would test whether iteration helps a 30B as it helps gpt-4o-mini.
   Our multi-hop and temporal losses are where it should show.
4. **Corrections first.** Letta's reflection ranks mistakes and corrections — user feedback, frustration, failed
   retries — above everything else. Our capture is tuned for durable project facts and decisions, yet corrections
   ("that was wrong because…", "don't do X here") are what a coding agent most needs so as not to repeat a mistake.
   A capture-rule change, measured on the cross-session sets — and checked against P0, so that corrections of the
   assistant's behavior stay distinguishable from injected instructions.
5. **Limits as code.** A pre-commit validator caps core memory and protects read-only files: size and identity
   integrity enforced mechanically, not by prompt. Our context budget is enforced at assembly; that `wiki_remember`
   cannot touch the identity tier is worth keeping that way.
6. **Reviewable memory updates.** Dreaming can stage its changes for review before applying them. Our agent writes
   already wait in the journal until the next fold — a natural review point we don't expose (a command that lists
   pending operations and lets a person drop one).

### What we would not adopt
- **An agent as the curator.** Letta's reflection is a capable agent with shell and edit tools rewriting files;
  it presumes a strong model. With a local 30B we measured four times that model judgments of memory hurt (path-b
  §13), which is why our curation is deterministic.
- **Free-form Markdown as the only store.** No machine-readable validity, no as-of queries, no per-fact
  confidence — we want the export, not a replacement.
- **Cloud by default.** Letta Cloud holds memory, identity and conversations unless the user opts for local; and
  automatic dreaming is off on native Windows, our platform.

**In short.** Letta has moved furthest from "memory as a database": memory is the agent's own context, kept as
files in git, curated by the agent and a dreaming subagent, with git as the history and the agent's own search as
retrieval. OpenWiki sits at the database end — structured facts, validity intervals, an assembled context — built
for a local model that cannot be trusted to curate. The two meet at three cheap ideas: a git-tracked Markdown
export (which also mitigates R10), a context that is an index plus a pull, and raw sessions kept searchable.


---

## 5. Cognee

*Sources: [github.com/topoteretes/cognee](https://github.com/topoteretes/cognee) at `b32d8af` (2026-10-01), `cognee`
1.6.2, Apache-2.0, Python 3.10–3.14; its BEAM evaluation report (`cognee/eval_framework/beam/REPORT.md`); the
[Claude Code plugin](https://github.com/topoteretes/cognee-integrations/tree/main/integrations/claude-code) README;
the paper "Optimizing the Interface Between Knowledge Graphs and LLMs for Complex Reasoning" (Markovic et al.,
[arXiv 2505.24478](https://arxiv.org/abs/2505.24478), 2025).*

**What it is.** A knowledge and memory engine for documents, code, conversations and agent sessions — "a company
brain" — behind four operations: `remember`, `recall`, `improve`, `forget`. It turns text into chunks, an
LLM-extracted entity–relationship graph, summaries and embeddings, and code into a graph of symbols. Its default
stack is embedded: SQLite, LanceDB for vectors, and a graph store that was **Kuzu and is now LadybugDB**. It runs
without an LLM key (local GLiNER extraction and local embeddings), with Ollama, or with hosted models; plugins exist
for Claude Code, Codex and OpenClaw, and an MCP server for other clients.

### How its memory works
- **Permanent memory:** `cognify` chunks each document, extracts entities and relationships with an LLM (optionally
  guided by an ontology), writes summaries, and embeds everything; a *global context index* organizes the local
  summaries into a tree with one root. An optional event graph extracts timestamped events.
- **Sessions:** turns are first stored as session memory. *Candidate lessons* carry a confidence and
  **helpful / harmful counts** from feedback that an LLM detects in later user turns; a lesson is served only while
  it has never been rated harmful and its confidence is ≥ 0.75. At the end, **session distillation** curates the
  session in batches (one curator call each), checks each proposed lesson against prior lessons for novelty, lets a
  writer accept or reject it with a reason ("why learned"), and cognifies the accepted lessons into the graph. The
  curator may not promote a claim that exists only in an assistant answer unless the user or a candidate backs it.
  A watermark makes sure a failed call never marks a session as distilled.
- **Retrieval:** some twenty strategies — chunks, BM25 over chunks, summaries, triplets, graph completion with
  decomposition or chain of thought, Cypher, natural language to Cypher, agentic completion, code, skills — and a
  router. The `TEMPORAL` strategy has an LLM extract the time window a question refers to ("in 2015", "before X",
  "now") and searches events within it.
- **Time:** no validity intervals on facts; time lives in the event graph and in that query-time window.
- **Portability:** **COGX**, a memory exchange format — a manifest plus one JSONL file per record kind: documents,
  episodes (turns with timestamps), entities (type, description, aliases), *facts* (subject, predicate, object,
  `valid_at` / `invalid_at`, confidence, provenance), Mem0-style memories and Letta-style memory blocks. Importers
  exist for Mem0, Zep / Graphiti, Letta and LangMem.
- **Claude Code plugin:** hooks capture prompts, tool traces and answers into session memory; memory is injected on
  **every prompt** (a 12 s budget, ≤ 12,000 characters); **before Claude reads a file, facts about that file are
  injected** (once per session); session-to-graph improvement runs after 60 idle seconds, every 150 tool calls, and
  at session end. Credentials, authorization values, database URLs and private keys are redacted by default, and
  paths such as `.env` are never captured. It runs against a local Cognee server by default.
- **Evaluation:** BEAM — **0.79** at 100 K tokens (one held-out conversation, 20 questions, four rounds) and
  **0.67** at 10 M, explicitly called exploratory because its question-type routing was tuned on the scored
  questions. The report also discusses overfitting to a fully public benchmark and says no ablations were run.
  Earlier HotpotQA comparisons are archived.

### Side by side

| | Cognee | OpenWiki (Path B) |
|---|---|---|
| Purpose | a knowledge + memory engine (documents, code, sessions), a "company brain" | the memory under a coding agent, next to a document wiki |
| Models | none needed (GLiNER + local embeddings), Ollama, or hosted | local only (a 30B chat model + bge-m3) |
| Store | SQLite + LanceDB + **LadybugDB** (formerly Kuzu) | Kuzu 0.11 (archived upstream, R10) |
| Unit of memory | chunks, entities, relationships, summaries; distilled session lessons | subject–predicate–object facts with validity |
| Capture | session turns → curator + writer calls → lessons → cognify | one capture call per session → valid-time merge |
| Time | an optional event graph; query-time windows | bi-temporal facts, as-of / known-at |
| Feedback | helpful / harmful counts per lesson; harmful ones withheld | confidence from re-affirmation; `wiki_remember` replacements |
| Retrieval | ~20 strategies + routing | similarity recall + themes, 16 facts per prompt |
| Live injection | every prompt (≤ 12,000 chars) + facts about each file Claude reads | every prompt (≤ 3,000 chars) |
| Hygiene | credential redaction, denied paths, an assistant-claim grounding rule | the unsafe-instruction policy, forgetting, source tags |
| Portability | COGX export / import, importers from four other systems | none yet (planned export, R10) |
| Evaluation | BEAM 0.79 (100 K, held out) / 0.67 (10 M, exploratory) | cross-session sets; LoCoMo 60.7 % J (local 30B) |

### What we learn
1. **The Kuzu → LadybugDB path, already walked.** Cognee runs `ladybug` 0.19.0 behind a `kuzu` compatibility
   package (`from ladybug import *` — the API is near-identical), reads each database's on-disk storage code
   (Kuzu 0.11.3 writes 39, Ladybug 0.19.0 writes 43), and migrates by running `EXPORT DATABASE` in a venv with the
   old engine and `IMPORT DATABASE` in the new one. The caveats it documents: storage-corruption fixes in 0.18.2 and
   0.19.1; 0.19.1 segfaulting mid-write in its CI (hence the pin on 0.19.0); no JSON extension published for
   0.19.1; Windows wheels that no longer bundle OpenSSL, so a DLL shim is needed or the first open fails with
   "Could not find lbug C API shared library"; macOS ≤ 14 limited to 0.17. **Ladybug publishes Windows wheels for
   Python 3.13 and 3.14** (checked with `pip download`), so the move would also lift our 3.13 ceiling. The R10 spike
   now has a map.
2. **Export in an existing exchange format.** Our assertions map onto COGX facts almost field for field — subject,
   predicate, object, `valid_from` → `valid_at`, `valid_to` → `invalid_at`, confidence, the session as provenance;
   sessions map to episodes, wiki entities to entities, and the rest (cardinality, source, attribute key,
   transaction times, forgotten) goes into metadata. An export to COGX makes the memory R10 needs to protect
   portable not only across engines but across memory systems — paired with the readable git-tracked Markdown view
   (§4).
3. **Memory triggered by the file being read.** Cognee's plugin injects facts about a file at the moment Claude
   reads it. Our dev memory is full of module-level facts ("`context_for` recalls 16 facts by default") that today
   surface only when a prompt's wording happens to match. A hook before file reads that recalls with the file's path
   or module name as the query is a structural trigger — like an index plus pull (§4), it needs no judgment.
4. **Credential redaction is standard among coding-agent plugins** — Mem0 and Cognee both do it (credentials,
   authorization headers, database URLs, private keys, plus denied paths like `.env`). It strengthens §3, lesson 2.
5. **Feedback on served memory.** Cognee withholds a lesson once it has been rated harmful. Our facts gain
   confidence only by being restated. The cheap version for us: `wiki_remember`'s `replaces` already marks a fact as
   wrong; a way for the agent to flag an injected fact as stale or irrelevant could lower its confidence without any
   model judgment.
6. **A grounding rule for assistant claims.** Cognee's curator never promotes a claim that only the assistant made.
   We store assistant-established facts freely (source `assistant`), and some have been wrong — the dev memory once
   held the assistant's mislabel of an arc42 section until `wiki_remember` corrected it. Worth measuring first: how
   many of the dev memory's wrong facts are assistant-only?
7. **Time in the question.** The `TEMPORAL` retriever extracts the window a question refers to; our recall ignores
   times in the query unless a caller passes `as_of`. For LoCoMo's temporal questions (45.8 %), extracting explicit
   dates, months and years from the question (rules first, the local model for the rest) and boosting facts whose
   validity overlaps the window is a cheap paired test.
8. **Honest reporting, and BEAM.** The BEAM report keeps a held-out score apart from an in-sample one and names the
   overfitting risk of a public benchmark — the standard we hold ourselves to. BEAM itself tests knowledge updates,
   contradiction resolution, event ordering, abstention and preference following — what B7 is built for, and what
   LoCoMo lacks (Zep's critique, §2). A candidate for our next external benchmark.

### What we would not adopt
- **The weight.** A relational, vector and graph database with migration chains, access control and a distributed
  mode, in some 2,500 Python modules.
- **A chain of curator and writer calls per session** — several model calls to curate memory, the pattern our local
  measurements argue against.
- **A per-prompt budget of 12,000 characters**, four times ours, measured nowhere in the plugin.

**In short.** Cognee is the most "platform" of the five systems: a knowledge engine over documents, code and
sessions, with a session layer that distills lessons, feedback counts, provenance, an exchange format and a
time-window retriever. For OpenWiki it turns out to be the most practically useful review so far: it has already
taken the Kuzu → LadybugDB path we face (R10), and COGX gives our planned memory export a ready-made target.


---

## 6. LangMem

*Source: [github.com/langchain-ai/langmem](https://github.com/langchain-ai/langmem) at `48e3c11` (2026-10-02),
`langmem` 0.0.30, MIT, Python ≥ 3.10, built on LangChain / LangGraph / trustcall. Recent activity is maintenance:
the last commits are dependency bumps and documentation fixes.*

**What it is.** A toolkit of memory primitives for LangGraph agents rather than a memory system: memory managers
that turn conversations into memories, tools an agent calls to manage and search its memory, prompt optimizers that
rewrite an agent's instructions, and a summarization node for short-term memory. Storage is LangGraph's `BaseStore`
— in process by default (memories are lost on restart) or Postgres — with namespaces such as `("memories",
"{user_id}")`.

### How its memory works
- **Memory types, cleanly framed:** *semantic* memory as a **collection** (many documents, searched at run time) or
  a **profile** (one schema-typed document per user, updated in place, always current); *episodic* memory as stored
  successful interactions; *procedural* memory as the agent's own instructions.
- **Memory manager:** one structured-extraction call sees the conversation plus the current memories and may insert
  new documents, patch existing ones (JSON patch) or delete them, for up to `max_steps` passes ending with a `Done`
  tool. Its default instructions ask it to extract with stated confidence ("p(x)"), consolidate and compress, remove
  incorrect or redundant memories, and draw conclusions by deduction, induction and abduction. A store manager first
  has a model write search queries to fetch the related existing memories (5 by default), and can run *phases* — for
  example extract, then consolidate — with different instructions.
- **Hot path vs background:** the agent can manage memory itself through tools during a conversation, or a
  background manager reflects afterwards. The `ReflectionExecutor` **debounces** per thread: each new submission
  cancels the pending one, so reflection runs once a conversation has been quiet for N seconds.
- **Procedural memory:** prompt optimizers (`gradient`, `metaprompt`, `prompt_memory`) propose a new system prompt
  from conversation trajectories and feedback.
- **Retrieval, time, evaluation:** semantic search and metadata filters through the store; no time model —
  contradictions are resolved by the model rewriting memories (waku's lab, §1: three sentences in, two memories out,
  "launch is scheduled for June (updated from May)"); no evaluation in the repository.

### Side by side

| | LangMem | OpenWiki (Path B) |
|---|---|---|
| Purpose | a toolkit of memory primitives for LangGraph agents | the memory under a coding agent, next to a document wiki |
| Models | hosted by default (OpenAI / Anthropic via LangChain) | local only (a 30B chat model + bge-m3) |
| Store | LangGraph `BaseStore` (in-memory default, Postgres) | Kuzu (archived upstream, R10) |
| Memory shapes | collections, typed profiles, episodes, prompts | facts with validity; themes; an identity string |
| Write path | a model inserts, patches and deletes documents, in several passes | one capture call, a deterministic valid-time merge |
| Contradictions | resolved by rewriting the memory (no history) | the old fact is closed and kept |
| Background | debounced reflection after N quiet seconds | capture at session end / compaction |
| Procedural memory | prompt optimizers rewrite the agent's instructions | out of scope |
| Evaluation | none in the repository | cross-session sets; LoCoMo 60.7 % J (local 30B) |

### What we learn
1. **A profile next to the collection.** A typed profile — one document that is always current and always in
   context — next to the searched collection. waku's learned rules and Letta's core files are the same idea: three
   of the six systems keep a small, curated, always-present core. Ours is only the hand-written identity string;
   user-stated conventions must win recall to appear. An automatically maintained core (user-sourced conventions,
   decisions with high confidence) is the candidate — subject to the poisoning set, as noted in §1.
2. **Debounced background capture.** Reflect once the conversation has been quiet for N seconds, each new message
   pushing it back. Mem0 (every 5 exchanges), Letta (every N steps), Cognee (after 60 idle seconds) and LangMem all
   capture *during* a session; we capture at session end or compaction only. With the detached worker and the
   journal already in place, an idle-triggered capture is mostly plumbing; deduplication absorbs the overlap.
3. **Procedural memory as proposed instruction edits.** LangMem's optimizers rewrite the agent's prompt from
   feedback. For a coding agent the procedural memory is `CLAUDE.md`. A command that *proposes* `CLAUDE.md` edits
   from remembered corrections and conventions would close that loop — proposals only: `CLAUDE.md` is the user's
   file.
4. **Stated uncertainty.** Memories can carry a probability in their text. Our confidence comes only from being
   restated; hedged statements ("we might switch to X") are captured like settled ones. A capture tag for hedged
   facts (lower starting confidence) is cheap — B7 already gives planned facts their own status.

### What we would not adopt
- **Model-driven rewrites and deletes** — the contradiction disappears into a parenthetical; no history, no as-of.
- **An in-process store by default**, and the LangChain / LangGraph / LangSmith stack as a dependency.
- **A library in maintenance mode** as a foundation.

**In short.** LangMem is a toolkit rather than a system — the clearest conceptual framing of the six (semantic
memory as profiles or collections, episodic, procedural; hot path vs background), but with no storage, time model or
evaluation of its own, and little recent development. Its lessons are conceptual: keep a small profile-like core
next to the facts, capture in the background once a conversation goes quiet, and treat `CLAUDE.md` as procedural
memory the system can propose edits to.


---

## 7. Hindsight

*Sources: [github.com/vectorize-io/hindsight](https://github.com/vectorize-io/hindsight) at `f7dd3f4` (2026-10-02),
`hindsight-api` 0.10.2, MIT, Python ≥ 3.11; the paper "Hindsight is 20/20: Building Agent Memory that Retains,
Recalls, and Reflects" (Latimer et al., [arXiv 2512.12818](https://arxiv.org/abs/2512.12818), December 2025); the
README of its coding-agents integration. The first project from the world-model shortlist below.*

**What it is.** An agent memory server — API and UI; Docker, pip, Kubernetes, or embedded in a Python process with
an embedded Postgres — with Python, TypeScript and Go clients, a CLI, an MCP server, integrations for dozens of
frameworks and about twenty coding agents. It works with 25+ model providers, local ones included (Ollama, LM
Studio, llama.cpp); a hosted cloud is optional. Memories live in **banks** — one per user, agent or project — each
with a *mission* (what to track) and *disposition* traits (skepticism, literalism, empathy) that shape how it
reasons.

### How its memory works
- **World facts and experiences, kept apart.** Retain extracts *world* facts (objective facts, including the user's
  preferences, rules and corrections) and *experiences* (what the agent itself did, tried, decided). Each fact
  has *what / when / where / who / why*, a kind — an event with `occurred_start` / `occurred_end`, or an ongoing
  state without dates — and entities (always including "user" when the fact is about the user). References are
  resolved into names ("my roommate" + "Emily" → "Emily (user's roommate)"), relative dates are written as
  absolute ones, and coarse dates span their whole period ("in March 2026" → the entire month). A fact also keeps
  **`mentioned_at`** — when its source said it, which may be long after the event. Facts stay as recorded; entity,
  temporal and causal links connect them.
- **Observations — beliefs in a derived layer.** A background consolidator merges facts into one belief per facet,
  each with its source-fact ids, a proof count and supporting quotes. Its rules read like lessons learned: prefer
  updating an observation over creating a sibling; one facet per observation; match by entity, not by topic; a
  state change updates the observation with its date ("owned a 2019 Honda Civic; sold it on March 15, 2025");
  cascade to every affected observation; preserve history; **never do arithmetic** on counts; every create,
  update and delete carries a reason, which is audited. Belief sets can be scoped per bank, per tag, or per source
  (what the commits say vs. what was decided in conversation).
- **Mental models and knowledge pages.** A mental model is a standing question whose answer the bank rewrites in
  the background; reading it is a database read, with no retrieval and no model call. Knowledge pages are mental
  models organized like a wiki — searchable, and projectable to disk as ordinary Markdown.
- **Recall:** four strategies in parallel — semantic, BM25, graph (entity / temporal / causal links), and a time
  range parsed from the query by a rule-based date parser (no model call) — fused by reciprocal rank fusion,
  reranked by a cross-encoder, trimmed to a token budget.
- **Reflect:** an agentic reasoning loop over the bank, shaped by its disposition, that can also refine or
  retract observations.
- **Hygiene:** *Memory Defense*, opt-in per bank, scans every retain against 45 secret and PII patterns and redacts
  or blocks the match before storage. Facts keep their language and entities their script.
- **Coding agents:** one package wires about twenty CLI agents (for Claude Code: three hooks, an MCP server and a
  skill). Each repository gets a bank built automatically from its **git history** (a commit-message seed, then
  per-commit diffs, newest first) and from past sessions (written back at the end of each turn), plus **five
  knowledge pages** — component map, core concepts, conventions and patterns, key decisions and rationale,
  initiatives — each synthesized only from the facts routed to its tier. One belief set per repository, whichever
  agent wrote. Recall runs on every prompt; a reflect runs once per session, on the first prompt, and is cached.
  The injected context says how many pages exist and how to search them — **not their titles**: measured over 40
  real Claude Code turns, a visible roster made the agent open pages by the copied id and never search (0 searches
  at 3 pages, still 0 at 12). Memory lives in the hosted cloud (the default), on a self-hosted server, or in a local
  daemon whose extraction needs a model (it falls back to the Claude Code CLI).
- **Evaluation:** the paper reports **83.6 % on LongMemEval with a 20B open-source backbone**, against 39 % for full
  context with the same model, and 91.4 % on LongMemEval / 89.61 % on LoCoMo with larger models; independent
  reproduction is claimed for Virginia Tech and The Washington Post, and a live site publishes per-model accuracy,
  latency and cost.

### Side by side

| | Hindsight | OpenWiki (Path B) |
|---|---|---|
| Purpose | an agent memory server; per-repo memory for coding agents | the memory under a coding agent, next to a document wiki |
| Models | 25+ providers, local ones included | local only (a 30B chat model + bge-m3) |
| Store | Postgres + pgvector (embedded or server), Oracle | Kuzu (archived upstream, R10) |
| World vs. agent | world facts apart from the agent's experiences | one fact store; `source` tags user / assistant / material |
| Unit of memory | a 5W fact sentence with entities, event time and mention time | an atomic fact with validity interval, cardinality, source |
| Consolidation | per-facet observations over immutable facts, with evidence and reasons | warm-start Louvain themes over facts |
| Time | event time + mention time; coarse dates as intervals; query-time ranges | valid + transaction time; as-of / known-at |
| Retrieval | semantic + BM25 + graph + time range, fused, cross-encoder reranked | cosine × confidence × recency |
| Live injection | recall every prompt; one cached reflect per session; page count, not titles | 16 facts + themes on every prompt |
| Coding-agent sources | git history + sessions | sessions (hooks, backfill) + `wiki_remember` |
| Synthesized documents | five knowledge pages per repository, projectable to Markdown | none — the wiki is built from documents only |
| Hygiene | opt-in secret / PII redaction (45 patterns) | an unsafe-instruction policy, forgetting; no credential redaction |
| Evaluation | LongMemEval 83.6 % (20B open model) / 91.4 %; LoCoMo 89.61 % | LoCoMo 60.7 % J (local 30B, audited ≈ 7 points generous) |

### What we learn
1. **A model of our size can score far higher.** 83.6 % on LongMemEval with a 20B open model — against 39 % for full
   context with the same model — is the calibration point this series was missing: our 60.7 % LoCoMo J with a local
   30B is not a model ceiling. The benchmarks differ and their judges differ, but the gap is architecture, and most
   of it sits on the retrieval side — four recall strategies with cross-encoder reranking, rich 5W facts with
   resolved references, a belief layer. It makes the measurable experiments on our list (BM25 in recall, the
   question's time window, the add-only ablation) worth running soon.
2. **Don't inject an index of titles.** This answers our own idea (§4) of injecting theme labels as a table of
   contents: a visible roster makes the agent open entries by id and never search. Name the count and the way in,
   not the contents — or, if we try it, measure both variants.
3. **Memory writes the wiki.** Five knowledge pages per repository — component map, core concepts, conventions,
   decisions, initiatives — synthesized from tiered facts and projectable to Markdown. For OpenWiki that is the
   missing bridge between its two halves: the memory tier could *write* wiki pages — a "Decisions" and a
   "Conventions" page regenerated at `sleep` from facts and themes, readable in the Wiki tab and exportable (R10).
   And **git history as a source**: our dev memory ignores 400+ commit messages that record most decisions.
4. **Model curation only in a derived layer.** Raw facts are immutable; the model rewrites only observations, which
   can be rebuilt. That reconciles our two findings — model judgments of memory hurt, so keep them away from the
   facts; consolidation needs judgment, so confine it to a recomputable view (our themes already are one). A
   per-facet belief layer over our facts would be safe by construction.
5. **Capture rules we found missing ourselves.** References resolved into names ("Emily (user's roommate)") is
   exactly the "my dog Bruno" loss from our cue experiments; "always include *user* in a fact about the user" makes
   personal facts detectable without relying on the subject; coarse dates as whole-period intervals avoid
   collapsing "March 2026" onto March 1, as our `YYYY-MM` valid-from does.
6. **A third time per fact — when it was said.** Event time and mention time are kept apart. B7 has valid and
   transaction time; "when it was said" we only approximate through the session date. Hindsight makes it a field and
   uses it for recency.
7. **Query-time dates without a model** — a rule-based date parser, for the time-window experiment (Cognee uses a
   model call).
8. **Secret redaction** — the third project with it (Mem0, Cognee, Hindsight).

### What we would not adopt
- **The stack:** Postgres (embedded or server), a reranker model and a model server are heavier than embedded Kuzu
  and NumPy — though its local daemon mode resembles ours.
- **A reasoning loop at the start of every session.** A reflect call per session is the per-prompt probe cost
  again on a local GPU.
- **Cloud as the default** of the coding-agent integration.

**In short.** Hindsight is the most complete system of the series and the closest to what OpenWiki's memory is for:
a per-repository memory for coding agents built from sessions and git history, world facts kept apart from the
agent's own experiences, evidence-backed beliefs in a derived layer, wiki-like knowledge pages — and strong results
with an open 20B model. It is also the most direct challenge to our own numbers: the distance between our 60.7 % and
what it reports with a model of our size is architecture, and much of it is retrieval.


---

## 8. MIRIX

*Sources: [github.com/Mirix-AI/MIRIX](https://github.com/Mirix-AI/MIRIX) at `8cb06a6` (2026-08-20), `mirix` 0.1.0
(client `mirix-client` on PyPI), Apache-2.0, Python ≥ 3.10; the paper "MIRIX: Multi-Agent Memory System for LLM-Based
Agents" (Wang & Chen, [arXiv 2507.07957](https://arxiv.org/abs/2507.07957), July 2025).*

**What it is.** A personal assistant whose memory is built from **screen observation**, voice, files and
conversation — a perception-driven world model of a user's digital life. A Docker backend (Postgres with pgvector
and native BM25) and a dashboard; clients for Python; defaults to Gemini models for both language and embeddings.
Long-term data stays on the user's machine.

### How its memory works
- **Six memory types, defined by purpose.** *Core* — who the user is and how to interact with them (persona and
  human blocks); *episodic* — what happened when, each entry with a timestamp; *semantic* — concepts, people,
  places and organizations in the user's world; *procedural* — reusable skills with triggers and instructions;
  *resource* — documents, files and screenshots the user shared or referenced; *knowledge vault* — static reference
  data: contacts, IDs, addresses and, deliberately, credentials (`secret_value`, labeled with a sensitivity level;
  no encryption found in the code).
- **One manager agent per type.** Incoming messages accumulate; a *meta memory manager* reads each batch and routes
  it to the managers of the affected types, which update their memory through tools. A chat agent answers from all
  of them.
- **Procedural memory from the work process.** Tool calls, tool errors, retries and the fix that finally worked are
  passed in with the conversation — "the distiller's strongest signals" for learning a skill.
- **Consolidation.** A *reflexion* agent runs per query or daily to deduplicate and repair every memory type; an
  *auto-dream* endpoint reviews one memory component at a time, merges duplicates and resolves conflicts
  conservatively — the more recent or more detailed item wins, and **if uncertain, both are kept and the
  discrepancy is recorded**.
- **Retrieval:** topic-based search across the memory types with BM25 and vectors; a rule-based parser turns "today",
  "yesterday", "last week" into a date range.
- **Evaluation:** the paper reports **85.4 % on LoCoMo** and, on its own ScreenshotVQA benchmark (sequences of ~20,000
  screenshots), 35 % higher accuracy than a RAG baseline with 99.9 % less storage.

### Side by side

| | MIRIX | OpenWiki (Path B) |
|---|---|---|
| Purpose | a personal assistant that remembers what happens on the user's screen | the memory under a coding agent, next to a document wiki |
| Perception | screenshots, voice, files, chat | conversation transcripts (+ the documents of the wiki) |
| Models | Gemini by default (any provider) | local only (a 30B chat model + bge-m3) |
| Store | Postgres + pgvector + BM25 | Kuzu (archived upstream, R10) |
| Memory types | six, by purpose, each with its own manager agent | one fact store; `source` tags only |
| Write path | a router agent + six manager agents per batch | one capture call per session + a deterministic merge |
| Conflicts | the newer / more detailed wins; if unsure, keep both and note the discrepancy | valid-time supersession; a coexistence check keeps compatible values |
| Secrets | stored on purpose in the knowledge vault | never stored (P0 blocks instructions; redaction still missing) |
| Procedural memory | skills distilled from tool errors and fixes | out of scope; tool output is stripped from capture |
| Evaluation | LoCoMo 85.4 %; ScreenshotVQA | LoCoMo 60.7 % J (local 30B, audited ≈ 7 points generous) |

### What we learn
1. **Typed memory with purpose definitions.** MIRIX makes CoALA's taxonomy concrete — core, episodic, semantic,
   procedural, resource, plus a vault for lookups — each with a "key question" that decides where a fact belongs.
   Our facts carry no type. A type tag at capture (a cheap field in the existing call) would allow per-type recall
   budgets — the user's core preferences always present, episodes for time questions, resources when a document is
   referenced — and connects to the always-present core and Hindsight's world/experience split.
2. **Errors and fixes are the strongest procedural signal.** MIRIX feeds tool errors, retries and the final fix to
   its skill distiller; Mem0's coding plugin records failed commands with their fixes. Our capture strips tool
   output from the transcript, so a coding session's most reusable lesson — what failed, and what finally worked —
   never reaches memory. Capturing bounded failure → fix pairs as facts is a capture change worth measuring.
3. **Record uncertainty instead of forcing a winner.** When auto-dream is unsure which of two conflicting entries is
   right, it keeps both and writes the discrepancy down. Our coexistence check keeps compatible values, but an
   unresolved conflict is either closed or kept silently — a "disputed" mark would let the context say so.
4. **A counter-example on secrets.** Storing credentials by design, with only a sensitivity label, is the opposite of
   what Mem0, Cognee and Hindsight do. For a memory injected into every prompt we keep the opposite rule — and it
   underlines that our missing redaction is a gap, not a choice.

### What we would not adopt
- **Seven LLM agents on the write path** — a router plus one manager per memory type, per batch: far beyond a local
  30B on one GPU.
- **Secrets in memory**, and screen capture as a perception source — outside our scope, and a privacy load.
- **Cloud models by default** for processing screenshots of a user's desktop.

**In short.** MIRIX takes CoALA's taxonomy furthest: six purpose-typed memories, each with its own manager agent, fed
by what happens on a user's screen. Compared with Cognitive Substrate it shares the perception-first idea but has no
ground-truth tier and no per-tier write authority — every type is written by its own LLM manager. For OpenWiki its
value is the taxonomy (typed facts with per-type budgets), procedural memory distilled from errors and fixes, and a
rule for disagreements: keep both and say so.


---

## 9. AriGraph

*Sources: [github.com/AIRI-Institute/AriGraph](https://github.com/AIRI-Institute/AriGraph) at `e884b76` (2024-09-10),
MIT, research code (~4,000 lines; Linux only because of TextWorld; unchanged since September 2024); the paper
"AriGraph: Learning Knowledge Graph World Models with Episodic Memory for LLM Agents" (Anokhin et al., AIRI,
[arXiv 2407.04363](https://arxiv.org/abs/2407.04363), IJCAI 2025).*

**What it is.** The memory of *Ariadne*, an LLM agent that plays text adventures (TextWorld): it explores rooms,
picks up items, reads notes and follows recipes, and builds a **knowledge-graph world model** of the environment
from scratch as it goes — the most literal "world model from incoming data" of the shortlist.

### How its memory works
- **Per observation, two LLM calls.** First, extract triplets from the new observation ("kitchen, contains, fridge";
  "key, is in, locker"), with earlier triplets as examples; subjects and objects stay atomic, and **uncertain
  statements become hypotheses** ("John, could be, winner", never "John, will be, winner"). Second, a *refining*
  call compares the new triplets with the existing ones around the same entities and names the **outdated** ones to
  replace — "item, is in, locker" → "item, is in, inventory" when the player takes it — with a strongly
  conservative bias: replace only on a conflict about the *same* aspect; when unsure, keep.
- **Semantic memory = the current state.** Outdated triplets are deleted from the semantic graph; it always describes
  the world *now*.
- **Episodic memory = the history.** Every raw observation is stored as an episodic vertex, linked to the triplets
  extracted from it.
- **Retrieval by semantic breadth-first search.** For each entity in the agent's plan, find the triplets most similar
  to it (above a 0.75 threshold), then queue the entities of those triplets as new queries, down to a set depth —
  spreading activation over facts. **Episodes are ranked** by how much their triplets overlap the retrieved subgraph,
  weighted by their similarity to the plan; the top ones come back as raw text.
- **Evaluation:** on its five TextWorld environments AriGraph reaches normalized scores of 1.0 / 0.79 / 1.0 (Treasure
  Hunt / Cleaning / Cooking) and 1.0 on both hard variants, against 0.05–0.52 for full history, summaries and RAG,
  and at or above the top human players. Without architecture changes it also answers multi-hop QA (MuSiQue F1
  47.4, HotpotQA F1 69.9 with GPT-4).

### Side by side

| | AriGraph | OpenWiki (Path B) |
|---|---|---|
| Purpose | the world model of a game-playing agent | the memory under a coding agent, next to a document wiki |
| Input | one observation per step | conversation transcripts per session |
| Write path | extract triplets + an LLM names outdated ones (per step) | one capture call per session + a deterministic valid-time merge |
| Current vs. history | current state in the semantic graph; history in raw episodes | both in one fact store (closed intervals), as-of / known-at |
| Uncertainty | hypotheses as their own triplets | not captured (planned facts have their own status) |
| Retrieval | semantic BFS over triplets from the plan's entities; episodes ranked by overlap | single-hop similarity over facts + themes |
| Raw source | episodes returned with the facts | transcripts stay outside the memory tier |
| Evaluation | TextWorld games, MuSiQue, HotpotQA | cross-session sets; LoCoMo 60.7 % J |

### What we learn
1. **Multi-hop recall by expansion.** Retrieve facts similar to the query, then the facts around *their* subjects and
   objects, with a similarity threshold and a depth limit. Our memory recall is single-hop similarity, and multi-hop
   is a LoCoMo category we lose (65.2 %). The expansion runs over the facts already in memory — subjects and objects
   are strings, so "around" means sharing one — and is cheap to test as a paired re-answer. (It is also a concrete
   version of B8, spreading activation.)
2. **Episodes linked to their facts, returned with them.** Keeping the raw observation as an episode linked to the
   facts it produced, and ranking episodes by overlap with the retrieved facts, gives the answering model the context
   that atomic facts lose. For us: store each capture window's text (or a short summary) with links to its facts, and
   add the best-matching windows to the context — the "raw sessions searchable" theme, in its simplest form.
3. **The current state and the history, kept in different places.** AriGraph's graph always says what is true *now*,
   while history lives in the episodes. We keep both in one fact store (closed facts stay as history), which serves
   as-of questions — at the price of history competing with the present in recall. Our recall filters to current
   facts, so the outcome is similar; the split is still a clean way to think about a world model.
4. **Hypotheses as hypotheses.** Uncertain statements are stored as such ("could be"), not as facts — the second
   project after LangMem to do so; it strengthens the hedged-facts idea (§6, lesson 4).
5. **When unsure, keep.** The refining prompt's conservative bias — replace only on a conflict about the same aspect
   — is the same lesson as our veto-only coexistence check, arrived at independently.

### What we would not adopt
- **Destructive deletes** in the semantic graph — the history survives only as raw episodes, with no as-of view.
- **Two LLM calls per observation** — fine for a game agent, too much for a local 30B over long sessions.
- **The code as a dependency** — research code, Linux only, unmaintained since 2024.

**In short.** AriGraph is the purest "world model from incoming data" of the series: per observation, extract
triplets, replace what has become false, keep the raw observation as an episode linked to its triplets, and retrieve
by expanding through the graph from the entities the agent is planning around. Close in spirit to Cognitive
Substrate (semantic + episodic, current state vs. history), and research-grade. For OpenWiki its two retrieval
ideas — semantic expansion through shared entities, and episodes ranked by overlap with the retrieved facts — are
cheap to test on LoCoMo's multi-hop questions.


---

## 10. Nemori

*Sources: [github.com/nemori-ai/nemori](https://github.com/nemori-ai/nemori) at `d2a6dff` (2026-04-16), `nemori` 0.2.0,
MIT, Python ≥ 3.10 (a rewrite aligned with the paper; the earlier MVP lives on a branch); the paper, now titled
"What Deserves Memory: Adaptive Memory Distillation for LLM Agents" (Ma et al.,
[arXiv 2508.03341](https://arxiv.org/abs/2508.03341), v4 April 2026).*

**What it is.** A long-term memory substrate for conversational agents built on two ideas from cognitive science:
*event segmentation* — memory is organized in episodes whose boundaries fall where the topic shifts — and
*predictive processing* — what deserves to be learned is what the current model of the world failed to predict.
PostgreSQL for metadata and text search, Qdrant for vectors; hosted models through OpenRouter or OpenAI.

### How its memory works
- **Segmentation.** Messages are buffered per user (1–20 at a time) and a model detects topic boundaries, so each
  episode is one coherent stretch of conversation.
- **Episodes.** Each segment becomes an episodic memory: a 10–20-word title, a third-person narrative with "who, at
  what time, what was discussed, what was decided, what emotions, what plans", and a timestamp precise to the hour;
  relative dates are converted to absolute ones in the text ("on the upcoming weekend (March 16, 2024)"). A
  new episode that continues an existing one (same event, less than an hour apart) is merged into it.
- **Predict–calibrate.** For each new episode the system retrieves the semantic statements relevant to its title
  — "your current world model" — and asks a model to *predict* what happened in the episode from those alone. A
  second call compares the prediction with the real conversation and extracts **only what the original contains
  that the prediction missed or got wrong**, filtered by four tests: still true in six months, specific, useful for
  predicting future needs, understandable on its own. Semantic memory therefore grows only by what was surprising.
- **Retrieval:** hybrid vector and text search over episodes and semantic statements; the LoCoMo setup puts the top
  10 episodes and the top 20 statements into the answer context (optionally the original messages of the best
  episodes).
- **Evaluation:** the README reports **LoCoMo 0.83** (LLM judge) with gpt-4.1-mini — single-hop 0.89, multi-hop 0.79,
  **temporal 0.79**, open-domain 0.59.

### Side by side

| | Nemori | OpenWiki (Path B) |
|---|---|---|
| Unit of memory | dated narrative episodes + atomic semantic statements | atomic facts with validity intervals |
| Segmentation | topic boundaries detected by a model | capture per session (hooks) or per fixed window (backfill) |
| What gets stored | only what the existing knowledge failed to predict | every durable fact; re-stating one raises its confidence |
| Time | absolute dates written into each episode; hour-precise timestamps | valid-from per fact; bi-temporal merge |
| Answer context | top 10 episodes + top 20 statements | top 16 facts + themes |
| Calls per unit | segmentation + episode + prediction + extraction | one capture call per session (+ lazy merge checks) |
| Evaluation | LoCoMo 0.83 (gpt-4.1-mini); temporal 0.79 | LoCoMo 60.7 % J (local 30B); temporal 45.8 % |

### What we learn
1. **Narrative episodes in the answer context.** The widest category gap between Nemori and us is temporal (0.79 vs
   45.8 %), and temporal questions are exactly where a dated narrative helps: the event and its absolute date sit
   in one sentence, where our atomic facts scatter them. With waku's episodes, Graphiti's sagas, Mem0's rich
   memories and Hindsight's 5W facts, this is the fifth system pointing the same way — the strongest case yet for
   the episode experiment: one narrative per LoCoMo session with absolute dates, retrieved next to the facts,
   re-answered paired.
2. **Our own capture misses most of a long session.** Nemori segments the *whole* stream; checking our hook capture
   against that showed it reads only the **last 20,000 characters** of the transcript at each compaction and at
   session end (`parse_claude_transcript`'s default). For the long dogfooding session that wrote this review —
   1.5 million characters of conversation over 36 days, nine compactions — that is at most ~13 % of the
   conversation; the rest never reaches memory through the hooks (`backfill` covered older history once). The fix
   is mechanical: remember a per-session capture watermark and capture everything since it, cut into bounded windows
   (the windowing `backfill` already uses), in the detached worker.
3. **Store what was surprising.** Predict–calibrate is a principled novelty filter: semantic memory grows only by
   prediction error, so it stays free of what is already known. Our design takes the opposite view of repetition —
   re-stating a fact raises its confidence — and keeps redundancy in check by deduplication instead. Both are
   defensible; the filter costs two extra model calls per episode, which is the deciding factor on a local 30B.
4. **A recall budget split by memory type.** Ten episodes plus twenty statements is a deliberate mix; with typed
   memory (§8) we could budget facts, episodes and themes separately instead of one ranked list.

### What we would not adopt
- **Four model calls per segment** (segmentation, episode, prediction, extraction) on the write path.
- **PostgreSQL + Qdrant** as infrastructure, and hosted models as the default.

**In short.** Nemori gives a principled answer to "what deserves memory": cut the stream where the topic changes,
keep each segment as a dated narrative, and distill into semantic memory only what the existing knowledge could not
predict. Its LoCoMo profile is strongest where we are weakest — temporal questions — which points at narrative
episodes; and its insistence on segmenting the whole stream exposed a real gap in our own capture.


---

## 11. memory-champ

*Source: [github.com/mikeleewoodai/memory-champ](https://github.com/mikeleewoodai/memory-champ) at `60691eb` (2026-10-01),
`memory-champ` 1.0.0, MIT, Python ≥ 3.10 (not on PyPI; installed from GitHub); its published tool contract and policy
schema (`contracts/`). A small, carefully engineered project (~7,900 lines with tests, one test per acceptance
criterion).*

**What it is.** A memory *service* for agent orchestrations, structured explicitly on CoALA: episodic, semantic and
procedural memory, plus CoALA's working memory as "decision cycles", exposed as nine MCP tools (stdio only) and kept
by an independent daemon. SQLite with sqlite-vec and FTS5; a local embedding model; **no language model by default**
— the host agent writes its own memories through the tools. It never reaches the network, never touches files
outside its database, never talks to a user: "safe to attach to an arbitrary agentic loop".

### How its memory works
- **Learning gates per memory type** (in `policy.yaml`). *Episodic* writes are automatic — "a record of what happened
  is not a claim". *Semantic* writes are automatic too, but **contradictions are surfaced, never resolved**: a write
  that conflicts with existing facts returns them as a warning, recall returns them alongside the results, and only
  an explicit `supersedes` replaces a fact. *Procedural* memory is **always a proposal**: the agent can only queue a
  procedure; approving it requires an **Ed25519 signature** from a reviewer key the agent does not have, and the daemon
  may never approve. Procedures carry success statistics and are judged after enough invocations.
- **Importance priors:** 0.5 by default, +0.2 when the record is a failure ("failures teach more than successes"),
  +0.3 when a human wrote it, +0.1 per corroboration.
- **Forgetting by type:** episodes expire after 90 days and older ones are consolidated into semantic facts (clusters
  of three or more); semantic facts never expire but carry a **volatility class** — stable, slow, volatile — that
  schedules a recheck after 365, 90 or 14 days. Removal is a tombstone (reversible), a redaction (content
  overwritten, shape and provenance kept — for personal data written by mistake), or an explicit hard delete.
- **Working memory as decision cycles.** A host may open a cycle, record observations in it, and close it, which
  promotes them to episodes; a crashed or abandoned cycle is reaped and promoted by the daemon. Loop safety detects an
  agent repeating itself and caps writes per minute.
- **Recall:** reciprocal rank fusion over FTS5 and vector search, weighted by relevance 0.5, recency 0.2 (half-life
  72 hours) and importance 0.3; strategies hybrid / semantic / keyword / recent; 12 records and a **measured** hard
  ceiling of 1,500 tokens for the context block; episodes are opt-in; records flagged sensitive are redacted in the
  context block.
- **The daemon** (every six hours): reap cycles, expire by TTL, consolidate episodes, detect contradictions, turn
  repeated successful patterns into procedure *proposals*, re-embed after a model change. An optional LLM
  adjudication pass is off by default.
- **Health:** `memory_stats` returns warnings — proposals nobody reviews, procedures that keep failing, cycles that
  never close.

### Side by side

| | memory-champ | OpenWiki (Path B) |
|---|---|---|
| Who writes memories | the host agent, through tools (no LLM in the server) | a local 30B captures transcripts; the agent may add via `wiki_remember` |
| Memory types | episodic, semantic, procedural + working-memory cycles | one fact store |
| Contradictions | surfaced, never resolved (explicit `supersedes` only) | resolved by a valid-time merge and a coexistence check |
| Risky writes | procedures behind a signed human approval | instructions blocked by policy; agent writes opt-in |
| Staleness | volatility classes with recheck intervals | restatement + `wiki_remember`; ephemeral events forgotten |
| Importance | priors by failure, human authorship, corroboration | confidence by corroboration; material ×0.75 |
| Recall | FTS5 + vectors fused; relevance / recency / importance weights | cosine × confidence × recency |
| Budget | 12 records, a measured 1,500-token ceiling | 16 facts in 3,000 characters (~4 per token) |
| Store | SQLite + sqlite-vec + FTS5 | Kuzu (archived upstream, R10) |
| Evaluation | acceptance tests, a recall eval on fixtures | cross-session sets; LoCoMo 60.7 % J |

### What we learn
1. **Volatility classes for facts.** A fact that is true "now" — a current release, a count, "the remaining item",
   "next on the roadmap" — goes stale on its own, while a definition does not. Every stale fact we corrected this week
   through `wiki_remember` was of the first kind. A volatility tag at capture (one more field in the existing call)
   would let recall mark a volatile fact as "possibly outdated" after a few weeks, or rank it lower — a deterministic
   handle on the stale-state debt (D12).
2. **A human gate for the riskiest write path.** memory-champ puts procedural memory behind a signature the agent
   cannot produce. We have the matching open question: an always-present core of user conventions (§1, §6) would be
   the most powerful — and most poisonable — memory we could add. Gate it the same way: conventions enter the core
   only by explicit human approval (a CLI or Gedächtnis-tab action), never by capture alone.
3. **Surface contradictions you cannot resolve.** The second system after MIRIX to keep both sides of an unresolved
   conflict and say so. Our merge resolves conflicts by valid time; where it cannot decide, a "disputed" mark shown in
   the context would be more honest than either outcome.
4. **Importance by provenance and outcome.** Failures and human-authored records start with higher importance. We
   down-weight discussed material, but give no lift to the user's own decisions — and capture no failures at all
   (§8, lesson 2).
5. **Health warnings.** A memory that reports its own warning signs — for us: a journal that is not draining, capture
   failures in the hook log, a growing share of volatile facts past their recheck date — would make `openwiki status`
   more useful than counts alone.
6. **The other way to write memory.** Here the host agent — usually a frontier model — writes its memories itself, and
   the server stays deterministic. That sidesteps local-model capture quality entirely, at the price of depending on
   the agent's diligence. `wiki_remember` is the same mechanism for us; memory-champ shows it can be the main path.

### What we would not adopt
- **Agent writes as the only path** — memory would depend on the agent remembering to write.
- **An English-only small embedder** (all-MiniLM-L6-v2) for a German corpus.
- **The decision-cycle API** as a requirement — even memory-champ makes it optional.

**In short.** memory-champ is CoALA taken literally and engineered carefully: three memory types with their own write
rules, contradictions surfaced rather than resolved, procedures behind a signed human gate, volatility-driven
rechecks, and a server with no model of its own. Of all the systems in the series it is the closest to Cognitive
Substrate's "server-enforced authority per tier". For OpenWiki its sharpest ideas are a volatility class per fact and a
human gate for whatever memory becomes always-present.


---

## 12. Hermes Agent

*Sources: [github.com/NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) at `d795726` (2026-10-03),
release `v2026.9.24`, MIT, Python (about three quarters of a million lines in its main packages); its user guide on
memory, skills, the curator and memory providers (`website/docs/user-guide/features/`). Reviewed on request, from a
description that circulates with it — checked claim by claim below.*

**What it is.** Nous Research's self-hosted agent, "the agent that grows with you": a CLI, TUI and desktop app plus
messaging gateways (Telegram, Discord, Slack, WhatsApp, e-mail), cron jobs, subagents and sandboxed terminals. Memory
is one part of a large harness, and the built-in part is small on purpose; deeper memory comes from provider plugins.

### How its memory works
- **Two curated files.** `MEMORY.md` (the agent's notes on its environment, 2,200 characters) and `USER.md` (the
  user's profile, 1,375 characters), written by the agent itself through a `memory` tool — add, or replace / remove by
  a unique substring. Both enter the system prompt as a **frozen snapshot** at session start, never mid-session, so
  the prompt cache survives; a gauge shows the fill level ("67 % — 1,474/2,200 chars"). A full store refuses the
  write and the agent must consolidate in the same turn — nothing is compacted automatically.
- **Declarative, durable, narrow.** The system prompt asks for facts, not instructions ("User prefers concise
  responses", not "Always respond concisely" — imperatives get re-read as directives later), sends everything
  task-specific to skills — memory is "the narrow exception for facts that apply to every session" — and keeps
  volatile state out: "a fact stale within a week belongs in session history".
- **Session history.** Every message of every session sits in SQLite with FTS5; `session_search` finds sessions and
  scrolls through them — no LLM, no summaries. Cron sessions are demoted so their repetitive vocabulary can't crowd out
  the user's own ("recall blindness").
- **The learning loop.** After a reply is delivered, a **background review** forks the agent — on the same prompt
  cache, or on a cheaper model with a digest of the conversation — every 10 user turns for memory and after 10
  tool-calling iterations without a skill write for skills. The skill prompt pushes for action ("a pass that does
  nothing is a missed learning opportunity"), names the signals — the user corrected style or workflow, a fix was
  found, a loaded skill was wrong — and what never to keep: environment-dependent failures, **negative claims about
  tools** ("they harden into refusals the agent cites against itself for months"), and unresolved failures written up
  as a working method. On a local GPU the review waits until the machine is idle.
- **Skills as procedural memory.** A skill is Markdown in the agentskills.io format — `SKILL.md` plus `references/`,
  `templates/`, `scripts/` — loaded on demand; written in a turn, by the review, or with `/learn` from documents and
  URLs. The content rule is "lessons, not logs": a pitfall is a general rule plus one clause of why, with no incident
  story, date or PR number. A patch needs a fresh read of the skill (enforced).
- **The curator** keeps agent-made skills in check: usage counts; an unused skill turns stale after 14 days and is
  archived after 30 — never deleted, pinned skills exempt; an LLM pass that merges skills into class-level ones is
  opt-in (50–100 calls per run). Every change lands in a ledger with content-addressed before/after copies, so one
  change can be rolled back.
- **Hygiene.** Writes to memory and skills pass a threat-pattern scan — prompt injection, promptware / C2 vocabulary,
  exfiltration, SSH backdoors, hardcoded secrets — on Unicode-NFKC-folded text, plus a check for invisible
  characters. An optional **write approval** stages every write (`/memory pending`, `approve`, `reject`); a staged
  replace is pinned to the exact entry it was reviewed against.
- **Providers.** Five bundled memory plugins (Mem0, Holographic, OpenViking, RetainDB, ByteRover) and more in a catalog
  (Honcho, Hindsight, Supermemory) run alongside the files, through hooks that mirror ours: prefetch before a turn,
  sync after it, extract before compression and at session end. Recall is skipped for trivial prompts ("ok",
  "thanks", "continue").

### The description, against the code
| Claim | In the code |
|---|---|
| a semantic knowledge graph of facts and preferences | two Markdown files of 3,575 characters; graphs only through optional plugins (Hindsight, Holographic) |
| episodic memory of past tasks, successes and errors | every message in SQLite, searchable by full text; no separate record of outcomes |
| skills compiled into a Python tool library | Markdown procedures (with optional scripts), loaded into the prompt on demand |
| one timeline across CLI, Telegram and Discord | one session store and one memory per profile; chats stay separate sessions |
| zero amnesia, no "hallucination dip" | long sessions are compressed; the docs warn that a session run for weeks grows expensive and its learning loop "almost never gets to fire", and recommend `/new` at natural boundaries |

### Side by side

| | Hermes Agent | OpenWiki (Path B) |
|---|---|---|
| Purpose | a self-hosted agent harness (CLI, desktop, messaging gateways, cron) with a memory slot | the memory under a coding agent, next to a document wiki |
| Built-in memory | two agent-curated Markdown files, 3,575 characters, in the system prompt | ~1,200 current facts; 16 recalled per prompt within 3,000 characters |
| Who writes | the agent (`memory` tool) and a background review every 10 turns | a local 30B captures each session; the agent via `wiki_remember` |
| History | every message in SQLite + FTS5, searched by the agent | transcripts outside memory; `backfill` turns them into facts |
| Time and contradictions | none — a replace overwrites the entry | bi-temporal facts merged by valid time; as-of / known-at |
| Procedural memory | skills written by the agent and the review, kept by a curator | none (Claude Code skills are written by hand) |
| Injection | once per session (frozen, cache-friendly) + provider recall per turn | facts on every prompt + the handoff brief at session start (v0.98) |
| Hygiene | threat-pattern scan (NFKC, invisible characters, secrets); optional write approval | P0 policy on facts and the handoff note; one-off events forgotten by `sleep` |
| Store | files + SQLite; provider plugins alongside | Kuzu (archived upstream, R10) |
| Evaluation | behavior probes; no memory benchmark in the repository | cross-session sets; LoCoMo 60.7 % J |

### What we learn
1. **Writes must land during a session, not only after it.** Hermes' docs say it plainly: memory "needs session
   boundaries". Our new handoff showed the same on its first run (v0.98): 144 facts were waiting in the journal — 126
   from the evening's compaction capture, 18 from the agent's own `wiki_remember` — because the MCP server holds the
   graph read-only for as long as a session lives, so nothing written during a session lands before it ends; the
   longer the session, the larger the backlog. Hermes writes plain files and never meets a lock. For us: let the MCP
   server fold the journal itself when it is idle — release its read connection, fold writable, reopen.
2. **Skip memory for prompts that don't need it.** Hermes skips recall for "ok" and "continue". Its list matches 1 of
   the 465 prompts of our dogfooding session — but release chores ("push", "push and tag …", "sync arc42 docs") are
   75 of them (16 %), and each received 16 facts. The idea transfers; the trivial set is project-specific.
3. **Harden the policy against evasion.** NFKC folding before matching (full-width "ｃａｔ" → "cat"), a check for
   invisible and bidirectional characters, and a hardcoded-secret pattern — three small additions to `is_unsafe_text`;
   the last is the credential redaction already first on our list.
4. **Inject once, search on demand.** Hermes loads its memory once per session, keeps the prompt cache intact and leaves
   the rest to search — the pattern our SessionStart brief now follows for the handoff. The per-prompt facts remain
   the open "when to inject" question.
5. **Guardrails for procedural memory, should we add it.** Hermes' do-not-capture list is the most concrete in the
   series: no environment-dependent failures, no negative tool claims, no unresolved failure presented as a method.
   Checked against our current facts, the durable capture already avoids both risks we can measure — imperative
   phrasing (5 of 1,187 facts contain imperative words, mostly descriptive: "always exits 0") and negative tool claims
   (3, none a stale refusal) — so the list matters for the "procedural memory from errors" candidate, not for today's
   capture.
6. **Approval through the journal.** `write_approval` stages writes for a person to approve. Our journal already holds
   the agent's writes until the next fold; a `pending` / `approve` step before folding would give `wiki_remember` the
   same gate — and the always-present core, if it comes.

### What we would not adopt
- **A few kilobytes as the whole memory.** 3,575 characters suit a personal assistant's profile; a project memory of
  ~1,200 facts with history does not fit, and a substring `replace` keeps no history.
- **A review biased toward writing** ("Be ACTIVE"): Hermes needs a curator to clean up after it, and our measurements
  found local-model judgments of memory unreliable (`path-b-memory.md` §13.1, §13.3–13.5).
- **Replaying the conversation for every review.** About 30,000 tokens per event is cheap with a cloud prompt cache and
  slow on a local 30B — Hermes itself defers reviews on local GPUs.

**In short.** Hermes Agent is a large, carefully engineered agent harness whose built-in memory is small on purpose: two
curated files injected once per session, full-text search over every message, and a background review that turns
corrections and fixes into skills — procedural memory with a curator, a ledger and rollback. The description that
circulates with it promises a knowledge graph and zero amnesia that the code does not contain; its own docs say memory
needs session boundaries. That observation, confirmed on our memory by the new handoff, is its sharpest lesson for
OpenWiki: writes should land while a session is still running.


---

## 13. A design report: predictive world models through transfer entropy

*Source: "Building a Predictive World Model in Digital Knowledge Management: Integrating Dynamic Experience Networks
through Transfer Entropy" — a report without author or date, shared on 2026-10-04, with 40 references; checked against
those references and against OpenWiki's own data. A design proposal, not a system: there is no code to read.*

**What it proposes.** A Second Brain stores knowledge as a static graph that only grows; to become a *predictive world
model* it should learn which new experiences change which established concepts. The report separates a stable
**world-model graph** — concepts joined by directed, weighted causal edges — from temporary **experience subgraphs**
(a debugging session, a project, a book), and merges the second into the first only where **transfer entropy**
(Schreiber 2000) shows directed information flow over time: how much the past of X reduces the uncertainty about Y's
future beyond Y's own past.

### The pipeline
1. **Time series.** Local telemetry turns every note into an activity series — reads, edit bursts, timestamps —
   collected by Obsidian plugins (TSDB, Effort Index, Activity Atlas, Patina).
2. **Transfer entropy** between an experience node and an established concept, estimated with the KSG
   nearest-neighbour estimator (JIDT) or a learned estimator (AGM-TE), to cope with short series.
3. **Merge.** Above a threshold, add a directed edge weighted by the TE value (case A); on an existing edge, strengthen
   or weaken it — forgetting as "synaptic depression" (case B); near zero, keep the experience out, however similar it
   looks (case C).
4. **Denoise** with conditional transfer entropy or PCMCI (Runge 2019), so that A → B → C does not also leave a direct
   A → C.

Beyond that it sketches TE-steered message passing in graph neural networks and warnings from learned fault cascades
("error X has preceded failure Y before").

### The claims, checked
- **The mathematics and the attributions hold.** Transfer entropy (Schreiber 2000), its equivalence with Granger
  causality for Gaussian processes, the KSG estimator, conditional TE, PCMCI and JIDT are real and described correctly
  in outline.
- **The cited sources exist — and say less than the report.** The four Obsidian plugins are real and record what it
  says (a local time-series database, edit bursts, 10-minute activity bursts, a decay score with a half-life). The GCN
  paper (Moldovan et al. 2024) finds that TE-based node selection "enhances accuracy", not that it "drastically
  reduces" errors. The Max Planck / Ontic Labs article is about world models for robots trained on video, not about
  knowledge management. AGM-TE (Kornai et al., CLeaR 2025) is demonstrated on 250-dimensional neural spike data — long,
  dense series — and opens by defining causation through interventions.
- **"Causal" means predictive.** TE measures directed predictive information. Edit and read telemetry records the
  *user's* attention, so a high TE from X to Y says that working on X tends to be followed by working on Y — a
  regularity of the workflow, with the task as a hidden common cause that no conditioning on observed notes removes.
  The report reads it as X causally driving Y.
- **Significance is assumed.** A fixed threshold (`THRESHOLD = 0.15  # Defined significance level`) stands in for
  surrogate tests, and n concepts mean n² directed tests without a correction for multiple comparisons; "mathematically
  proven" and "guarantees … a strictly minimal, causal network" overstate what estimates under PCMCI's assumptions (no
  hidden confounders, stationarity) can deliver.
- **The code is a sketch.** `get_node_activity_history` and `create_te_calculator` are undefined, and NetworkX has no
  `add_directed_edge`. Nothing is evaluated: no data set, no comparison with plain co-occurrence, no effect on retrieval
  or answers.

### Against OpenWiki's data
TE needs long activity series per concept. Measured on the dogfooding project (read-only):
- **Memory:** 1,486 facts about 304 distinct subjects; the facts about a subject were said on a median of **1 day**
  (90th percentile 2, maximum 7 — "project"). No subject is active on 10 days, so no pair of subjects has series to
  estimate from.
- **Usage memory:** **0** `REINFORCES` edges and 0 pending usage records. The one directed, decaying signal we built —
  seed page → page pulled in by GraphRAG — comes from `wiki_ask`, and the dogfooding session (465 prompts) called it 0
  times: it read memory through the hook and wrote it through `wiki_remember` (9 calls).
- **Git history:** the densest signal — 173 commits, 12 files changed in at least 30 of them — but its strongest
  couplings are release mechanics (`openwiki/__init__.py` with `pyproject.toml` in 114 commits), and they are
  simultaneous — the same commit — where TE looks for a lag.

### What we learn
1. **Check the data before the method.** A learning-from-use mechanism needs a use signal, and we lacked one without
   knowing it: the usage memory has been empty in the workflow it was meant for. Before any TE-like idea, the coding
   workflow needs a signal it actually produces — which recalled facts an answer uses, which files change together.
2. **Change coupling is the cheap, testable variant for code.** Files that change together in git history (release
   mechanics excluded) are a deterministic edge type for a code-corpus wiki — "when `cli.py` changes, `CLAUDE.md` and the
   tests usually do too" — and the baseline any directed measure would have to beat.
3. **Experience versus world model is the right split, and we have it.** Sessions and their facts on one side,
   consolidated themes and the wiki on the other, with consolidation as the merge. The report's filter — admit only what
   adds predictive information — has a form that works on sparse data: Nemori's predict–calibrate (§10), which asks a
   model what it failed to predict instead of estimating entropies from activity.

### What we would not adopt
- **Transfer entropy on interaction telemetry** for a personal or project memory: the series are far too short (above),
  the signal is the workflow, and n² pairs need significance control.
- **Rejecting links by TE** (case C): it would drop true references and typed relations that are rarely co-edited.
- **Keystroke and reading-time telemetry** — privacy-heavy, editor-specific, and absent from a coding agent's setting.

### Thought experiment: a transfer-entropy estimator for very short data

*Added 2026-10-04.* Suppose an estimator existed that recovers transfer entropy from a handful of events. It would
remove the first obstacle above (series too short), not the other two: the signal would still be the workflow, with
the task as a hidden common cause, and n² pairs would still need significance control. And a few events carry only a
few bits — an estimator that works there must bring the rest from a **prior**, learned on other histories, so its edges
would be hypotheses that the data confirms or weakens over time (which would make the report's "Bayesian update"
meaningful). The data would be at hand: B7 keeps every attribute's history as validity intervals, a series of change
points.

**What it would change in consolidation.**
- **An influence graph next to the similarity graph:** directed, lagged edges ("a change in X is followed by a change
  in Y within a day"); themes could describe mechanisms — release → test count → docs — instead of topics.
- **Staleness by dependency — the main payoff.** Our open problem (D12, §13.4–13.6 of `path-b-memory.md`): a "current"
  fact goes stale when the world changes and nothing says so. With learned dependencies, consolidation would act like a
  build system — when X changes, mark what depends on X "possibly outdated" and recheck it.
- **Measured volatility:** the entropy rate of a fact's own series — memory-champ's stable / slow / volatile classes,
  measured instead of guessed.
- **Keep the causes, derive the rest:** conditional TE finds facts that others fully explain — a principled redundancy
  criterion for forgetting, Nemori's predict–calibrate made quantitative.
- **Surprise on write** (an unexpected change is the learning signal) and **directed recall** ("why did X change?"
  follows edges backward — the temporal and multi-hop questions where our LoCoMo scores are lowest).

**What would go wrong.** Stable knowledge is invisible to TE (a constant series has no entropy, so "OpenWiki uses Kuzu"
looks inert — forgetting by low TE would delete the bedrock). Routines look like dependencies ("push and tag" moves
version, docs and test-count facts together) — though a coding agent's confounder is partly observable (the user's
prompts) and its own edits are interventions, which is what turns predictive edges into causal ones. Injected memory
steers the agent, so TE would learn the memory's own influence unless it conditions on what was injected; an attacker
who controls timing can plant edges; and over-eager propagation marks half the memory stale at every release.

**Checked on our data.** Before building an influence graph for any estimator, the staleness idea can be tested with a
crude dependency proxy. The dev memory was rewound to 2026-09-27 09:00, just before `wiki_remember` closed the 14
labeled stale facts of §13.4 (1,191 current facts then; labels cover samples, so other stale facts go unlabeled). Rules
flag a current fact "possibly outdated"; lift = recall / share of memory flagged (1 = chance):

| Rule | Stale caught | Memory flagged | Lift |
|---|---|---|---|
| co-change on the same day, attribute level, k ≥ 2 | 0/14 | 2.7 % | 0 |
| co-change, attribute level, k = 1 | 6/14 | 64.3 % | 0.67 |
| lead-lag 1–2 days (the TE-like proxy), attribute level, k = 1 | 8/14 | 55.3 % | 1.03 |
| co-change, subject level, k = 3 (the best lift) | 2/14 | 6.0 % | 2.36 |
| lead-lag 1–2 days, subject level, k = 1 | 9/14 | 53.1 % | 1.21 |
| age > 7 days | 10/14 | 88.2 % | 0.81 |
| volatile phrasing — plans, running states, counts / versions, capability gaps (written after seeing the 14: optimistic) | 12/14 | 10.4 % | 8.23 |

**All 14 stale facts belong to attributes stated exactly once** — there is no history from which any estimator, TE or
co-change, could learn a dependency; at the subject level dependencies exist but follow the routine, at chance. What
caught them is the *kind* of fact — a plan, a count, a running state — which is the prior a short-data estimator would
have to supply anyway. So the evidence points away from an influence graph (for now) and toward **volatility classes
at capture** (plan item 12 in `agent-memory-summary.md`), to be confirmed on a fresh labeled set.

**In short.** A well-written synthesis of real methods — transfer entropy, KSG, conditional TE, PCMCI — applied where
their preconditions fail: personal-knowledge telemetry is sparse, confounded by the user's own attention, and tested n²
times. No implementation, no evaluation, and "causal" throughout where the measure is predictive. Its lasting value for
OpenWiki was the check it prompted: our one learning-from-use signal turned out to be empty, and git co-changes are the
denser, testable substitute.

---

## Across the series — what it suggests for OpenWiki

Twelve systems — six in a first round, then Hindsight, MIRIX, AriGraph, Nemori and memory-champ from the world-model
shortlist, and Hermes Agent on request — read from their source in October 2026, plus one design report (§13). Where OpenWiki stands out: a real time model (only Graphiti matches it; Hindsight has event and
mention time; Mem0 keeps time-aware retrieval on its hosted platform; Cognee has a query-time window),
deterministic hygiene against poisoning (of the twelve, only Hermes Agent has a comparable policy — a threat-pattern
scan on memory writes), policy-based forgetting,
local-first operation on a 30B model, and audited measurement. Where it lags, the same gaps recur — and Hindsight
shows how large the distance can be (83.6 % on LongMemEval with a 20B open model):

| Theme | Seen in | OpenWiki today | Candidate | Cost |
|---|---|---|---|---|
| Credentials in memory | Mem0, Cognee, Hindsight (redaction before storage), Hermes (a hardcoded-secret pattern blocks the write); MIRIX stores them on purpose | **fixed in v0.99** — before, a pasted key could become an injected fact | redaction before capture and in `remember()`, the journal, `wiki_remember` and the handoff — done | — |
| Readable, portable memory | waku (`MEMORY.md`), Letta (git), Cognee (COGX) | memory lives only in a Kuzu file — archived upstream (R10) | a COGX export + a git-tracked Markdown view, written at `sleep` | small–medium; mitigates R10 |
| When to inject | waku (model gate), Mem0 (first prompt + pull), Letta (core + index + pull), Cognee (per file read), Hindsight (every prompt + one reflect per session; page count, not titles), Hermes (once per session, frozen for the prompt cache; recall skipped for trivial prompts) | 16 facts on every prompt — including release chores, 16 % of our prompts; a score gate failed (§1) | skip chore prompts; inject on the first prompt and after compaction, an index of themes, facts on file reads; `wiki_memory` for the rest | small; judged by real sessions |
| BM25 next to embeddings | waku, Graphiti, Mem0, Cognee, Hindsight (+ graph, time range, cross-encoder) | memory recall is dense-only | BM25 + rank fusion in `recall` | small; a paired LoCoMo re-answer |
| Capture during a session | Mem0, Letta, Cognee, LangMem, Hindsight (write-back every turn), Hermes (a background review every 10 turns) | at session end / compaction | debounced idle capture through the detached worker | small–medium |
| Writes land during a session | Hermes and Letta (plain writes, immediate) | **fixed in v0.100** — before, writes queued while the MCP server held the graph read-only for the whole session (144 facts waiting at the first v0.98 handoff) | readers hold the graph per call, writers lock only to apply (two phases), `wiki_remember` folds at once — done | — |
| Capture coverage | Nemori (segments the whole stream), Cognee (a watermark per session), Mem0 (every batch) | **fixed in v0.97** — before, the hook read only the last 20,000 characters per capture (~13 % of a long session) | every turn since a per-session watermark, in bounded windows — done | — |
| Raw sessions searchable | Graphiti, Letta, Mem0, Cognee, Hindsight, AriGraph (episodes ranked by overlap with retrieved facts), Hermes (FTS5 over every message, no LLM; automation demoted) | transcripts stay outside the memory tier | a session search tool for agents | medium |
| A curated always-present core | waku, Letta, LangMem, Hindsight (mental models), Hermes (two files, 3,575 characters) | a hand-written identity string | user-sourced conventions in the identity tier, entering only by human approval (memory-champ's gate; Hermes stages writes for approval — our journal could too) | small; must pass the poisoning set |
| Richer context than atomic facts | waku (episodes), Graphiti (sagas), Mem0 (rich memories), Cognee (lessons with reasons), Hindsight (5W facts), Nemori (dated narrative episodes) | atomic subject–predicate–object facts | episode summaries or a detail sentence per fact | medium; a paired LoCoMo run |
| Write-time model judgments | Mem0 dropped them; Letta and LangMem rely on strong curators; Hindsight confines them to a derived layer over immutable facts | two LLM checks in the merge (attribute resolution, coexistence) | an add-only ablation on LoCoMo — do they earn their place? | small; replay saved captures |
| Time in the question | Cognee (query-time window), Mem0 platform, Hindsight and MIRIX (rule-based date parsers) | recall ignores times in the query | boost facts whose validity overlaps a window extracted from the question | small; LoCoMo temporal |
| Multi-hop recall by expansion | AriGraph (semantic BFS over triplets), Graphiti (BFS from entities), Hindsight (graph links), Cognee (graph completion) | memory recall is single-hop similarity (the wiki side has GraphRAG expansion) | expand from the recalled facts through shared subjects / objects, thresholded, depth 2 | small; LoCoMo multi-hop |
| Typed memory | waku (facts / episodes / skills / persona), Letta (core / deferred / skills), Hindsight (world / experience), MIRIX (six purpose types), memory-champ (episodic / semantic / procedural, per-type write gates) | one untyped fact store (`source` tags only) | a type tag at capture, per-type recall budgets | small–medium |
| Procedural memory from errors | Mem0 plugin (failed commands with fixes), MIRIX (tool errors → skills), Hermes (skills from corrections and fixes — never negative tool claims or unresolved failures) | tool output stripped from capture | capture bounded failure → fix pairs | medium |
| Staleness by volatility | memory-champ (stable / slow / volatile facts, rechecked after 365 / 90 / 14 days) | stale "current" facts persist until restated or corrected via `wiki_remember` (D12) | a volatility tag at capture; volatile facts flagged "possibly outdated" after a few weeks — a phrasing rule caught 12/14 labeled stale facts at 10 % of memory flagged, where co-change dependencies were at chance (§13, in-sample) | small |
| Unicode evasion of the policy | Hermes (NFKC folding, invisible and bidirectional characters) | **fixed in v0.99** — before, the regexes matched the raw text | NFKC, zero-width characters removed, bidirectional overrides refused — done | — |
| Unresolved conflicts shown | MIRIX (keep both, note the discrepancy), memory-champ (surfaced, never resolved) | the merge closes or keeps silently | a "disputed" mark in the context | small |
| Learning from use | the transfer-entropy report (§13: directed, decaying edges from activity), Hermes (skill usage counts drive the curator) | `REINFORCES` edges from GraphRAG expansion — none in the coding workflow, which never calls `wiki_ask` | a use signal the workflow produces — git co-changes for code corpora, facts an answer uses — before any learning-from-use method | small to measure |
| Memory writes documents | Hindsight (five knowledge pages per repository), Letta (memory as Markdown) | the wiki is built from documents only; memory writes no pages | "Decisions" / "Conventions" pages regenerated from facts at `sleep`; git history as a capture source | medium |

**The order** in which we take these up — deterministic fixes, then paired LoCoMo experiments, then changes judged in
real sessions — is in the summary: [`agent-memory-summary.md` → What we will do](agent-memory-summary.md#what-we-will-do--in-order).


---

## Candidates — world models and CoALA

A shortlist from a search on 2026-10-03 for projects that build world models from incoming data and organize memory
the CoALA way, as `G:\Claude\Cognitive Substrate` does (episodic / semantic / procedural memory plus a canonical
ground-truth tier, consolidation as a sleep cycle, perception from many sources). Reviewed ones link to their section.

**Closest matches — for full reviews**
1. **Hindsight** — world facts apart from the agent's experiences, evidence-backed beliefs, mental models; per-repo
   memory for coding agents from git history and sessions. → [§7](#7-hindsight)
2. **MIRIX** — six memory types (core, episodic, semantic, procedural, resource, knowledge vault), each managed by
   its own agent, fed by continuous screen observation. → [§8](#8-mirix)
3. **AriGraph** — the most literal world model from observations: a semantic knowledge graph of the current state plus
   episodic nodes linking each observation to its triples. → [§9](#9-arigraph)
4. **Nemori** — topic-segmented narrative episodes, semantic memory learned by *predict–calibrate* (store only what
   existing memory failed to predict). → [§10](#10-nemori)
5. **memory-champ** — CoALA as an MCP service with per-type write gates; procedures behind a signed human approval.
   → [§11](#11-memory-champ)

**Brain-inspired and theory**
6. **[HippoRAG 2](https://arxiv.org/abs/2502.14802)** (ICML 2025) — the LLM as neocortex, a knowledge graph with
   Personalized PageRank as hippocampus; non-parametric continual learning over documents.
7. **[Human-Inspired Memory Architecture for LLM Agents](https://arxiv.org/abs/2605.08538)** (Microsoft Research) —
   sleep consolidation, interference-based forgetting, engram maturation, reconsolidation on retrieval, an entity
   graph, multi-cue retrieval; a streaming LongMemEval evaluation (475 sessions, ~540 K turns).
8. **[The Missing Knowledge Layer in Cognitive Architectures](https://arxiv.org/abs/2604.11364)** — argues CoALA lacks
   a knowledge layer with its own persistence: facts are superseded, never decayed; applying decay to factual claims
   is a category error — a challenge to our recency factor in recall.
9. **[OpenCog Hyperon](https://arxiv.org/abs/2310.18318)** + **[Hyperon-MCP](https://glama.ai/mcp/servers/amiroussama/Hyperon-MCP)**
   — a symbolic metagraph world model with probabilistic logic inference; symbolic memory with inference for coding
   agents.
10. **[MemOS](https://github.com/MemTensor/MemOS)** ([paper](https://arxiv.org/abs/2507.03724)) — memory types below the
    prompt: plaintext, activation (KV cache) and parametric (LoRA) memory under one scheduler.
11. **Generative Agents** (Park et al., 2023) — the memory stream, reflection into beliefs, planning: the archetype
    CoALA draws on.

**Trends and references.** Between-session "dreaming" consolidation is spreading (OpenDream, Opencode-Dreams,
clawdreamer; Anthropic's Dreaming for Claude Managed Agents, research preview since May 2026). A second exchange
format besides COGX: [memorywire](https://arxiv.org/abs/2606.01138). No open-source project was found that combines an
LLM world model with OPC UA or sensor telemetry the way Cognitive Substrate does — that work is academic (industrial
digital-twin architectures). Lists: [Awesome-Agent-Memory](https://github.com/TeleAI-UAGI/Awesome-Agent-Memory),
[Agent-Memory-Paper-List](https://github.com/Shichun-Liu/Agent-Memory-Paper-List),
[Awesome-GraphMemory](https://github.com/DEEP-PolyU/Awesome-GraphMemory).
