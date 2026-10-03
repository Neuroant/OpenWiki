# Agent-memory systems — a comparative review

OpenWiki's memory tier (Path B, [`path-b-memory.md`](path-b-memory.md)) compared with other agent-memory
projects, one at a time: what each one does, how it differs from ours, what we can learn from it — and,
where a lesson can be checked cheaply against our own data, the check. Each project is read from its source
code and docs, not its marketing, and measured against the same yardstick.

**The yardstick:** purpose and runtime · store and data model · capture (write path) · retrieval (read
path) · time and contradictions · consolidation · forgetting and hygiene · identity and procedural memory ·
agent writes · surfaces and sharing · evaluation.

## Contents
1. [waku-agent](#1-waku-agent) (reviewed 2026-10-03)
2. [Zep / Graphiti](#2-zep--graphiti) (reviewed 2026-10-03)

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
