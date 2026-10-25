# Path C — World Models (design proposal)

> **Status: proposal (2026-10-18, OpenWiki v0.120.0). Nothing is built.** **The main architectural plan is §2.1** —
> the world model as a dynamical system with two boundaries, approved on 2026-10-18 (arc42 ADR-50); §2.2 adds that a
> twin is a program and that language models work at design time while the twin runs as code (ADR-51); ADR-52 makes
> Paths B and C hybrid — local models where possible, cloud models where really needed; §2.3 places the world model in
> the project concept (ADR-53) and decides how it is deployed — a scheduled `owiki world observe` between sessions,
> twins as running services reached by URL (ADR-54). The next step is
> C0, an inventory that needs no code and decides whether the rest is worth building. **The implementation is tracked**
> in [`path-c-roadmap.md`](path-c-roadmap.md) — phases, work packages, gates, status. Path C builds on Path B ([`path-b-memory.md`](path-b-memory.md))
> and the agent-memory review ([`memory-systems-review.md`](memory-systems-review.md),
> [`agent-memory-summary.md`](agent-memory-summary.md)); for machines it pairs with the sibling project **Cognitive
> Substrate** (`G:\Claude\Cognitive Substrate`). Its test playground is the working digital twin of Ableton Live in
> the sibling project **Ableton Expert System** (§11.5). As with every earlier step, each stage names how it is
> measured and when it stops.

*References:* **PB §** points to [`path-b-memory.md`](path-b-memory.md), **MSR §** to
[`memory-systems-review.md`](memory-systems-review.md); a plain § is a section of this document.

**In one paragraph.** Path B remembers what was *said*. Its weak spot is state that changes without anyone saying so:
versions, counts, running services, defaults, plans that got done. Path C lets the world write too. What can be
observed is observed — by built-in sensors over git, files, code, tests and local services, and for machines over
OPC UA — and recorded as facts the memory already knows how to merge (B7). What can't be observed — decisions,
reasons, plans, preferences — stays remembered, now linked to the state it is about. Rules from documents and from
history (*laws*) are checked against the observations; predictions about changes are logged and resolved;
contradictions, violations and misses become first-class *events*. At the centre is one loop — predict, act, observe,
compare, correct (§3.4) — that turns every observation into a test of the model and corrects automatically only what
is deterministic. The agent's context becomes a slice of that model: what a prompt touches, its current state, the
rules that bind it, what surprised us lately, and what was decided about it.

## Contents

1. [Motivation & main idea](#1-motivation--main-idea)
2. [What "world model" means here](#2-what-world-model-means-here) — including
   [2.1 The world model as a dynamical system — the architectural plan](#21-the-world-model-as-a-dynamical-system--the-architectural-plan),
   [2.2 Digital twins are programs — and where language models work](#22-digital-twins-are-programs--and-where-language-models-work) and
   [2.3 Projects and environments — where a world model lives](#23-projects-and-environments--where-a-world-model-lives)
3. [Conceptual model](#3-conceptual-model) — including [3.4 The loop](#34-the-loop)
4. [Target architecture](#4-target-architecture)
5. [Proposed data model](#5-proposed-data-model)
6. [Sensors and observations](#6-sensors-and-observations)
7. [Laws, decisions and drift](#7-laws-decisions-and-drift)
8. [Predictions and surprise](#8-predictions-and-surprise)
9. [The world slice](#9-the-world-slice)
10. [Twins, the MCP contract and Cognitive Substrate](#10-twins-the-mcp-contract-and-cognitive-substrate)
11. [The physical case — the LCU 9000 and the Ableton playground](#11-the-physical-case--the-lcu-9000-and-the-ableton-playground)
12. [Staged delivery plan (C0–C4)](#12-staged-delivery-plan-c0c4)
13. [Evaluation strategy](#13-evaluation-strategy)
14. [Risks & guardrails](#14-risks--guardrails)
15. [Decisions](#15-decisions)
16. [Relationship to the current code](#16-relationship-to-the-current-code)
17. [Prior art](#17-prior-art)

## 1. Motivation & main idea

### 1.1 The problem Path B cannot solve on its own

Path B made the graph an agent's memory, and it works: memory across sessions, facts in time, hygiene against
poisoning, each part measured before it shipped. Its weak spot is **state** — a fact that was true when said and is
outdated by later work:

- **The successor is missing.** Of 14 stale facts labeled on 2026-09-26, 12 had no newer fact anywhere in memory to be
  replaced by (PB §13.4): transient states have no successor by nature, and the sessions that changed a
  state did not restate it.
- **The local model can't tell.** Four attempts to have it judge memory — the poisoning audit, the forgetting review,
  a wiki check and update-aware capture — failed the same way (PB §13.1–13.5): it cannot tell "no longer true" from
  "related". Update-aware capture flagged 82 % of its candidates.
- **A list, not a fix.** On blind labels 1 in 4 facts of the volatile kinds — plans, counts, gaps, versions, running
  states — was stale (PB §13.26). On 2026-10-18 the dev memory holds 332 current facts of those kinds (18 %), 199 of them
  versions; `analyze memory --review` lists them for a person.
- **The writer has to remember.** `wiki_remember` (ADR-33) fixes stale state at the source when the agent records the
  change it makes. That depends on the agent's diligence and covers only what the agent itself changed.

Of the four stale facts PB §13.4 quotes, three describe something a sensor could read: the web UI's "six tabs" (a count
of the tab buttons in `index.html`), "openwiki-dev has backfill running" (a live lock file), "no memory tier in real
projects" (`[memory] enabled` in the manifests). The fourth, "U7 streaming chat is the remaining large item", is a
plan; only a person or a deadline settles it. And a current example: on 2026-10-18 the memory's release fact still
read "v0.120.0 (committed locally as f698515, not pushed yet)", a day after the push. Nobody said otherwise, so the
memory could not know. A git sensor would have.

### 1.2 The idea: let the world write too

Observe what can be observed, remember what can't, and check each against the other.

| Shift | Path B today | Path C |
|---|---|---|
| **From said to observed** | every fact comes from a conversation, captured or written by the agent | state comes from sensors — git, files, code, tests, local services, OPC UA; conversation keeps decisions, reasons, plans, preferences |
| **From recall to prediction** | the memory answers "what was said about X?" | it also answers "what happens if …?": which files a change touches, which tests break, what the coolant does at 4,500 W |
| **From storage to a loop** | capture → merge → recall; the nightly pass summarizes and forgets | perceive → model → predict → act → observe → correct; the nightly pass also checks laws and resolves predictions |

**The consequence that matters most.** Today a fact can only be contradicted by a later conversation; in a world model
it can be contradicted by the world. That gives the memory a core that can be proven wrong — exactly what its stale
facts lack — and changes how it is judged: by stale facts in context, predictions that came true and faults diagnosed,
next to recall.

### 1.3 What stays, what changes

- **Stays:** the bi-temporal fact store (B7, ADR-27) and fact identity (B9, ADR-29); the hygiene policy and credential
  redaction (ADR-30, ADR-37); local-first operation — local models where possible, cloud models where really needed
  (ADR-2, ADR-52); measure before adopting (ADR-9); readers that hold the
  graph per call and writers that lock only to apply (ADR-38).
- **Changes:** where state comes from, how content is typed (by how it is checked, §3.1), what the context holds (§9),
  and how the memory is judged (§13).
- **Paradigm or evolution?** For a code repository it is an evolution of Path B: the same store gains sensors, laws and
  predictions. The change is in purpose and yardstick — from "remember the conversation" to "keep a model of the
  environment that conversations update and the world can falsify". For machines it is a new class of system, a
  cyber-physical twin, to which OpenWiki contributes its document and memory brain. The review found no open-source
  project that combines an LLM world model with OPC UA or sensor telemetry (MSR, *Candidates*).

## 2. What "world model" means here

| Sense | What it is | For Path C |
|---|---|---|
| **Learned world models** (Ha & Schmidhuber 2018, DreamerV3, JEPA, Genie) | neural dynamics learned from large streams of observations, used to imagine and plan | not the core: the state must stay explicit and correctable, and learned dynamics need dense data a coding workflow rarely produces. Compute is not the limit — under the hybrid approach (ADR-52) cloud compute is there where really needed —, so where an environment yields dense data (a machine's telemetry, C4) a learned model may be trained, locally or in the cloud, and kept as one more prediction basis if it beats the declared and counted ones |
| **Digital twins** (industry) | a model of an asset kept in sync with it: state from sensors, history, simulation, a control interface | the operational model for machines; for code, a twin needs no learned physics — the real system can be run in a sandbox |
| **Cognitive world models** (CoALA; Path B's own frame) | the agent's internal model of its world, which it reasons with | what the memory becomes once it is grounded in observation |

**Working definition.** A world model is a set of **entities** with **observable state**, the **rules** by which that
state changes, and a **loop** that predicts, observes and corrects. It fits a code repository (files, configuration,
services, tests) and a laser control unit (coolant, power, interlocks) alike.

**How far along the ladder.** Kritzinger et al. (2018) separate a *digital model* (data flows by hand), a *digital
shadow* (data flows in automatically) and a *digital twin* (data flows both ways). Path C aims at a shadow first —
observation only — and at a twin only where acting is explicitly allowed (§10.2).

**Terminology.** OpenWiki's *world-model analysis* (Direction I, `owiki analyze`, ADR-25) measures the structure of
the gathered knowledge — where the graph and the embedding space agree. Path C models the *environment* that
knowledge is about. In this document "world model" means the latter.

The working definition is made precise in §2.1 — the plan the rest of this document details —, §2.2 says what a
twin is and where language models work, and §2.3 where a world model lives in a project.

### 2.1 The world model as a dynamical system — the architectural plan

> **The main architectural plan for Path C, approved on 2026-10-18 (arc42 ADR-50).** The rest of this document details
> it; where a later section seems to say otherwise, this section decides.

**The definition.** A world model is an internal dynamical system with a state. Projected onto an interface —
language, for agents and people — the state predicts the outside world. One step: an input arrives (an observation or
a statement); the state changes if the input carries new information; the state is projected onto the interface; the
output is a prediction and, through the agent, an action — whose effect arrives as the next input.

**Path B and Path C, side by side.**

*Path B — a knowledge model: what was said.*
- **Behaviour:** reactive. Its state changes only on stimuli — a captured session, an agent's write, the nightly
  `sleep`. Time only ages it: decay, recency for the kinds of fact that go stale, forgetting.
- **Structure:**

  ```
  knowledge model (graph + semantic index) ↔ language interface (capture, recall, wiki_remember)
      ↔ agents and people ↔ (the environment — only as described in words)
  ```

*Path C — a world model: what is, what follows, what was decided.*
- **Behaviour:** internal dynamics — a forward step (persistence, laws, co-change, sandbox runs) and an update (the
  merge; a mismatch becomes an event). Proactive in perceiving: on a schedule and without being asked, it keeps its
  state in sync with the twins, checks its laws and its open predictions, and lets time carry expectations — deadlines,
  settle times, persistence. Never proactive in acting: it proposes; the agent acts, checked by the model.
- **Structure:**

  ```
  environment ↔ digital twins (sensors + actuators + their own state, kept in sync)
      ↔ structured interface (state · history · act: typed values with time and quality)
      ↔ world model (state · laws · predictions · beliefs, stored in the graph; the semantic space as its index)
      ↔ language interface (world slice · MCP answers · UI)
      ↔ agents and people (the policy: decide · act · approve)
  ```

- **Relation to Path B:** Path C contains it. Its beliefs are Path B's facts, linked to the state they are about.

**The difference in one line:** Path B can only be told it is wrong; Path C finds out — because it keeps expecting
things, and checks.

**The architecture.**

```
                outside world: repository · runtime · Ableton set · machine
                    ▲ actions (gated)                     │ observations
                    │                                     ▼
    ┌───────── twins: actuators + sensors (+ their own state) ───────────┐
                    ▲                                     │
                    │ act: checked against laws           │ state(variable), events
                    │ and preconditions                   ▼
    ┌──────────────── OpenWiki world model ──────────────────────────────┐
    │ structure:  entities · variables · bindings         (rarely)       │
    │ parameters: laws · co-change · method scores        (slowly)       │
    │ state s(t): values · beliefs · predictions · events (fast)         │
    │ trajectory: every value's interval (B7)                            │
    │ forward F:  persistence · laws · co-change · sandbox runs          │
    │ update U:   the merge (observation wins) · events · closures       │
    └────────────────────────────────────────────────────────────────────┘
                    ▲ parse: words → variables, intents   │ project: state → text
                    │ (semantic space + BM25)             ▼ (world slice, MCP)
                agents and people: the policy (decide · act · approve)
```

**Seven principles.**
1. **Two dynamics.** A forward step runs the state ahead without input — every value persists unless an action
   touches it (the null prediction, §3.4.3), the laws say what must follow (§7), co-change says which files move
   together, and for code the real system runs in a sandbox (§8.1). An update corrects the state from an observation —
   the merge, in which an observation outranks a remembered statement (§3.3, §6.4). An observation that matches the
   prediction carries no news and only refreshes `last_seen`; only a mismatch changes the state and raises an event —
   only the errors travel.
2. **Three speeds of change, and the trajectory kept.** The *state* — current values, beliefs, open predictions and
   events — changes per observation (the fast and the session loops); the *parameters* — laws, co-change weights,
   method scores, settle times — change slowly (the nightly loop); the *structure* — which entities, variables, sensors
   and twins exist — changes rarely, by a person (§3.4.2). And the state's history is kept: every value's interval
   (B7), so any past state can be replayed.
3. **The semantic space is the interface, not the state.** The state is symbolic — variables, values, intervals,
   rules. The embeddings (and BM25) are the language interface's encoder and decoder: they find which variables a
   prompt, a remembered fact or a manual sentence is about, and which part of the state to render as text (§9).
   Similarity is not dynamics: 76 % of the files that change together are not each other's nearest embedding
   neighbours (`path-b-memory.md` §13.29), and about a third of the graph's other connections are no closer in the
   embedding space than a random pair (ADR-25).
4. **Twins are the boundary — a stateful interface that can fail — and the executable half.** A twin is the sensor and
   the actuator for one environment, often with its own state (the Ableton twin's own graph); the repository's "twin" is
   OpenWiki's own sensors. And a twin is a program: besides the boundary it runs the dynamics, the laws as tests and the
   loop (§2.2). The world model is composed: the shared part — beliefs, laws, predictions, relations across twins — in
   OpenWiki's graph, each environment's detail in its twin, reached through one contract (§10.1). A twin's state is an
   observation with a quality — complete or partial, fresh or old —, so a twin out of sync is the stale-fact problem one
   level down: only a complete, fresh snapshot may close a belief (§3.3, §11.5).
5. **The model predicts; the agent acts — and the model checks the action.** The world model outputs predictions,
   explanations and proposals; the coding agent, with a person where approval is required, is the policy; before an
   action reaches the world, the model checks its precondition (the same object as observed) and the laws as
   interlocks (§10.2). The model never acts on its own surprises (§3.4.6).
6. **Predict in the state space, speak in language.** A prediction is recorded structured — variable, expected value,
   tolerance, deadline, basis — so the comparison is exact (§3.4.4); only then is it rendered as text for the agent.
   Between twins and the model the interface is typed for the same reason.
7. **Time carries expectations.** Every value is expected to persist, every prediction has a deadline, every action a
   settle time. So "nothing happened" is information too: an expected change that doesn't come is a miss, a silent
   sensor leaves predictions unresolved (§3.4.4).

**One step, in the plan's terms.**

| Step | In Path C | Mechanism |
|---|---|---|
| Input | an observation (a sensor, a twin) or a statement (a conversation, `wiki_remember`) | observed facts (§5.1), captured facts |
| Compare with the expectation | the forward step predicted "stays as it is", or "becomes 0.82" | the null prediction, laws, predictions (§3.4.3, §7, §8) |
| Change the state if the input is new information | re-affirm when it matches; update and raise an event when it doesn't | the merge (§6.4), events (§8.3) |
| Project onto the interface | the world slice for a prompt, MCP answers for agents, the UI for people | `context_for`, the `state` / `explain` verbs (§9, §10.1) |
| Output a prediction | structured first (variable, value, tolerance, deadline), then rendered as text | `Prediction` (§5.2) |
| Output an action | the agent's decision, checked by the model before it reaches the world | preconditions, laws as interlocks, approval (§10.2) |
| …and the next input | the action's effect is observed and compared | the loop (§3.4) |

**In notation.**

```
state       s(t)   = values of the variables, beliefs, open predictions, open events
parameters  θ      = laws, co-change weights, method scores, settle times
forward     ŝ(t+Δ) = F_θ( s(t), a )          persistence · laws · co-change · sandbox runs
observe     o      = H( world )              sensors and twins — complete? fresh?
update      s(t+Δ) = U( ŝ(t+Δ), o )          observation outranks belief; a mismatch → an event
learn       θ      ← θ + slow updates         method scores automatically; laws only by a person
project     text   = g( s(t), question )      the world slice — semantic space + BM25 pick the part
policy      a      = π( text, goal )          the agent, with approval where required — outside the model
check       a ok?  = laws( ŝ after a ) ∧ precondition( a, s(t) )
```

**Why the core is not a learned world model.** Learned world models (Dreamer, JEPA, Genie) learn a continuous,
hidden state and its dynamics by gradient descent and imagine ahead. This plan's core is deliberately different: the
state is explicit and symbolic — every value can be read, dated and corrected; the dynamics are mostly declared or
counted (laws, co-change), because the memory's own history is too sparse to learn them from (MSR §13); for digital
environments the simulator is the real system in a sandbox — the world as its own best model, in Rodney Brooks'
phrase; and the language model is a prior, not the physics: it may propose predictions and explanations, which only the
loop confirms (§3.2). Compute is not the reason. Paths B and C take a hybrid approach (ADR-52) — local models where
possible, cloud models where really needed —, so where an environment yields dense data, a machine's telemetry above
all (C4), a learned dynamics model can join as one more prediction basis, trained locally or in the cloud and scored
against the baselines like every other basis (§3.4.4). And large language models are themselves learned models of the
world of text and code: through the coding agent and, where needed, cloud calls, they serve as priors — proposals the
loop confirms. What the core gives up is imagination in unseen situations; what it gains is a model that can be
checked, replayed and trusted around a codebase or a machine — and a regulator that contains a model of what it
regulates, as Conant and Ashby put it (1970).

### 2.2 Digital twins are programs — and where language models work

> **Part of the plan, decided on 2026-10-18 (arc42 ADR-51).** It widens §2.1's fourth principle: a twin is not only
> the boundary, it is the executable half of the world model.

**Two halves.** The world model has a declarative and an executable half:
- the **knowledge model** — the graph and the semantic index, holding facts, beliefs, laws as statements, predictions
  and history: *what we know and believe*;
- the **digital twin** — a program: sensors, actuators, a mirror of the environment's state, its dynamics, laws turned
  into runnable checks, a simulator, and the loop that runs them: *what we can run*.

The twin is to the knowledge model what a test suite is to a codebase. Every claim that can be tested becomes a test;
the twin runs the tests against reality on a schedule, as continuous integration runs a codebase's tests on every
change; a failure becomes an event; and the results consolidate the knowledge model.

**The loop between the halves.**

```
            ┌───────────── knowledge model (declarative) ─────────────┐
            │  graph + semantic index: facts · beliefs · laws as      │
            │  statements · predictions · history                     │
            └─────▲──────────────────────────────────────────┬────────┘
  results become  │ observations · check results ·           │ knowledge becomes code:
  knowledge       │ prediction outcomes (deterministic;      │ the large model writes it,
                  │ text parsed by the local model)          │ review and tests accept it
            ┌─────┴──────────────────────────────────────────▼────────┐
            │  digital twin (executable): sensors · actuators ·       │
            │  state mirror · dynamics · laws as tests · simulator ·  │
            │  the loop runner                                        │
            └─────▲──────────────────────────────────────────┬────────┘
                  │ observe                                  │ act (gated)
                                      environment
```

Two translations run in opposite directions. *Knowledge becomes code* — programming the twin: a law becomes a check, a
binding a sensor, a decision a constraint, a dynamic a simulator. *Results become knowledge*: observations become
facts, check results the laws' status, misses events, outcomes prediction scores.

**What a twin program contains.**

| Part | The repository twin | The Ableton twin (§11.5) |
|---|---|---|
| sensors | the version, the tags, the test count, Ollama's models | the extractor's snapshot and observers |
| actuators | none — read-only | the injector's fixed set of commands |
| state mirror | observed values with their history (§5.1) | the twin's own graph |
| laws as tests | the two version files agree; every release has a tag | volume at most unity unless boosted; output at most the power limit |
| simulator | the real code in a git worktree, with the tests at risk (§8.1) | a dry run, or a simple gain-staging calculation |
| loop runner | scheduled sensor reads, deadline checks, the nightly pass | observers, read-back after each write, periodic sync |

The Ableton review's six recommendations (R1–R6) are programming its twin in this sense: clamps, stable ids, a sync
that knows it is complete, read-back, more observers.

**Language models at design time, code at run time.** The large model's judgment is compiled into reviewed, tested
code, and the twin's loop runs without any language-model call — fast, deterministic, local, private, free per run.
- **Programming the twin — the large model.** Sensors, checks and simulators are hard to write and consequential: work
  for a frontier coding agent, the one already in the loop (the host agent, ADR-33). It does not contradict §3.2's
  rule: code is a proposal that can be verified by running it — a sensor that reads wrong disagrees with others, a check
  is tested on planted violations, a simulator against the real outcome. Cognitive Substrate's self-healing skills work
  the same way (its D17: the model proposes, Playwright validates, a failing skill is demoted). The large model is used
  once per change to a twin, not once per turn of the loop; "constantly" applies to the twin running, not to its
  programming, which happens in steps, in sessions, with review.
- **Building and maintaining the knowledge model — the local model, by default.** Where it is measurably not enough for
  a part of this work, the hybrid approach below applies. Language must be read and written at run time —
  capture, extraction, summaries, answers: high volume, private, in the background. The local model is weak at
  judgment (§3.2), and the twin catches its errors wherever the content is testable: when capture records "756 tests"
  and the test-count sensor reads 770, the fact closes by itself. The better the twin, the cheaper the model that builds
  the knowledge model can be. For what no twin can test — decisions, reasons, preferences — the existing guards stay:
  the policy, the agent's `wiki_remember`, a person.
- **Where the large model touches the knowledge model:** hard cases handed to the agent in a session — review lists,
  explanations of surprises, proposed laws — and, under the hybrid approach below, cloud calls where the local model
  is measurably not enough: a benchmark judge, extraction from public documents, a large one-off job. Private memory
  goes to a cloud model only by the project's explicit opt-in, after redaction.

| Work | Who | When | Verified by |
|---|---|---|---|
| programming the twin — sensors, laws as tests, simulators, the loop | the large model (the coding agent) | design time, in sessions | review, tests, planted violations, running it |
| running the twin — observe, check, predict, compare | code, no language model | run time, on a schedule | cross-checks, fixtures |
| building and maintaining the knowledge model — capture, extraction, summaries, embeddings | the local model (Ollama) by default; a cloud model where really needed (ADR-52) | run time, in the background | the twin for testable content; the policy, the agent and people for the rest |
| hard judgments — staleness, explanations, proposed laws | the large model (the agent in sessions, or a cloud model where needed) or a person | in sessions, or as a measured batch job | the twin, where testable |

**Four tiers.**

| Tier | Who | Does |
|---|---|---|
| 0 — no language model | rules and code | the merge, the policy, forgetting, sensors, comparisons, law checks, interlocks, the world slice, the twin's loop |
| 1 — local models | OpenWiki in the background (bge-m3, the 30B) | embeddings; capture; extraction; the narrow merge checks; summaries; answers in the wiki |
| 2 — large models | the coding agent, in sessions; cloud models OpenWiki calls where really needed (ADR-52) | programming the twins; decisions and actions; recording new states; explanations; proposed laws; review lists; a benchmark judge; large one-off extraction |
| 3 — people | the user | accept laws, approve actions and twin code, confirm or close facts |

**Decided on 2026-10-18.**
1. **Where twin code lives.** The generic machinery — the built-in sensor types (§6.1), generic law checkers, the loop
   runner, the twin contract (§10.1) — in OpenWiki; each environment's twin code in that environment's repository: the
   repository twin's in OpenWiki, the Ableton twin's in the Ableton Expert System, the LCU 9000's in Cognitive
   Substrate. Every twin beyond the built-in sensors runs as its own process behind the twin contract, and OpenWiki
   never imports environment code (§2.3).
2. **Every change to twin code goes through a reviewed commit.** Read-only sensors and checks may later be relaxed to
   land without review, once their fixtures have proven them; actuators never.
3. **Declarative first.** Laws are data with generic checkers (§7.2); a reviewed test function only where an
   expression can't say it — less code to maintain, and the laws stay readable.
4. **Hybrid language models** (decided 2026-10-24): local where possible, cloud where really needed — the conditions
   below (ADR-52).

**The hybrid approach (ADR-52, decided 2026-10-24).** Local-only inference stays Path A's principle (ADR-2): the
document pipeline never leaves the machine. Paths B and C are hybrid — a local model wherever it is enough, a powerful
cloud model where it is really needed. A task may be routed to a cloud model when all of these hold:
- **a measured need** — a paired measurement shows the local model isn't enough for the task, or the task is too large
  for it in the time it has;
- **the data class allows it** — public data freely; private memory only with the project's explicit opt-in, and after
  credential redaction;
- **the result is verifiable** — by a twin, a check, review or a person — or the model is an audited evaluation judge;
- **a local fallback exists** — offline means degraded, never broken;
- **the cost is visible** — the model, its version, the tokens and the latency are recorded per task.

Never inside a twin's loop (ADR-51), and not on the per-prompt path unless a project configures it. Cloud models sit
behind the same `ChatModel` / `Embedder` protocols as the local ones; keys come from the environment and are never
stored or logged. The coding agent stays the first large model in the loop. The routing is track L of
[`path-c-roadmap.md`](path-c-roadmap.md).

**Who tests the tester.**
- Every check must fail on a planted violation; a check that can't fail is reported, never trusted.
- A variable measured twice is cross-checked — the two version files are such a pair.
- Laws are mutation-tested in a scratch copy: change the value, and the check must fire.
- A law whose text changed while its check didn't — or the reverse — raises a drift event.

**Testable share — the knowledge model's test coverage.** The share of facts and laws a twin can test is a headline
measure next to a stale belief's lifetime (§13): the larger it is, the more of the knowledge model is held to the
world. C0's inventory (§12) is its first measurement.

### 2.3 Projects and environments — where a world model lives

> **Decided on 2026-10-24 (arc42 ADR-53).** The project concept ([`projects.md`](projects.md)) checked against the plan:
> it holds. Path C adds a third input class and a runtime role next to `build`. Where it runs was decided on 2026-10-25
> (ADR-54, *Where it runs* below).

**What a project is today.**

| Part | Role |
|---|---|
| a folder with `openwiki.toml` | identity and settings — flag > manifest > user config > default (ADR-10) |
| `[[sources]]` — PDF, Markdown, URL, a code repository | *what is written*: the document tier — static, rebuilt at build time through the fingerprint chain (ADR-11) |
| sessions — transcripts, hooks, backfill, agent writes | *what is said*: the memory tier — captured over time; the graph is its only copy, so rebuilds keep it (ADR-16) |
| one Kuzu graph | both tiers; one writer at a time (ADR-38) |
| modes | Wiki (documents only) or Second Brain (with memory) |
| when things run | `build` (batch), hooks (events), `sleep` (nightly), `serve` and MCP (on demand) |
| a repository bound to a project | `claude-code --hooks --into`: the repository's sessions feed the project's memory |

**A third input class.**

```
   sources      ── build time ───────▶  document tier   (what is written — rebuildable)
   sessions     ── capture time ─────▶  memory tier     (what is said — authoritative)
   environments ── observation time ─▶  world tier      (what is — authoritative)
                                        └── one graph per project, three clocks
```

Modes nest — Wiki ⊂ Second Brain ⊂ World model: `[world] enabled` requires `[memory] enabled`, since observations are
memory-tier facts and the beliefs live in the memory tier.

```toml
[world]
enabled = true                                   # requires [memory] enabled

[[environments]]                                 # the repository twin: built-in sensors only
name   = "openwiki"
kind   = "repository"
source = "G:/Claude/OpenWiki"                    # the code source with this path — defined once

[[environments]]                                 # a twin: a running service behind the twin contract
name      = "live-set"
kind      = "twin"
url       = "http://127.0.0.1:8770/mcp"          # its MCP server (P.7), started by its own backend
token_env = "LIVE_SET_TWIN_TOKEN"                # the variable that holds its token — never the manifest
```

Sensors name the environment they read (`environment = "openwiki"`, §6.2).

**What already fits.**
- **The project is the world model's home.** openwiki-dev already is the repository twin's home: the OpenWiki
  repository is its code source, its hooks route the repository's sessions to it, and its sensors would read the same
  repository.
- **Settings precedence** carries the new configuration — `[[environments]]`, `[[world.sensors]]`, `[models.tasks]`
  for the hybrid routing (ADR-52), keys from the environment.
- **The hooks are the loop's triggers:** `SessionStart` observes at a session's start, `UserPromptSubmit` runs the
  read-time overlay (§6.5), `PreCompact` and `SessionEnd` the slow sensors, `PostToolUse` records actions (C3).
- **`sleep`** is the slow loop's home (§3.4.2).
- **The registry and the project switcher** keep several world projects side by side.
- **Session search** gives the evidence for explanations: what was said when something changed.
- **Per-project eval sets, the metrics collector and build observability** carry the new measures — testable share, a
  stale belief's lifetime, the cost per task.

**Eight adjustments** — each a work package in [`path-c-roadmap.md`](path-c-roadmap.md):
1. **Environments in the manifest.** `[[environments]]` — a name, a kind (`repository` or `twin`), a source's path or
   a running twin's URL and token variable (ADR-54); sources stay static, environments are live (C1.2).
2. **Rebuilds keep the world tier.** Tables declare themselves authoritative in one registry that B0's snapshot /
   restore reads, instead of a hand-kept list — a table forgotten in that list would be lost at the next build (C2.1).
3. **Sessions get a kind** — conversation, agent, sensor, twin, import — so the Sitzungen list and session search keep
   showing conversations (C1.12).
4. **Write on change, not per reading.** Readings stay in the world cache; the graph is written when a value changes,
   and `last_seen` is synced at `sleep` — §2.1's "only the errors travel", applied to writes on a one-writer graph
   (C1.5).
5. **OpenWiki is the twins' client, not their host.** It never imports environment code: every twin beyond the
   built-in sensors runs as its own process behind the twin contract (MCP) — the Ableton twin's server, Cognitive
   Substrate's LCU 9000 server, a custom server in a repository —, declared in the manifest by its URL: OpenWiki
   connects to the running twin and starts only twins it owns (ADR-54). Process isolation, any language, and the rule
   that a manifest adds no code (§6.6) still hold (C2.10, P.7).
6. **Actions go through OpenWiki's `act`.** It checks the precondition and the laws, records the `Action` and its
   `Prediction`, forwards the action to the twin's own guarded `act` — whose interlocks stay: defence in depth — and
   resolves the prediction by the read-back. An agent calling a twin directly would bypass all three (C3.9).
7. **Actions arrive from any agent:** git commits, an MCP tool an agent reports an action with, and host hooks such as
   Claude Code's `PostToolUse` where they exist (C3.1).
8. **One probe implementation.** From C1 on, the handoff reads the world model's observed state, events and open
   predictions instead of probing on its own; its brief at a session's start becomes a world slice (C1.13).

**Two synergies.**
- **The project observes itself.** A code source's wiki is a snapshot that drifts from the repository — the last
  handoff note found the dev index 62 commits behind. Wiki freshness becomes an observed variable with a law ("at most
  N commits behind"), and a violation proposes `owiki build`: the build pipeline becomes part of the loop.
- **Live state on wiki pages** — reader overlays like the related-pages panel (ADR-28): a code file's page shows its
  observed constants and the laws on it; a manual's page shows a parameter's live value from a twin.

**How the projects would look.**

| Project | Environments | Sources | Sessions |
|---|---|---|---|
| openwiki-dev | the OpenWiki repository (built-in sensors) | the repository as code | Claude Code sessions in the repository |
| ableton-playground (new) | the Ableton Live set, through the Ableton twin's MCP server | the Ableton 12 manual | the playground's sessions |
| ableton-dev (optional) | the Ableton Expert System's repository | its code | Claude Code sessions in that repository |
| lcu-9000 (C4) | the LCU 9000, through Cognitive Substrate's twin | the LCU 9000 manual | operator sessions |

The Ableton Expert System has two environments: its codebase — a repository someone develops — and the Live set it
controls — the twin, our playground (§11.5). They belong to different projects.

**Where it runs** (decided on 2026-10-25, arc42 ADR-54 and §7.5). On the machine of its environments, and still without
a resident OpenWiki service — every process is one-shot or on demand, as today:

| Process | When | Its part of the loop |
|---|---|---|
| the hooks (Claude Code) | during a session: each prompt, a session's start and end | the overlay (fast sensors), the session's observations, the action log (C3) |
| `owiki world observe`, scheduled | every few minutes, between sessions | the sensors whose period is due; writes on change; resolves the predictions that fell due |
| `owiki sleep`, scheduled | nightly | the slow loop — laws re-checked, prediction bases re-scored; a rolling backup |
| `owiki serve`, `owiki mcp` | on demand | read the world model; approvals in the Pflege panel or from the CLI |
| a twin | always — started by its own system | its fast loop; its state over MCP on localhost |

- **The loop between sessions is a scheduled `owiki world observe`**, installed with the OS scheduler like `sleep`
  (Task Scheduler or cron, pinned to the hooks' interpreter; `owiki schedule`, track O). Not inside `serve`: the web UI
  runs only while someone has it open, and the loop must not depend on that.
- **Twins are running services, reached by URL.** A twin owns its environment — the Ableton knowledge server listens on
  UDP 11001 and holds its own graph file; a second instance started by OpenWiki would collide on both. So a `twin`
  environment names its `url` (MCP over Streamable HTTP, `127.0.0.1` only) and the environment variable that holds its
  token; OpenWiki connects to it and never supervises it. A twin that doesn't answer is a failed reading: its beliefs
  go stale, they don't change. `command` remains for twins OpenWiki owns, such as a simulator a test starts and stops.
- **The world tier runs natively.** Sensors read the host's git, files, processes and GPU; in the Docker image they
  would see only the container. Docker stays for document-only setups and CI.
- **Keys and other machines.** Cloud keys (ADR-52) and twin tokens come from the user account's environment — a
  scheduled task doesn't inherit a session's. Twins on other machines wait for network transport, TLS and
  authentication (Direction F).

**What does not change:** one corpus per project; discovery by location, with the registry as the fallback; the
fingerprinted build chain — environments stay outside it, as the memory stage does; Wiki mode for document-only
projects.

## 3. Conceptual model

### 3.1 Five kinds of content, typed by how they are checked

| Kind | Digital example | Physical example | Checked by | Lives in |
|---|---|---|---|---|
| **Entity** | the file `openwiki/graph/store.py`, the `sleep` subcommand, the Ollama runtime | the LCU 9000, its chiller, the delivery fiber | the sources (rebuilt) | the document tier, plus `Variable` nodes (§5.2) |
| **State** | the release in `pyproject.toml`; `RECENCY_FLOOR = 0.9`; the models Ollama has pulled | coolant flow rate, back-reflection level, interlock status | **sensors** — observe again | the memory tier, `source = "observed"` |
| **Law** | "the version in `pyproject.toml` equals `openwiki.__version__`"; "keep `--split-level` the same for index and graph-build"; `cli.py` co-changes with `CLAUDE.md` | "output never exceeds the Max Output Power Limit"; "door open or chiller alarm → emission severed" | **data** — observations checked against it | a new `Law` layer |
| **Belief** | decisions, reasons, plans, conventions, preferences | an operator's decision, e.g. "cap at 4,000 W for brass" | **people** — the Pflege panel, approval | the memory tier (Path B as it is) |
| **Prediction** | "this change will also touch `CLAUDE.md` and `test_cli.py`" | "at this duty cycle the coolant temperature passes its limit within ten minutes" | **time** — resolved by a later observation | a new `Prediction` layer |

Typing by verification route is more useful here than CoALA's semantic / episodic / procedural split: it says how each
kind is kept fresh and who may change it.

### 3.2 The local model proposes; it never verifies

Path B's audits found the local 30B unreliable as a judge of memory four times (PB §13.1–13.5), and its volatility tags
did worse than a phrasing rule (PB §13.26). Path C turns that into a rule: the model may *propose* — a candidate law, the
variable a fact is about, an explanation — but sensors, data, tests, simulators or people decide. It is also Cognitive
Substrate's invariant for acting: the LLM proposes, Playwright validates (its D15 and D17).

### 3.3 Authority

For an attribute a sensor governs (§6.2), sources rank
**observed > agent-recorded (`wiki_remember`) > user > assistant > material**. An observation may close a belief; a
belief never closes an observation — only a newer observation can. A contradicting belief is kept as history and
raises an event (§8.3). This is Cognitive Substrate's tier write-authority
(its CANONICAL tier can't be overwritten by distilled guesses), applied per attribute rather than per tier. Attributes
without a sensor keep Path B's rules unchanged.

### 3.4 The loop

The loop is what makes Path C a model rather than a shadow. Without it, observed facts would only sit next to
remembered ones; with it, the model makes claims, the world answers them, and the model is corrected — every
observation becomes a test. Every other part of Path C is a part of this loop: sensors perceive, the graph models,
predictions predict, the agent and the twins act, sensors observe again, and the merge, the laws and the nightly pass
correct.

| Step | In OpenWiki terms | Today |
|---|---|---|
| Perceive | sensors (§6) and capture | capture only |
| Model | the graph: entities, state, laws, beliefs | entities, beliefs |
| Predict | predictions for changes (§8), the null prediction (§3.4.3), laws as standing predictions (§7) | — |
| Act | the coding agent; twin actions behind guards (§10.2) — each recorded as an `Action` (§3.4.9) | the agent, unrecorded, unguarded by the memory |
| Observe | sensors again, commits, test runs, a twin's read-back | — |
| Compare | hit, partial, miss or unresolved (§3.4.4) | — |
| Correct | within the limits of §3.4.6: the merge closes stale beliefs (§6.4), scores and rule results are updated, events are raised | `sleep` forgets and summarizes |

#### 3.4.1 One turn, recorded

```
belief/intent ─▶ prediction ──▶ action ───▶ observation ──▶ comparison ──────▶ correction
what we think    what should    what was    what the world  hit · partial ·    close a belief
or want done     happen, by     done, by    says: sensor,   miss · unresolved  rescore a basis
                 when, and why  whom        time, complete                     raise an event
                                                                               ask a person
```

| Element | Holds | Recorded as |
|---|---|---|
| Belief or intent | what we think is true, or what someone wants done | a fact (Path B), or the task in the prompt |
| Prediction | the target, the expected value or set, a tolerance, a deadline, the basis, a confidence, who made it | `Prediction` (§5.2) |
| Action | the actor, the verb, the target, the arguments, the precondition, the approval, dry run or not, the confirmation | `Action` (§5.2) |
| Observation | the value, the time, the sensor, whether the snapshot was complete | an observed fact (§5.1) |
| Comparison | hit, partial, miss or unresolved, and the error | the prediction's outcome and score |
| Correction | what changed in the model | closed beliefs (`SUPERSEDES`), basis scores, rule results, events (§3.4.6) |

Every turn leaves a trace that can be followed in both directions, so *why the model changed its mind* always has an
answer — and *why the world is in this state* can be traced back to an action or to an outside change.

#### 3.4.2 Four loops, four speeds

```
┌─ slow loop · nightly (sleep) ────────────────────────────────────────────────┐
│  laws re-checked · prediction bases re-scored · decisions whose reasons      │
│  changed flagged                                                             │
│  ┌─ session loop · per task / commit ─────────────────────────────────────┐  │
│  │  plan → predict files and tests → work → commit → resolve → misses     │  │
│  │  become events                                                         │  │
│  │  ┌─ fast loop · per action (seconds) ───────────────────────────────┐  │  │
│  │  │  check the precondition → act → read back → verify               │  │  │
│  │  └──────────────────────────────────────────────────────────────────┘  │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────────┘
  human loop · as needed: review events, accept laws, approve actions (the Pflege panel)
```

| Loop | Closed by | Cadence | Repository | Ableton playground (§11.5) |
|---|---|---|---|---|
| Fast | the twin or a sensor | seconds | an edit → the tests at risk | a write → the value read back |
| Session | the agent and the commit | minutes to hours | companion files and tests predicted, resolved by the commit | "make the bass punchier" → the diagnostics run again |
| Slow | `sleep` | nightly | law results, basis scores, drift | the same, over the day's observations |
| Human | a person | as needed | accept laws, confirm or close facts | approve actions, accept laws |

Between sessions no agent closes the session loop; a scheduled `owiki world observe` keeps the observations current and
resolves the predictions that fell due (§2.3, ADR-54).

The inner loops are cheap and frequent and correct only what is deterministic; the outer loops are rare and correct
what needs judgment. This is the shape of MAPE-K from autonomic computing (monitor, analyze, plan, execute over shared
knowledge — §17); Path C adds explicit predictions and a recorded history of every turn.

#### 3.4.3 The null prediction

Every observed variable carries an implicit prediction: **it stays as it is, unless an action targets it.** That turns
every observation into a test:

- **A change with a matching action** is expected; it verifies the action.
- **A change without an action** is *exogenous* — a person, another program, or a dynamic the model doesn't know. It is
  an attribution, not an error: a producer moving a fader is normal. It becomes an event only when it breaks a law or a
  decision ("the bass fader is above the cap we decided").
- **A variable that keeps changing exogenously** is marked *externally controlled*: the model stops expecting
  persistence for it, and the agent stops acting on it without asking.

Attribution needs identity (the same object in the action and in the observation — the Ableton review's R2) and time
(actions with timestamps, a settle time per variable, §3.4.4). Before the action log exists (C3), git is the
repository's own action log: a value that changed in a commit is attributed to that commit, and a change outside any
commit — an uncommitted edit, a restarted service — counts as exogenous.

#### 3.4.4 Comparing: hit, partial, miss, unresolved

| Kind of prediction | Compared by | Outcome |
|---|---|---|
| a value ("the volume becomes 0.82") | the observed value within a tolerance | hit inside the tolerance; partial in the right direction outside it; miss otherwise |
| a set ("these files change", "these tests fail") | precision and recall against the observed set | hit when both are at least 0.8; partial; miss when nothing predicted happened (starting thresholds, set in C3) |
| a state ("the release has a tag") | match | hit or miss |
| a law (a standing prediction, §7) | its check on each new observation | held or violated |

- **Unresolved is not a hit.** A deadline that passes without an observation leaves the prediction unresolved, and
  repeated unresolved predictions for one sensor raise a sensor-health event — silence never confirms anything.
- **Settle time.** A prediction is compared only after the effect has had time to land: a read-back after a few hundred
  milliseconds, a commit at once, a restarted service after seconds.
- **Skill, not hit rate.** Each basis is scored against a baseline that needs no model — persistence for state, a file's
  base rate of change for companion files — the way weather forecasts are scored against climatology. A basis that
  doesn't beat its baseline is dropped (§8.2).

#### 3.4.5 Explaining a surprise, from the bottom up

A miss is explained by the cheapest rung that accounts for it:

1. **The sensor.** Was the snapshot complete and fresh? A partial sync explains many misses (the Ableton review's R4).
2. **The action.** Did it run, on the right object, and was it confirmed? A wrong target, a clamped value, an error.
3. **Another actor.** Did a person or another program change the value in between (§3.4.3)?
4. **The model.** Only now is the law or the prediction basis wrong — the one case that should teach the model.

The `explain` verb (§10.1) walks this ladder and returns the first rung that accounts for the miss, with its evidence.
The local model may propose hypotheses on the model rung, but a rung counts only when a check confirms it (§3.2).

#### 3.4.6 What the loop may correct by itself

| Correction | Who | Why |
|---|---|---|
| close a belief that a complete observation contradicts | automatic | deterministic; kept as history (§6.4) |
| re-score a prediction basis; record a law's result | automatic | statistics only |
| raise an event; mark a variable externally controlled | automatic, reversible | nothing is lost |
| accept, change or retire a law | a person | laws are few and consequential (§7.3) |
| change a decision | a person, or the agent through `wiki_remember` as today | decisions are beliefs |
| **act on the world because of a surprise** | **never automatic** — propose, dry run, approve | runaway loops, fighting controllers (§3.4.7) |

This line is the loop's learning rate: the inner loops adapt only what is deterministic, and everything that needs
judgment waits for a person.

#### 3.4.7 How a loop fails

| Failure | Symptom | Guard |
|---|---|---|
| acting on a stale model | a write lands on the wrong object | every action carries its precondition — compare before write (§10.2) |
| silence taken as confirmation | a sensor drops out; the deadline passes | unresolved is not a hit; a sensor-health event (§3.4.4) |
| fighting another controller | a value flips between the agent's action and an outside change | a *contested* event; the agent stops acting on the variable |
| runaway corrections | surprise → action → surprise | no automatic remediation (§3.4.6) |
| self-confirming predictions | trivial hits, or the agent edits the test it predicted would pass | skill against a baseline, not hit rate (§3.4.4) |
| gaming the score | only easy predictions are made | coverage (actions with a prediction) reported next to skill (§13) |
| resolving too early | read before the effect has landed | a settle time per variable (§3.4.4) |

#### 3.4.8 Two turns, end to end

**The repository: "release v0.121.0".**
- *Predict:* the companion files from co-change and the release law — `pyproject.toml`, `openwiki/__init__.py`,
  `CLAUDE.md`, `docs/roadmap.md`, arc42 §10; the release variable becomes 0.121.0 at the commit; the convention "every
  released version has a tag" gets a deadline.
- *Act:* the agent edits and commits; each edit is recorded as an `Action` (C3: a `PostToolUse` hook; before that, the
  commit itself).
- *Observe:* the commit's files, the version in `pyproject.toml`, the tags.
- *Compare:* the companion files by precision and recall; the version a hit; no tag by the deadline a violation.
- *Correct:* the remembered v0.120.0 closes by itself — no `wiki_remember` needed; the co-change basis is re-scored;
  the missing tag is an event. (The two releases of 2026-10-10 that stayed untagged for a week would have raised it on
  their own.)

**The Ableton playground: "make the bass punchier without clipping".**
- *Precondition:* the last sync is complete; the device at that position is still the compressor (compare before
  write).
- *Predict:* the bass volume becomes 0.82 ± 0.005 within a second; the compressor's threshold reaches its target; the
  gain-staging warning disappears.
- *Law check:* 0.82 is at most unity (0.85) — no clamp needed.
- *Act:* approved; the injector writes and replies with the value it set.
- *Observe and compare:* the read-back and the observer's update — a hit.
- *A minute later* the producer pushes the fader to 0.92. No action explains it: an exogenous change (§3.4.3), and a law
  violation — above unity and above the cap the producer decided. One event; the agent offers a correction and does
  not act.

#### 3.4.9 How the loop grows through the stages

- **C1 — half a loop:** observe and correct. Observations close contradicted beliefs; git attributes changes to commits;
  the null prediction labels everything else exogenous.
- **C2 — standing predictions:** laws are checked on every observation; violations, drift and exogenous changes that
  break a law become events.
- **C3 — the full loop on the repository:** the action log, explicit predictions with deadlines, resolution, misses as
  events, skill per basis.
- **The playground — the fast loop:** precondition, act, read back, verify on a system that answers in seconds.
- **C4 — reading the loop backwards:** diagnosis walks the recorded turns through the ladder of §3.4.5.

The loop needs two things the rest of the design doesn't: an **`Action` record** (§5.2) and a way to see actions — a
`PostToolUse` hook for the coding agent's edits and commands, git for commits, a twin's confirmation for its writes.
How well the loop works is measured by the lifetime of a stale belief, the closure rate, skill over the baseline and
the time to detect a change (§13).

## 4. Target architecture

```
             coding agent (Claude Code): asks · plans · acts · records decisions
                ▲ world slice (hook)        ▲ world tools (MCP)          │ act (dry run, approved)
  ┌─────────────┴───────────────────────────┴────────────────────────────▼──────────────┐
  │  BELIEFS      decisions · reasons · plans · conventions  (Path B)          ← people │
  │  LAWS         rules from documents, decisions and history                  ← data   │
  │  STATE        observed variables, bi-temporal; events, not samples        ← sensors │
  │  PREDICTIONS  expected effects with a deadline → resolved → events           ← time │
  │  ENTITIES     files · services · devices · variables  (the document tier)           │
  └──────▲──────────────────▲───────────────────▲─────────────────────▲─────────────────┘
      git · tests      files · code       Ollama · HTTP        OPC UA (via a twin)
      └──────── the repository and its runtime ────────┘        the LCU 9000
```

| Component | Does | Runs in | Builds on |
|---|---|---|---|
| **Sensors** | read one value each, with no side effects | the inject hook (fast ones), the capture worker, `sleep`, `handoff prepare`, `owiki world observe` (scheduled between sessions) | `handoff.git_state` / `ollama_state` / `capture_workers` / `wiki_freshness` |
| **Bindings** | map a sensor's value to a fact and to the attributes it governs | manifest `[[world.sensors]]`, defaults for code sources | B9's `store.attr_key` |
| **Observation merge** | store observations, close contradicted beliefs, raise events | the two-phase writer and the journal fold | `temporal.plan_merge`, `journal`, ADR-38 |
| **Read-time overlay** | replace a stale recalled fact with the fresh observation | `context_for` (hook, `context`, `wiki_memory`) | B6 assembly |
| **Laws** | hold rules with their check; evaluate them on new observations | the fold, `sleep`, `owiki world laws` | the wiki, entity extraction, co-change |
| **Action log** | record what the agent, a person or a twin did, with its precondition and confirmation (§3.4.1) | a `PostToolUse` hook (the agent's edits and commands), git (commits), a twin's write replies | the Claude Code hooks, `journal` |
| **Predictions** | log expected effects and compare them with what happened (§3.4.4) | capture, handoff, `sleep`; worktree test runs | `graph/cochange.py`, git |
| **Events** | discrepancies, violations, misses, drift, exogenous and contested changes, unresolved predictions | the world slice, the Pflege panel, the handoff brief | the Pflege panel (M7) |
| **World slice** | assemble the context around what a prompt touches | the inject hook, `context`, `wiki_memory` | `assemble_context` budgets |
| **World tools** | the MCP contract (§10.1) | `owiki mcp` | `mcp_server.build_server` |
| **Twins** | other world models behind the same contract | separate MCP servers of their own, running, reached by URL on localhost (ADR-54) — e.g. Cognitive Substrate's | — |

## 5. Proposed data model

### 5.1 Observed state is an Assertion

Observations reuse the reified `Assertion` (ADR-15) and its bi-temporal merge, so recall, the timeline, the Gedächtnis
tab, export and the B0 snapshots handle them without new readers:

| Field | For an observation |
|---|---|
| `subject` · `predicate` · `object` | from the binding's template, e.g. `OpenWiki` · `current release is` · `v0.120.0` |
| `source` | `"observed"` — a fourth value next to `user` / `assistant` / `material` (`memory.SOURCES`) |
| `attr` | the binding's attribute key (`store.attr_key`); no B9 resolution needed |
| `valid_from` | when the value began, if the sensor knows (a commit date, `git blame` on the line), else the first observation |
| `valid_to` | set when a different value is observed |
| `last_seen` | the last observation of this value |
| `confidence` | 1.0; repeated observations don't inflate it |
| `cardinality` | `one` — a sensor reports one value |
| `session_id` | `sensor:<id>`: one `Session` per sensor, its provenance |

### 5.2 New tables

Always created, empty until used (ADR-7); added lazily to old graphs, like the memory tables were:

| Table | Fields | Links | Kept across rebuilds |
|---|---|---|---|
| `Variable` | `id` (the attribute key), `label`, `subject`, `unit`, `sensor`, `kind` (state / setting / count / version) | `DESCRIBED_IN → Page` (the manual section, the code file) | yes |
| `Law` | `id`, `text`, `kind` (equality, comparison, implication, convention, co-change, precondition), `check` (a small expression; empty when untestable), `origin` (page, fact or learned), `status` (proposed / accepted / rejected), `last_result`, `last_checked`, `emb` | `CONSTRAINS → Variable`, `FROM_PAGE → Page`, `FROM_FACT → Assertion` | yes — the review status is a person's work |
| `Action` | `id`, `at`, `actor` (agent, user, system, twin), `verb` (edit, command, commit, write, …), `target` (files or a variable), `args`, `precondition` (the identity or value expected before acting), `approved_by`, `dry_run`, `confirmed_at`, `result` (ok / refused / failed) | `TARGETS → Variable` or `TARGETS_PAGE → Page`; `IN_SESSION → Session` | yes |
| `Prediction` | `id`, `made_at`, `made_by` (rule, co-change, agent), `basis`, `target` (files, tests or a variable), `expected`, `tolerance`, `due` (commits or a time), `settle_s`, `resolved_at`, `outcome` (hit / partial / miss / unresolved), `score`, `baseline_score` | `ABOUT → Variable` or `ABOUT_PAGE → Page`; `FOR_ACTION → Action`; `RESOLVED_BY → Assertion` | yes |
| `Event` | `id`, `kind` (discrepancy, violation, miss, drift, exogenous, contested, unresolved), `at`, `detail`, `resolved_at`, `resolution` | `CONCERNS → Variable`, `FROM_LAW → Law`, `FROM_PREDICTION → Prediction`, `FROM_ACTION → Action`, `INVOLVES → Assertion` | yes |

Every Path C table is registered as authoritative, so B0's snapshot / restore keeps it without a hand-kept list
(§2.3), and the portable export (ADR-39) carries it — how COGX holds it is open (§15). `Session` gains a `kind`
(conversation, agent, sensor, twin, import), so the Sitzungen list and session search keep showing conversations.

### 5.3 Raw telemetry stays outside the graph

Sensor samples don't go into Kuzu (or LadybugDB): it is not a time-series store, and the graph would drown. A machine's
raw series go to an append-only file per twin (SQLite or Parquet, decided in C4); the graph holds what a person or an
agent would want to recall — value changes, threshold crossings, alarms, regime changes — as observed facts and events.
Session search already works this way for transcripts: the raw record stays outside and is pulled on demand (ADR-44).

## 6. Sensors and observations

### 6.1 Sensor types

Built-in types only: a manifest configures them but never adds a command.

| Type | Reads | openwiki-dev example | Speed |
|---|---|---|---|
| `git` | branch, HEAD, upstream ahead / behind, latest tag, tags on a commit, uncommitted files | "OpenWiki · latest release tag is · v0.118.0" | fast |
| `file-pattern` | one value from a file under the project's sources, by regular expression | `pyproject.toml` `version = "0.120.0"` → "OpenWiki · current release is · v0.120.0" | fast |
| `py-constant` | a module-level constant, read with `ast` — the module is not imported | `RECENCY_FLOOR` in `openwiki/graph/store.py` → 0.9 | fast |
| `toml-key` | a key of a TOML file | `[memory] context_k` in `openwiki.toml` | fast |
| `count` | the matches of a pattern across files | the tab buttons in `index.html`; `def test_` in `tests/` | fast |
| `ollama` | `/api/tags`, `/api/ps`, `/api/version` | the pulled and the loaded models | fast (local HTTP) |
| `http-json` | one JSON value from a **localhost** endpoint | `serve`'s `/api/health` | fast |
| `process` | a listening port, a live lock file | "the web UI runs on 8137"; a capture worker is running | fast |
| `gpu` | total and free GPU memory (`nvidia-smi` with fixed arguments) | 12 GB | fast |
| `pytest-collect` | the number of collected tests (`python -m pytest --collect-only -q`); opt-in, since it runs the project's test setup | the test count | slow (seconds) |
| `opcua` (C4) | a node's value, read-only (asyncua) | the LCU 9000's coolant flow rate | fast |
| `twin` (C2–C4) | a variable of another world model, through its MCP `state` verb (§10.1) | a track's volume in the Ableton twin (§11.5) | as fast as the twin |

### 6.2 Bindings

A binding names a sensor, the fact it produces and the attributes it governs — the paraphrases B9 grouped, which the
C0 inventory lists:

```toml
[[world.sensors]]
id          = "release"
environment = "openwiki"                                          # the [[environments]] entry it reads (§2.3)
type        = "file-pattern"
path        = "pyproject.toml"
pattern     = '^version = "(?P<value>[^"]+)"'
fact        = { subject = "OpenWiki", predicate = "current release is", object = "v{value}" }
governs     = ["OpenWiki | has version", "project | has version"]   # further attribute groups, as subject | predicate
every       = "1m"                                                # at most once a minute; "0" = slow passes only
```

Code sources get a small default set without configuration (git state, a version file when one is found); C0 decides
which. Bindings can also be *proposed* from memory — facts whose object matches a constant in the code
("RECENCY_FLOOR is 0.9") — but a person accepts them, as with laws (§7.3).

### 6.3 When sensors run

| Where | Which sensors | How it writes |
|---|---|---|
| inject hook (every prompt) | fast ones whose cached value is older than `every`; at most ~200 ms in total | not at all — the overlay (§6.5), plus an `observe` op in the journal |
| capture worker, `sleep`, `handoff prepare` | all, slow ones included | through the two-phase writer (ADR-38) |
| `owiki world observe [--sensor ID]` — on demand, and scheduled every few minutes between sessions (ADR-54) | the ones asked for; scheduled: those whose period is due | as above |
| MCP world tools | the ones asked about | the journal |

### 6.4 Merge rules

An observation enters `temporal.plan_merge` with `source = "observed"` and its binding's attribute:

1. **Same value as the current observation** → re-affirmed: the world cache notes it, and `last_seen` reaches the
   graph when the value changes or at `sleep` — a write on change, not per reading (§2.3).
2. **A new value** → the previous observation closes at the new value's `valid_from`; the new one is current.
3. **Current beliefs in a governed attribute with a different object** close at the observation's `valid_from` —
   kept as history, with a `SUPERSEDES` edge from the observation. A belief recorded *after* the value it contradicts
   began also raises a *discrepancy* event: someone believed something the world had already changed.
4. **A belief that agrees** is re-affirmed and kept; it is still the user's or the agent's statement.
5. **A later belief that contradicts a current observation** never becomes current. It is stored as a zero-length
   interval — the device B9 already uses for ordered changes within one capture — and raises a discrepancy event:
   "the session said v0.119.0; `pyproject.toml` says 0.120.0".

The temporal set (13 / 13) guards these rules: they may not change what B7 decides for attributes without a sensor.

### 6.5 The read-time overlay

Readers can't write (ADR-38), and an observation made by the hook must not wait for a fold. So `context_for` overlays:
a recalled fact in a governed attribute whose fresh observation differs is replaced in the assembled text by the
observation — "OpenWiki current release is v0.120.0 (observed just now: pyproject.toml)" — and the observation is
queued as an `observe` op for the next writer. The context is right at once; the graph catches up within minutes.

### 6.6 Safety

- **No shell.** Built-in types only; file paths confined to the project's sources and repository; HTTP to localhost
  only; OPC UA read-only. Twin code beyond the built-in types — sensors, checks, simulators — enters only through a
  reviewed commit in the environment's repository (§2.2); at run time a manifest can't add a command.
- **Twins by URL, on localhost only.** A twin is reached at `127.0.0.1` with its own token, which lives in an
  environment variable, never in the manifest; any other address is refused (ADR-54).
- **Values are input like any other.** Observed values pass credential redaction and the P0 policy (ADR-30, ADR-37):
  a config file can hold a token, a log line an injected instruction.
- **`pytest-collect` is opt-in.** It runs the project's test setup; on a foreign repository that is code execution.
- **Sensors never write.** A twin's actions are a separate, guarded verb (§10.2).
- **Telemetry can be spoofed.** Observations carry their sensor and a quality flag; a law violation reported by one
  sensor is an event to check, not a command.

## 7. Laws, decisions and drift

### 7.1 Where laws come from

- **Documents.** Manuals, `CLAUDE.md` and the ADRs state rules: ranges, preconditions, procedures, conventions. The
  LCU 9000 manual: "Even if a program requests 5000W, the laser will cap out at this limit"; `CLAUDE.md`: keep
  `--split-level` consistent across `index` and `graph-build`.
- **Decisions in memory.** A decision (by the user, or recorded by the agent) that fixes a variable: "context_k is 16"
  is a law on `Project.context_k` for as long as the decision stands.
- **History.** Co-change for code (ADR-48); for machines, dependencies learned from telemetry (§11.3).

### 7.2 What a law looks like

Every law has its text; a law that can be checked also has a check:

| Kind | Check | Example |
|---|---|---|
| equality | `A == B` | the version in `pyproject.toml` equals `openwiki.__version__` |
| comparison | `A <= B` | output power ≤ Max Output Power Limit |
| implication | `P ⇒ Q` over state | door open ∨ chiller alarm ⇒ emission off |
| convention | a predicate over observations | every released version has a tag — it would have flagged v0.119.0 and v0.120.0 |
| co-change | files A change ⇒ files B change | a change to `cli.py` also changes `CLAUDE.md` |
| precondition | decision D holds while `V == v` | "embed in one phase first" holds while the GPU has 12 GB |

A law without a check stays text; it still informs the world slice. Declarative first (§2.2): a check is a small
expression for a generic checker, and a reviewed test function only where an expression can't say it. A check counts
only after it has failed on a planted violation.

### 7.3 Extraction and review

Candidate laws come from one model call per page whose sentences look like rules, pre-filtered by patterns (*must,
never, only if, above, below, requires, always — muss, nie, nur wenn, mindestens, höchstens*), with the variables they
mention resolved against `Variable` nodes and entities. A candidate becomes checkable only after a person accepts it in
the Pflege panel. Laws are few and consequential and the model's judgment is not trusted (§3.2); a review queue costs
little.

### 7.4 Decisions that remember their preconditions

A decision is recorded with the state it was made in: the observed values of the variables it names, taken
automatically when it is recorded, and optionally explicit preconditions (`wiki_remember` would accept a `depends_on`
list). When a precondition changes, a *drift* event says the reason may no longer hold. Two decisions of this project:

- "Embed in one phase first, because the 30B model and bge-m3 evict each other on the 12 GB GPU" ↔ GPU memory (`gpu`),
  the loaded models (`ollama`).
- "Python 3.13, because Kuzu has no Windows wheel for 3.14" ↔ that wheel's availability — a sensor that would need
  PyPI, i.e. outbound HTTP: opt-in, or checked by hand.

The other direction is the same check: the code leaving a decision — `RECENCY_FLOOR` changed without a new decision —
is either a regression or a decision nobody recorded.

### 7.5 Documents drift too

Documents state state: arc42 §10 names the test count, `CLAUDE.md` the defaults, the README the commands. Sensors make
that checkable ("arc42 §10 says 756 tests; the suite collects N"), so a law can flag the document as the stale side
rather than the code.

## 8. Predictions and surprise

### 8.1 What gets predicted

Start where the digital world can check itself exactly:

- **Companion files.** When the working tree has changes (seen at capture, at handoff or on request), predict the
  other files the change will touch, from co-change (ADR-48), the co-change laws and the version pair. Resolved by the
  next commits.
- **Tests at risk.** The test files that import the changed modules, optionally refined by a coverage map from one
  `pytest --cov` run. Resolved by the next test run — in the session, in CI, or in a git worktree that runs only the
  tests at risk: the repository twin's exact simulator.
- **The agent's own predictions (optional).** "This will make the failing test pass", recorded through a world tool
  and resolved by the run: a calibration signal for the agent rather than for the memory.

### 8.2 Resolution and calibration

Each prediction has a deadline (a number of commits, or a time) and a settle time, and is compared as §3.4.4 describes:
hit, partial, miss — or unresolved when nothing was observed in time. Scores are kept per basis next to the basis's
baseline — "co-change, top 10: 0.51 of the companions" against the files' base rates of change — so the model's own
track record is visible and a basis without skill can be dropped.

### 8.3 Events

Seven kinds, all first-class and all resolvable by a person:

| Kind | Raised when |
|---|---|
| discrepancy | a belief contradicts a current observation (§6.4, rules 3 and 5) |
| violation | an accepted law's check fails on new observations |
| miss | a prediction resolves as a miss |
| drift | a decision's precondition changes, or the code leaves a decision (§7.4) |
| exogenous | a change no action explains breaks a law or a decision (§3.4.3); other outside changes are only attributed |
| contested | a variable flips between an action and an outside change (§3.4.7) |
| unresolved | a sensor leaves predictions unresolved repeatedly (§3.4.4) |

Events reach the world slice when a prompt touches their variable, the Pflege panel, and the handoff brief's "what
changed since". This is the deterministic form of Nemori's predict–calibrate (MSR §10): what the
model failed to predict is what deserves attention — found without a model call per session.

### 8.4 What is not predicted

No model-based "imagine what happens" for anything that can be observed, run or simulated; no predict–calibrate at
capture with extra model calls per window (the review ruled out pipelines with several 30B calls per segment); no
physics from the language model.

## 9. The world slice

Today the hook injects the identity, up to 16 facts chosen by similarity and up to 4 themes within 3,000 characters
(ADR-35), each once per stretch (ADR-43). With Path C:

1. **Find what the prompt touches:** file paths and identifiers in the prompt, the working tree's changed files,
   variable labels matched by BM25, the subjects of the recalled facts.
2. **For each touched variable:** its current observed value and age, the accepted laws on it with their last result,
   open events, open predictions.
3. **The decisions about it:** Path B recall, restricted to facts linked to those variables or subjects.
4. **The rest as today:** facts and themes in the remaining budget.

The slice takes at most a third of the budget — a starting point that C2 measures — and is skipped when the prompt
touches nothing the model knows; the context is then exactly today's. Chore prompts still get nothing (v0.101).

## 10. Twins, the MCP contract and Cognitive Substrate

### 10.1 One contract for every world model

Each environment is a twin behind the same small set of MCP verbs, so the agent — and OpenWiki — can use any of them:

| Verb | Returns |
|---|---|
| `entities(query)` | entities and variables, with their sensors and descriptions |
| `state(variable, as_of)` | value, observed at, sensor, quality, validity interval |
| `history(variable, since, until)` | value changes and events |
| `laws(entity or variable)` | rules with their check and last result |
| `predict(change)` | expected effects with basis and confidence, logged as a `Prediction` |
| `explain(event)` | candidate causes from laws and dependencies, with evidence |
| `act(action, dry_run=true)` | only for twins that declare actions (§10.2) |

OpenWiki would implement the repository twin inside `owiki mcp` (tool names open, §15). A twin is a world model as an
entity in its own right: it observes its environment, keeps its state, knows its rules and answers questions about them
— the cyber-physical entity this direction started from. OpenWiki is the client of every other twin, connecting to it as
a running service by URL (§2.3, ADR-54): agents read twins through OpenWiki's world tools, and an action goes through
OpenWiki's `act`, which checks it, records it and forwards it to the twin's own guarded `act`.

### 10.2 Acting

Twins start read-only. A twin that declares actions takes one only:

- as a dry run first;
- with the accepted laws checked as interlocks — a SET_POWER above the limit is refused before it reaches the machine;
- after a person's approval — ADR-46's staging, applied to actions;
- logged as an event together with the observation that followed.

Physical actuation stays outside OpenWiki entirely; it belongs to the twin that owns the machine.

### 10.3 Federation with Cognitive Substrate

Cognitive Substrate already holds most of a physical twin's design: a CANONICAL tier for sensor ground truth with
server-enforced write authority; `(:FunctionalVariable)` anchors that tie a GUI element, an OPC UA node and a PLC
register to one physical variable (its Phase 11, the Cyber-Physical Digital Twin — designed, not built); guarded
actions that read live state before acting (D15); and self-healing skills (D17, its L3 *Evolver*). Its status on
2026-06-04: the perceive → consolidate → retrieve → plan → act → verify loop works on the LCU 9000 mock web UI;
OPC UA and PLC perception are designed, not built.

| | Cognitive Substrate | OpenWiki |
|---|---|---|
| Owns | the machine's twin: OPC UA / PLC perception, CANONICAL state, actions, procedural skills | documents (the manual as a wiki, the laws extracted from it), the operational memory (sessions, decisions), the repository twin |
| Engine | Neo4j 5 | Kuzu, moving to LadybugDB (R10) |
| Models | Gemini embeddings first (D14), a local fallback | local by default, cloud where really needed (ADR-52) |
| Meets the other through | the MCP contract (§10.1), shared variable ids (e.g. `lcu9000.coolant.flow_rate`), COGX for bulk exchange | the same |

**Why federate rather than merge.** The engines differ, so does the stance on cloud models, and both projects are
mid-flight. A shared contract and shared ids let each keep its stack; Claude Code connects to both servers. A merge can
follow if one engine wins.

### 10.4 What this re-opens

PB §11 took Cognitive Substrate's ideas, "not the weight", and listed sensor / OPC UA / PLC fusion among
what "deliberately does *not* transfer". Path C keeps that line for machines — fusion stays in the twin that owns them —
and moves it for the repository: built-in, local, read-only sensors are light enough for OpenWiki itself. It also
refines ADR-33: the writer that makes a change is no longer the only one who can record it; the world is a second
writer.

## 11. The physical case — the LCU 9000 and the Ableton playground

### 11.1 Variables and laws from the manual

The LCU 9000 user manual (Cognitive Substrate, `docs/source-manuals/lcu_9000_user_manual.md`) names the variables a
twin needs — real-time output power, Max Output Power Limit, Max Duty Cycle, back-reflection level, coolant temperature
and flow rate, power-supply state, interlock status, emission on / off, hardware interlock checking — and the laws that
bind them:

- "Even if a program requests 5000W, the laser will cap out at this limit" — output ≤ limit.
- "If a physical door is opened or a chiller alarm trips, this indicator turns red, and emission is mechanically
  severed" — door open ∨ chiller alarm ⇒ interlock red ∧ emission off.
- Back-reflection: "High levels trigger automatic safety shutdowns" — back reflection above a threshold ⇒ shutdown.
- "Never bypass the chiller coolant flow limits"; "ensure the Hardware Interlock Checking is enabled when operating
  outside a fully enclosed CNC laser cell".

OpenWiki builds the manual's wiki, extracts these as candidate laws with their variables (§7.3), and offers them as
`Variable` and `Law` nodes whose ids the twin's FunctionalVariables share.

### 11.2 A simulator with known causes

There is no machine to test against, so C4 starts with a simulated one: an OPC UA server (asyncua) exposing the
variables above, driven by a small model — power and duty cycle heat the module, coolant flow and the chiller remove
the heat (first order), the workpiece's reflectivity sets the back reflection — with injectable faults: a degrading
pump, a chiller alarm, an opened door, a drifting sensor, a reflective workpiece. Because the simulator's causal graph
is known, it is the ground truth for everything learned from it.

### 11.3 Learning dynamics where the data allows it

The transfer-entropy report was rejected for a personal memory because its series were far too short: the facts about a
subject were said on a median of one day (MSR §13). Telemetry is the setting such methods were
made for — long, dense, regularly sampled series. C4 tests them fairly: lagged correlation, Granger causality, and
transfer entropy with PCMCI and surrogate significance tests, against the simulator's true causal graph and the
manual's laws. If they don't beat lagged correlation there, they don't belong anywhere in OpenWiki.

### 11.4 The diagnosis benchmark

Inject N faults; ask "why did the laser shut down at 14:02?" or "why is the output below the setpoint?". Three
conditions: (a) the manual's wiki only (RAG); (b) plus live state and history from the twin; (c) plus laws and learned
dependencies (`explain`). Scored against the injected fault — an exact label, no judge — together with the false-alarm
rate of the law checks on fault-free runs.

### 11.5 A working playground — the Ableton twin

The LCU 9000 exists only as a manual and a mock web UI; its twin would start as a simulator. A working two-way twin
already runs on this machine: the **Ableton Expert System** (`G:\Antigravity\ableton-expert-system`,
`github.com/Neuroant/Ableton-Live-Expert-System`), a sibling project that models Ableton Live. A Max for Live extractor
streams Live's state over OSC into an embedded Kùzu graph — song, tracks, devices, parameters, clips, notes — bound to
a static model of 16 device classes, 57 parameter definitions with physical units and 248 manual sections; an injector
executes a fixed set of 15 commands; a Gemini agent and a React UI work through two MCP servers. It uses OpenWiki's
graph engine (Kùzu 0.11.3) and answers in seconds, with real sensors and real actions. That makes it Path C's test
playground: each part can be tried on a system that answers back.

| Path C part | On the Ableton twin |
|---|---|
| Sensors (§6) | its observers and snapshots, read through the twin contract — the `twin` sensor type calls the twin's `state` verb (§10.1) |
| State with history (§5.1) | the twin keeps current values only; observed facts with valid time add "what was the bass level when we decided to cap it?" |
| Laws (§7) | the Production Constitution (`0.85` = 0 dBFS unity), the mix-diagnostics rules, and the Ableton 12 manual — already ingested there — as their source; enforced as interlocks in the injector |
| Predictions (§8) | every write predicts its effect ("track 1's volume becomes 0.82") and is resolved by reading the value back; a diagnostics fix predicts that its warning disappears |
| Events (§8.3) | discrepancy (the twin vs. Live after a read-back), violation (a fader above unity), drift (a decision or a sound recipe that no longer matches the set) |
| World slice (§9) | the tracks and devices a prompt names, their values in physical units, the rules on them, recent events, the producer's decisions and project notes |
| Acting (§10.2) | the injector's whitelist, plus clamps, compare-before-write and approval |
| The MCP contract (§10.1) | a thin adapter over its knowledge and action servers: `entities`, `state`, `history`, `laws`, `predict`, `explain`, `act` |

**Before it can serve.** The review of 2026-10-18 (`docs/Digital_Twin_Review.md` in that repository) lists what has to
change first: safety limits in code rather than in the prompt (its R1), stable object ids so that an observation and an
action refer to the same thing (R2), a sync that knows whether it is complete (R4), and a read-back after every write
(R6). Path C ranks an observation above a belief (§3.3), so a partial or misaddressed snapshot must never close
anything: only a sync reported complete counts as an observation.

**Safe to play with.** A dedicated test set, a limiter on the master, low monitor levels, and observation only until
the clamps are in place. The twin's own agent uses Gemini; OpenWiki's side is local by default (ADR-52) and meets the twin only
through MCP.

## 12. Staged delivery plan (C0–C4)

Each stage is shippable and measured on its own, and each can end Path C. The work packages, gates, releases and
status of each stage are tracked in [`path-c-roadmap.md`](path-c-roadmap.md); results are recorded here, as a "Landed"
note per stage and in a measurements section (§18) created with C0's results.

### C0 — Inventory (no code)
- **Goal:** know how much of the memory, and of its known staleness, is observable before building anything.
- **Build:** nothing. Classify (a) the stale facts already labeled — 14 in PB §13.4, 40 in PB §13.26 — and (b) 200 random
  current facts of openwiki-dev, each by how it could be checked: a built-in sensor (which type, which binding), a
  law, a person, a deadline, or not at all; list the attribute groups each binding would govern. Count (c) the injected
  facts in those attributes on the replayed prompts of PB §13.32, and how many were stale at the prompt's time.
- **Builds on:** the labels of PB §13.4 and PB §13.26; the replay of PB §13.32; `memory.volatile_kind`.
- **Hard part:** honest labels — "observable" means a built-in sensor type could read it, not that a clever script
  could.
- **Exit (go / no-go):** go if about 15 bindings would have caught at least a third of the labeled stale facts;
  otherwise stop and record the negative result, like the measured-and-not-adopted items of Path B.

### C1 — Observe, don't remember
- **Goal:** observable state in memory is right by construction.
- **Build:** the sensor types of §6.1 except `opcua`; bindings, with defaults for code sources; `source = "observed"`;
  the merge rules (§6.4); the read-time overlay (§6.5); the `observe` journal op; `owiki world status` and
  `owiki world observe` (subcommands, ADR-13; scheduled between sessions, ADR-54); an "observed" badge in the
  Gedächtnis tab; governed attributes leave the review list; each observed change attributed to the commit that made
  it, else exogenous — half of the loop (§3.4.3, §3.4.9).
- **Builds on:** B7 and B9, the journal and the two-phase writer (ADR-38), `handoff`'s probes, `assemble_context`.
- **Hard part:** the governed attribute groups (B9's paraphrases) and the authority rule, without changing what B7
  decides elsewhere.
- **Exit:** stale facts among the injected facts in governed attributes fall to near zero on the replayed prompts
  (against C0's baseline); a stale belief in a governed attribute lives at most one sensor period (§13); the review list
  shrinks by the governed share; the inject hook's p95 latency grows by at most 300 ms; the guards hold (§13).

### C2 — Laws, drift and the world slice
- **Goal:** rules and decisions become checkable, and the context is built around what a prompt touches.
- **Build:** `Variable`, `Law`, `Event`; law extraction with review; checks at fold time — laws as standing predictions
  (§3.4.4); decisions recorded with their preconditions; drift, exogenous and contested events (§3.4.3, §3.4.7); the
  world slice (§9); events in the Pflege panel and the handoff brief. On the Ableton
  playground (§11.5): its Production Constitution as the first interlocks, the Ableton manual as the first law source
  outside this repository.
- **Builds on:** entity extraction, the Pflege panel (M7), the handoff.
- **Hard part:** false alarms — a law that fires wrongly teaches people to ignore events.
- **Exit:** of drifts planted in a scratch copy (a changed constant, a desynchronized version pair, a changed GPU, an
  untagged release), at least 9 of 10 detected; at most one false alarm a week in real use; on replayed prompts that
  touch a governed variable, the world slice judged more helpful than today's context at equal size (paired,
  significant); on the playground, every planted violation (a fader above unity, a value outside its range) raises an
  event and the interlock refuses the write.

### C3 — Predict and verify changes
- **Goal:** the model is judged by what it predicts — the full loop on the repository (§3.4).
- **Build:** `Action` and the action log (a `PostToolUse` hook for the agent's edits and commands; git for commits);
  `Prediction`, linked to its action; companion-file and test-at-risk predictions; comparison with tolerances, settle
  times and deadlines (§3.4.4); resolution by commits and test runs (a worktree for the tests at risk); misses and
  unresolved predictions as events; scores per basis against their baselines; `explain` walking the ladder of §3.4.5.
  On the Ableton playground: every write predicts its effect and is resolved by reading the value back.
- **Builds on:** `graph/cochange.py`, git, the test suite, the Claude Code hooks; the playground's read-back (its
  review's R6).
- **Hard part:** test runs cost time — only the tests at risk, only when idle or on request; and attribution — an
  observed change must find its action through identity and time (§3.4.3).
- **Exit:** online over the history (the PB §13.29 setup), companion files predicted from co-change plus laws beat
  co-change alone (0.51 top-10 recall of rarely-changed files); test-at-risk precision and recall reported; closure rate
  and skill over the baseline reported per basis (§13); replayed, injected misses judged helpful more often than the
  facts they displace; on the playground, every write's prediction resolves within a second and every mismatch becomes
  an event.

### C4 — The physical twin
- **Goal:** the same model for a working twin, then for a machine, through federation.
- **Build:** the twin contract on the Ableton twin first (§11.5), once its review's R1, R2, R4 and R6 are in; then the
  LCU 9000 simulator (§11.2) for faults with known causes; the LCU 9000 twin's MCP server — in Cognitive Substrate, the
  LCU 9000's repository (§2.2); the manual wikis' variables and laws with shared ids; the telemetry store; learned dependencies
  (§11.3); the diagnosis benchmark (§11.4) on both.
- **Builds on:** the Ableton Expert System's MCP servers; Cognitive Substrate's Phase 11 design and its LCU 9000 mock;
  C1–C3.
- **Hard part:** faults the twins can actually see — on the playground only after its observers cover device parameters
  (its review's R5); a simulator rich enough to make diagnosis non-trivial and simple enough to trust as ground truth.
- **Exit:** diagnosis accuracy (c) > (b) > (a), paired and significant, on faults planted in an Ableton test set (a fader
  above unity, a muted track, a changed device setting) and on the simulator's; learned dependencies recover the
  simulator's causal edges more precisely than lagged correlation; no write outside the clamps and approval — the LCU
  9000 twin stays read-only.

### 12.1 Recommended first slice

C0, then a minimal C1: five bindings — the release (`pyproject.toml`), `openwiki.__version__`, git (latest tag,
upstream sync), the number of tests (`pytest-collect`, opt-in) and Ollama's models — one law ("every released version
has a tag"), the overlay, and the replay measurement. A few days of work, measured on data that exists, aimed at the
memory's known weak spot.

## 13. Evaluation strategy

**Principles**, as in Path B: measure each step before it becomes a default (ADR-9); paired comparisons on the same
data; prompts replayed at their own time (PB §13.32); scratch copies of the dev graph, never the live one; blind hand
labels wherever a judge would decide; the local judge treated as generous (PB §13.11).

| Metric | Stage | How |
|---|---|---|
| stale facts among injected facts in governed attributes | C0 (baseline), C1 | replayed prompts, hand labels |
| **lifetime of a stale belief** — from the world's change to the belief's closing | C0 (baseline), C1 | observed changes against closings, on the replayed history |
| **testable share** — the facts and laws a twin can test: the knowledge model's test coverage (§2.2) | C0 (baseline), C1–C3 | the inventory, then the bindings and checks in place |
| check health — every check fails on its planted violation | C2, C3 | fixtures; mutation tests in a scratch copy |
| review-list size and its stale share | C1 | `analyze memory --review`, blind labels |
| inject-hook latency, p95 | C1 | the metrics collector |
| drift detection: detected of planted, false alarms per week | C2 | planted drifts on a scratch copy; real use |
| time to detect an exogenous change or a violation | C2 | planted changes on a scratch copy and on the playground |
| world slice against today's context | C2 | blind judge at equal characters, paired |
| usefulness of injected events and misses | C2, C3 | blind judge on replayed prompts, against the facts they displace |
| companion-file recall@10; test-at-risk precision and recall | C3 | online over the history |
| **closure rate** — actions with a prediction and an outcome; coverage of actions | C3 | the action log |
| **skill over the baseline**, per prediction basis | C3 | resolved predictions against persistence and base rates |
| root-cause accuracy, (a) / (b) / (c) | C4 | planted faults on the playground, simulator faults; exact labels |
| causal-edge recovery against the simulator's truth | C4 | precision and recall |

The lifetime of a stale belief is the cleanest single number for whether Path C works: today it is unbounded — a stale
fact lives until someone says otherwise —, with C1 it should not exceed one sensor period.

**Guards** (must not move): the temporal set at 13 / 13; the poisoning set at 0 / 5 leaks with 8 / 8 legitimate facts
kept; the cross-session and cue-trigger sets; LoCoMo overall J — LoCoMo has no sensors, so any change there is a bug.

## 14. Risks & guardrails

- **Hype.** "World model" invites learned simulators and grand claims. Path C's model is symbolic, statistical and
  grounded in real systems; every stage has a measured exit and can end the path.
- **Scope.** OpenWiki is a learning project for agentic wikis. The repository twin fits it; machines live in the twin
  that owns them (§10.3).
- **The model as simulator or judge.** Never for state that can be observed, run or simulated (§3.2).
- **Sensors as an attack surface.** No shell, confined paths, localhost only, redaction and the P0 policy on values
  (§6.6). Telemetry can be spoofed: observations carry source and quality, and events are checked, not obeyed.
- **Actuation.** Read-only by default; dry run, interlocks, approval and an audit trail for any action (§10.2);
  physical actuation never from OpenWiki.
- **False alarms.** Laws only after review, events resolvable by a person, the false-alarm rate an exit criterion (C2).
- **Loops that run away or fight.** No automatic remediation, contested variables left alone, silence never taken as
  confirmation, skill scored against a baseline (§3.4.6, §3.4.7).
- **Cloud dependence** (ADR-52). Private memory only by explicit opt-in and after redaction; the cost recorded per
  task; a local fallback for every routed task; the model and its version recorded with every result, since cloud
  models change under their names.
- **Twin code written by a model.** Written by the coding agent, accepted only through reviewed commits, every check
  proven on a planted violation; no change to a twin's code at run time (§2.2).
- **Over-observation.** Sensors on things nobody asks about cost latency and add noise; bindings come from the C0
  inventory, not from what is easy to read.
- **Volume.** No samples in the graph (§5.3).
- **Engine.** Kuzu is archived (R10). The new tables follow the LadybugDB move's constraints
  ([`ladybugdb-move.md`](ladybugdb-move.md)), and the portable export must carry them.

## 15. Decisions

**Decided on 2026-10-18**

- **The main architectural plan** — the world model as a dynamical system with two boundaries (§2.1, ADR-50).
- **Where Path C lives** — the knowledge model and the generic twin machinery in OpenWiki; each environment's twin in
  that environment's repository, federated through the twin contract (§2.2, §10.3). So the LCU 9000 twin's MCP server
  lives in Cognitive Substrate, the LCU 9000's repository.
- **Twin code is reviewed** — every change goes through a reviewed commit; read-only sensors and checks may later be
  relaxed to land without review once their fixtures have proven them; actuators never (§2.2).
- **Declarative first** — laws are data with generic checkers; a reviewed test function only where an expression can't
  say it (§2.2, §7.2).
- **Language models at design time, code at run time** — the coding agent programs the twins in sessions, the twin's
  loop makes no language-model call, the local model builds and maintains the knowledge model (§2.2, ADR-51).
- **Hybrid language models** (2026-10-24) — Paths B and C use a local model wherever it is enough and a cloud model
  where it is really needed, under the conditions of §2.2; Path A stays local (ADR-52, ADR-2).
- **Deployment** (2026-10-25) — between sessions the loop runs as a scheduled `owiki world observe`, not inside
  `serve`; twins are running services OpenWiki reaches by URL on localhost with a token, started by their own systems;
  the world tier runs natively, Docker stays for document-only setups and CI (§2.3, ADR-54).

**Open — for the user**

1. **The first environment:** the repository (measurable now), the Ableton twin (runs now; needs R1, R2, R4 and R6 of
   its review), or the LCU 9000 (needs the simulator)? *Recommended: the repository for C0–C1, where the stale-fact
   baseline is; the Ableton twin as the test playground from C2 on — laws, actions, predictions and diagnosis on a
   system that answers back (§11.5).*
2. **Acting:** may twins act, or stay read-only? *Recommended: read-only through C4.*
3. **Telemetry:** real hardware or an OPC UA server, or a simulator? *The plan starts with the Ableton twin's live
   data and adds a simulator for faults with known causes.*
4. **The action log:** record the coding agent's edits and commands as `Action`s through a `PostToolUse` hook — local,
   and needed to tell the agent's changes from everyone else's (§3.4.3)? *Recommended: yes, from C3; git covers commits
   before that.*
5. **What the loop may correct by itself** (§3.4.6): facts automatically, laws and decisions only by a person, never
   automatic remediation? *Recommended: as in §3.4.6.*
6. **Which cloud models and providers** for the hybrid approach — per task, behind the `ChatModel` protocol, keys from
   the environment? *Recommended: choose at the first use — a second benchmark judge (track L) — and add others only
   with their own measured need.*
7. **A small local model** for features that run on every prompt (constraint probes)? *Recommended: measure before
   adding one — the coexistence check proved sensitive to wording.*

**Open — technical**

8. Agent predictions: stated explicitly through a tool (`wiki_predict`), or system-made only — persistence, co-change,
   laws? *Recommended: system-made first; the tool once C3's scores show what the agent's own predictions would add.*
9. Where the slow loop runs: inside `sleep`, or as its own `owiki world review`? *Recommended: inside `sleep`, like
   forgetting and consolidation, with `owiki world review` for runs on demand.*
10. Names of the world tools (`wiki_state` and siblings, or a `world_*` family).
11. Bindings only in the manifest, or also proposed from memory (§6.2)?
12. The telemetry store, SQLite or Parquet (C4).
13. Whether a contradicting late belief is stored at all (§6.4, rule 5) or only reported.
14. How laws, predictions, actions and events travel in COGX (e.g. in `--full` metadata).
15. A *Welt* (world) tab in the web UI, or panels inside Gedächtnis.

## 16. Relationship to the current code

| Path C needs | Reuses |
|---|---|
| sensors | `handoff.git_state`, `git_commits`, `ollama_state`, `capture_workers`, `wiki_freshness`; the file set of `code_parser` |
| the observation merge | `temporal.plan_merge` (plus an authority rule), `store.attr_key`, `GraphStore.remember` and `retire` |
| deferred writes | `journal` (plus an `observe` op), the fold worker, `cli._write_memory` |
| the overlay and the world slice | `GraphStore.context_for`, `memory.assemble_context` (budgets, `exclude`, `report`) |
| candidate bindings | `memory.volatile_kind`, `analyze memory --review` |
| laws from documents | the wiki, `graph/entities.py`, the section anchors of `graph/references.py` |
| dynamics | `graph/cochange.py` |
| the action log | the Claude Code hook installer (`claude_code_template.hooks_config`, adding `PostToolUse`), `cli._cmd_hook`, the journal |
| events and their review | the Pflege panel (`/api/memory/maintenance`), `handoff.render_brief` |
| world tools | `mcp_server.build_server` |
| environments in the manifest | `project.py` (`Project.environments`, next to `source_paths()`), `[[sources]]` paths |
| the handoff as a world slice | `handoff.environment` — reading the world model's observed state, events and open predictions instead of its own probes |
| the generic twin machinery — built-in sensor types, generic law checkers, the loop runner, the twin contract | new in OpenWiki, on `handoff`'s probes, the journal and `mcp_server`; each environment's own twin code stays in its repository (§2.2) |
| analysis | `analysis/memory.py`, extended by a model-versus-reality section: discrepancies, violations, calibration |

## 17. Prior art

- **Digital twins.** Kritzinger et al., "Digital Twin in manufacturing: A categorical literature review and
  classification" (IFAC-PapersOnLine, 2018) — model, shadow, twin. Industrial information models — the OPC UA companion
  specifications, the Asset Administration Shell of Industrie 4.0 — are the natural vocabulary for a machine's
  variables once C4 meets a real one; FMI / FMU co-simulation for physics models beyond a toy.
- **Learned world models.** Ha & Schmidhuber, "World Models" (2018); DreamerV3 (Hafner et al., 2023); LeCun's JEPA
  proposal (2022); Genie (2024) — not the core of Path C, and no longer ruled out by compute: a candidate prediction
  basis wherever an environment yields dense data (§2.1, C4).
- **Control loops (§3.4).** MAPE-K — monitor, analyze, plan, execute over shared knowledge (Kephart & Chess, "The
  Vision of Autonomic Computing", 2003) — is the closest engineering pattern; OODA (Boyd) and PDCA (Shewhart, Deming)
  are its decision and quality cousins. Forecast verification gives the yardstick: proper scores such as the Brier
  score (1950) and skill against climatology or persistence. Control theory gives the failure modes: nested loops at
  different speeds, settling time, two controllers fighting over one variable. Predictive processing and active
  inference are inspiration only: Path C's loop reduces surprise by correcting the model or by asking a person, never
  by acting on its own.
- **The agent-memory review.** Hindsight keeps world facts apart from the agent's experiences (MSR §7); AriGraph builds the
  world model of a text game — the current state as a graph, its history as episodes (MSR §9); Nemori stores what memory
  failed to predict (MSR §10), of which §8.3's events are a deterministic form; memory-champ rechecks volatile facts on a
  schedule (MSR §11), which sensors do for real; the transfer-entropy report (MSR §13), rejected for a sparse memory and
  retested here on dense telemetry (§11.3); OpenCog Hyperon (MSR §17) — inference needs a curated vocabulary, which
  reviewed laws provide.
- **Programs as models (§2.2).** Voyager keeps a library of code skills verified by running them in Minecraft;
  Cognitive Substrate's self-healing skills (its D17) are proposed by a model, validated by Playwright and demoted when
  they fail; property-based testing states laws as properties a test tries to violate; continuous integration runs the
  tests on every change — the twin does the same for knowledge.
- **Ableton Expert System.** A working two-way twin: classes and instances bound with physical units (a normalized
  0.72 shown as 1,200 Hz), an injector restricted to a fixed set of commands, sound recipes as procedural memory
  captured from state — and, in its review of 2026-10-18 (`docs/Digital_Twin_Review.md` there), the gaps a twin shows
  without Path C's parts: no history, positional identity, safety in the prompt, no read-back (§11.5).
- **Cognitive Substrate.** Tier write-authority, FunctionalVariable anchors, guarded and self-healing actions, and the
  capability ladder L1 predictor → L2 simulator → L3 evolver → L4 cross-domain inference → L5 test synthesis (its
  `docs/ROADMAP.md`). Path C's repository twin reaches L1–L2 for code — predicting a change's effects, then running it
  in a worktree; C4 is its L4 with OpenWiki's documents and memory attached.
