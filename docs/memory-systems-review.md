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
