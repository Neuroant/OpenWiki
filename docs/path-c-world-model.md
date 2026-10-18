# Path C — World Models (design proposal)

> **Status: proposal (2026-10-18, OpenWiki v0.120.0). Nothing is built.** The next step is C0, an inventory that needs
> no code and decides whether the rest is worth building. Path C builds on Path B ([`path-b-memory.md`](path-b-memory.md))
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
contradictions, violations and misses become first-class *events*. The agent's context becomes a slice of that
model: what a prompt touches, its current state, the rules that bind it, what surprised us lately, and what was
decided about it.

## Contents

1. [Motivation & main idea](#1-motivation--main-idea)
2. [What "world model" means here](#2-what-world-model-means-here)
3. [Conceptual model](#3-conceptual-model)
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
15. [Open decisions](#15-open-decisions)
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
  redaction (ADR-30, ADR-37); local-first operation (ADR-2); measure before adopting (ADR-9); readers that hold the
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
| **Learned world models** (Ha & Schmidhuber 2018, DreamerV3, JEPA, Genie) | neural dynamics learned from large streams of observations, used to imagine and plan | out of scope: they need data and GPUs we don't have, and a coding agent's world is not a video stream |
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

| Step | In OpenWiki terms | Today |
|---|---|---|
| Perceive | sensors (§6) and capture | capture only |
| Model | the graph: entities, state, laws, beliefs | entities, beliefs |
| Predict | predictions for changes (§8) | — |
| Act | the coding agent; twin actions behind guards (§10.2) | the agent, unguarded by the memory |
| Observe | sensors again, commits, test runs | — |
| Correct | the merge closes stale beliefs (§6.4); `sleep` checks laws and resolves predictions | `sleep` forgets and summarizes |

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
| **Sensors** | read one value each, with no side effects | the inject hook (fast ones), the capture worker, `sleep`, `handoff prepare`, `owiki world observe` | `handoff.git_state` / `ollama_state` / `capture_workers` / `wiki_freshness` |
| **Bindings** | map a sensor's value to a fact and to the attributes it governs | manifest `[[world.sensors]]`, defaults for code sources | B9's `store.attr_key` |
| **Observation merge** | store observations, close contradicted beliefs, raise events | the two-phase writer and the journal fold | `temporal.plan_merge`, `journal`, ADR-38 |
| **Read-time overlay** | replace a stale recalled fact with the fresh observation | `context_for` (hook, `context`, `wiki_memory`) | B6 assembly |
| **Laws** | hold rules with their check; evaluate them on new observations | the fold, `sleep`, `owiki world laws` | the wiki, entity extraction, co-change |
| **Predictions** | log expected effects and resolve them | capture, handoff, `sleep`; worktree test runs | `graph/cochange.py`, git |
| **Events** | discrepancies, violations, misses, drift | the world slice, the Pflege panel, the handoff brief | the Pflege panel (M7) |
| **World slice** | assemble the context around what a prompt touches | the inject hook, `context`, `wiki_memory` | `assemble_context` budgets |
| **World tools** | the MCP contract (§10.1) | `owiki mcp` | `mcp_server.build_server` |
| **Twins** | other world models behind the same contract | separate MCP servers, e.g. Cognitive Substrate | — |

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
| `Prediction` | `id`, `made_at`, `made_by` (rule, co-change, agent), `basis`, `target` (files, tests or a variable), `expected`, `due` (commits or a time), `resolved_at`, `outcome` (hit / partial / miss), `score` | `ABOUT → Variable` or `ABOUT_PAGE → Page` | yes |
| `Event` | `id`, `kind` (discrepancy, violation, miss, drift), `at`, `detail`, `resolved_at`, `resolution` | `CONCERNS → Variable`, `FROM_LAW → Law`, `FROM_PREDICTION → Prediction`, `INVOLVES → Assertion` | yes |

B0's snapshot / restore and the portable export (ADR-39) would need to carry the new tables; how COGX holds them is
open (§15).

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
id      = "release"
type    = "file-pattern"
path    = "pyproject.toml"
pattern = '^version = "(?P<value>[^"]+)"'
fact    = { subject = "OpenWiki", predicate = "current release is", object = "v{value}" }
governs = ["OpenWiki | has version", "project | has version"]   # further attribute groups, as subject | predicate
every   = "1m"                                                  # at most once a minute; "0" = slow passes only
```

Code sources get a small default set without configuration (git state, a version file when one is found); C0 decides
which. Bindings can also be *proposed* from memory — facts whose object matches a constant in the code
("RECENCY_FLOOR is 0.9") — but a person accepts them, as with laws (§7.3).

### 6.3 When sensors run

| Where | Which sensors | How it writes |
|---|---|---|
| inject hook (every prompt) | fast ones whose cached value is older than `every`; at most ~200 ms in total | not at all — the overlay (§6.5), plus an `observe` op in the journal |
| capture worker, `sleep`, `handoff prepare` | all, slow ones included | through the two-phase writer (ADR-38) |
| `owiki world observe [--sensor ID]` | the ones asked for | as above |
| MCP world tools | the ones asked about | the journal |

### 6.4 Merge rules

An observation enters `temporal.plan_merge` with `source = "observed"` and its binding's attribute:

1. **Same value as the current observation** → re-affirmed (`last_seen`).
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
  only; OPC UA read-only.
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

A law without a check stays text; it still informs the world slice.

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

Each prediction has a deadline (a number of commits, or a time) and resolves to hit, partial or miss. Scores are kept
per basis — "co-change, top 10: 0.51 of the companions" — so the model's own track record is visible and a weak basis
can be dropped.

### 8.3 Events

Four kinds, all first-class and all resolvable by a person:

| Kind | Raised when |
|---|---|
| discrepancy | a belief contradicts a current observation (§6.4, rules 3 and 5) |
| violation | an accepted law's check fails on new observations |
| miss | a prediction resolves as a miss |
| drift | a decision's precondition changes, or the code leaves a decision (§7.4) |

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
entity in its own right: it observes its environment, keeps its state, knows its rules and answers questions about
them — the cyber-physical entity this direction started from.

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
| Models | Gemini embeddings first (D14), a local fallback | local only (ADR-2) |
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
the clamps are in place. The twin's own agent uses Gemini; OpenWiki's side stays local (ADR-2) and meets the twin only
through MCP.

## 12. Staged delivery plan (C0–C4)

Each stage is shippable and measured on its own, and each can end Path C.

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
  `owiki world observe` (subcommands, ADR-13); an "observed" badge in the Gedächtnis tab; governed attributes leave the
  review list.
- **Builds on:** B7 and B9, the journal and the two-phase writer (ADR-38), `handoff`'s probes, `assemble_context`.
- **Hard part:** the governed attribute groups (B9's paraphrases) and the authority rule, without changing what B7
  decides elsewhere.
- **Exit:** stale facts among the injected facts in governed attributes fall to near zero on the replayed prompts
  (against C0's baseline); the review list shrinks by the governed share; the inject hook's p95 latency grows by at most
  300 ms; the guards hold (§13).

### C2 — Laws, drift and the world slice
- **Goal:** rules and decisions become checkable, and the context is built around what a prompt touches.
- **Build:** `Variable`, `Law`, `Event`; law extraction with review; checks at fold time; decisions recorded with their
  preconditions; drift events; the world slice (§9); events in the Pflege panel and the handoff brief. On the Ableton
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
- **Goal:** the model is judged by what it predicts.
- **Build:** `Prediction`; companion-file and test-at-risk predictions; resolution by commits and test runs (a worktree
  for the tests at risk); misses as events; scores per basis. On the Ableton playground: every write predicts its
  effect and is resolved by reading the value back.
- **Builds on:** `graph/cochange.py`, git, the test suite; the playground's read-back (its review's R6).
- **Hard part:** test runs cost time — only the tests at risk, only when idle or on request.
- **Exit:** online over the history (the PB §13.29 setup), companion files predicted from co-change plus laws beat
  co-change alone (0.51 top-10 recall of rarely-changed files); test-at-risk precision and recall reported; replayed,
  injected misses judged helpful more often than the facts they displace; on the playground, every write's prediction
  resolves within a second and every mismatch becomes an event.

### C4 — The physical twin
- **Goal:** the same model for a working twin, then for a machine, through federation.
- **Build:** the twin contract on the Ableton twin first (§11.5), once its review's R1, R2, R4 and R6 are in; then the
  LCU 9000 simulator (§11.2) for faults with known causes; the LCU 9000 twin's MCP server — in Cognitive Substrate or
  standalone (§15); the manual wikis' variables and laws with shared ids; the telemetry store; learned dependencies
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
| review-list size and its stale share | C1 | `analyze memory --review`, blind labels |
| inject-hook latency, p95 | C1 | the metrics collector |
| drift detection: detected of planted, false alarms per week | C2 | planted drifts on a scratch copy; real use |
| world slice against today's context | C2 | blind judge at equal characters, paired |
| companion-file recall@10; test-at-risk precision and recall | C3 | online over the history |
| calibration per prediction basis | C3 | resolved predictions |
| root-cause accuracy, (a) / (b) / (c) | C4 | simulator faults, exact labels |
| causal-edge recovery against the simulator's truth | C4 | precision and recall |

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
- **Over-observation.** Sensors on things nobody asks about cost latency and add noise; bindings come from the C0
  inventory, not from what is easy to read.
- **Volume.** No samples in the graph (§5.3).
- **Engine.** Kuzu is archived (R10). The new tables follow the LadybugDB move's constraints
  ([`ladybugdb-move.md`](ladybugdb-move.md)), and the portable export must carry them.

## 15. Open decisions

**For the user**

1. **The first environment:** the repository (measurable now), the Ableton twin (runs now; needs R1, R2, R4 and R6 of
   its review), or the LCU 9000 (needs the simulator)? *Recommended: the repository for C0–C1, where the stale-fact
   baseline is; the Ableton twin as the test playground from C2 on — laws, actions, predictions and diagnosis on a
   system that answers back (§11.5).*
2. **Where Path C lives:** inside OpenWiki, in Cognitive Substrate, or federated (§10.3)? *Recommended: the repository
   twin in OpenWiki, machines federated.*
3. **Acting:** may twins act, or stay read-only? *Recommended: read-only through C4.*
4. **Telemetry:** real hardware or an OPC UA server, or a simulator? *The plan starts with the Ableton twin's live
   data and adds a simulator for faults with known causes.*

**Technical**

5. Names of the world tools (`wiki_state` and siblings, or a `world_*` family).
6. Bindings only in the manifest, or also proposed from memory (§6.2)?
7. The telemetry store, SQLite or Parquet (C4).
8. Whether a contradicting late belief is stored at all (§6.4, rule 5) or only reported.
9. Where the LCU 9000 twin's MCP server lives.
10. How laws, predictions and events travel in COGX (e.g. in `--full` metadata).
11. A *Welt* (world) tab in the web UI, or panels inside Gedächtnis.

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
| events and their review | the Pflege panel (`/api/memory/maintenance`), `handoff.render_brief` |
| world tools | `mcp_server.build_server` |
| analysis | `analysis/memory.py`, extended by a model-versus-reality section: discrepancies, violations, calibration |

## 17. Prior art

- **Digital twins.** Kritzinger et al., "Digital Twin in manufacturing: A categorical literature review and
  classification" (IFAC-PapersOnLine, 2018) — model, shadow, twin. Industrial information models — the OPC UA companion
  specifications, the Asset Administration Shell of Industrie 4.0 — are the natural vocabulary for a machine's
  variables once C4 meets a real one; FMI / FMU co-simulation for physics models beyond a toy.
- **Learned world models.** Ha & Schmidhuber, "World Models" (2018); DreamerV3 (Hafner et al., 2023); LeCun's JEPA
  proposal (2022); Genie (2024) — the sense Path C does not take (§2).
- **The agent-memory review.** Hindsight keeps world facts apart from the agent's experiences (MSR §7); AriGraph builds the
  world model of a text game — the current state as a graph, its history as episodes (MSR §9); Nemori stores what memory
  failed to predict (MSR §10), of which §8.3's events are a deterministic form; memory-champ rechecks volatile facts on a
  schedule (MSR §11), which sensors do for real; the transfer-entropy report (MSR §13), rejected for a sparse memory and
  retested here on dense telemetry (§11.3); OpenCog Hyperon (MSR §17) — inference needs a curated vocabulary, which
  reviewed laws provide.
- **Ableton Expert System.** A working two-way twin: classes and instances bound with physical units (a normalized
  0.72 shown as 1,200 Hz), an injector restricted to a fixed set of commands, sound recipes as procedural memory
  captured from state — and, in its review of 2026-10-18 (`docs/Digital_Twin_Review.md` there), the gaps a twin shows
  without Path C's parts: no history, positional identity, safety in the prompt, no read-back (§11.5).
- **Cognitive Substrate.** Tier write-authority, FunctionalVariable anchors, guarded and self-healing actions, and the
  capability ladder L1 predictor → L2 simulator → L3 evolver → L4 cross-domain inference → L5 test synthesis (its
  `docs/ROADMAP.md`). Path C's repository twin reaches L1–L2 for code — predicting a change's effects, then running it
  in a worktree; C4 is its L4 with OpenWiki's documents and memory attached.
