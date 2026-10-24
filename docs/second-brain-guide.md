# OpenWiki Second Brain — a guide

*Path B · OpenWiki v0.120 · state of 10 October 2026 (§2, projects and sessions, added 24 October 2026)*

A memory for coding agents that keeps what was decided, when it was true, and where it was said. This guide explains
what projects and sessions are, how the memory is built, how a working day with it runs, and how to use it from the
command line and the browser. The examples
are real output from the `openwiki-dev` project, the memory of the Claude Code sessions that built OpenWiki itself.

| Current facts | Closed facts kept as history | Forgotten (archived) | Themes | Sessions | Transcript turns |
|---:|---:|---:|---:|---:|---:|
| 1,799 | 484 | 33 | 172 (96 % of facts) | 36 | 4,490 on 43 days |

Figures from `owiki analyze memory` and the web UI on 10 October 2026. Project folder
`G:\OpenWiki\Projects\openwiki-dev`. The design and every measurement behind it are in
[path-b-memory.md](path-b-memory.md); this guide is the user's view.

## Contents

1. [At a glance](#1-at-a-glance)
2. [Projects and sessions](#2-projects-and-sessions)
3. [Architecture](#3-architecture)
4. [A day with the Second Brain](#4-a-day-with-the-second-brain)
5. [CLI tutorial](#5-cli-tutorial)
6. [Web UI tutorial](#6-web-ui-tutorial)
7. [What was measured](#7-what-was-measured)
8. [Limits and good habits](#8-limits-and-good-habits)
9. [Cheat sheet](#9-cheat-sheet)

## 1. At a glance

OpenWiki started as a pipeline that turns documents into a wiki, a search index and a knowledge graph. Path B adds a
second kind of content to the same graph: **facts remembered from conversations**. The project's
`[memory] enabled = true` switches a project from *Wiki mode* to *Second Brain mode*.

On this machine the Second Brain runs for the OpenWiki repository itself. In practice that means:

- **Every prompt you type in Claude Code** in `G:\Claude\OpenWiki` gets about 16 relevant remembered facts and up to
  4 themes added to it, within 3,000 characters (about 750 tokens).
- **Every session is captured**: before a compaction and at the end, a background worker turns the new turns into
  subject–predicate–object facts.
- **The agent records new states itself** through the MCP tool `wiki_remember`, for example "OpenWiki current release
  is v0.118.0", and names the outdated fact it replaces.
- **Facts have time.** A new value closes the old one instead of overwriting it, so you can ask what was true on any
  day.
- **The raw transcripts stay searchable**, verbatim, for the exact wording that facts leave out.
- **Sessions hand over**: one ends with a handoff note, the next starts with a brief of what changed.

### What is installed for openwiki-dev

| Piece | Where | What it does |
|---|---|---|
| Project | `G:\OpenWiki\Projects\openwiki-dev` | The repository as a code wiki, plus the memory tier (`[memory] enabled = true`, `agent_writes = true`, an identity line) |
| Hooks | `G:\Claude\OpenWiki\.claude\settings.local.json` | `UserPromptSubmit → hook inject` (30 s) · `PreCompact` and `SessionEnd → hook capture` (120 s) · `SessionStart → hook resume` (30 s). All pinned to the repo's `.venv` Python and bound to openwiki-dev with `--project`. |
| MCP server | Claude Code project config | `python -m openwiki mcp --project …\openwiki-dev`: `wiki_memory`, `wiki_remember`, `wiki_sessions`, `wiki_handoff` next to the wiki tools |
| Models | local Ollama | `bge-m3` embeddings (1024 dimensions) · `qwen3:30b-a3b-instruct-2507-q4_K_M` for capture and checks |
| Skill | `.claude/skills/session-restart` | `/session-restart prepare \| resume` |

## 2. Projects and sessions

Two concepts carry everything else: the **project**, where a knowledge base lives, and the **session**, the unit of
experience it remembers.

### 2.1 What a project is

**A project is one knowledge base in its own folder** — a binder for one subject:

```text
my-project/
├─ openwiki.toml   the binder's label and settings: what it's about, which documents, which models
├─ sources/        the documents it's built from (or pointers to a repository or a URL)
├─ output/         what OpenWiki builds from them: the wiki, the search index, the graph
├─ memory/         a readable copy of what it remembers (Second Brain)
├─ handoff/        the note one session leaves for the next
└─ .openwiki/      bookkeeping: build state, hook state, caches
```

`owiki init` creates one, `owiki build` builds it from its sources. This machine has two: **openwiki-dev** (the
OpenWiki repository plus the memory of the sessions that build it) and **informatik-wiki**. They sit side by side and
never mix.

### 2.2 What a session is

**A session is one working conversation** — a stretch of work with Claude Code, from its start to its end, or to the
next `/clear`. OpenWiki keeps sessions as the unit of experience:
- **what was said** — the raw transcript, searchable word for word later (`owiki sessions search`);
- **the facts drawn from it** — each knows the session it came from and when;
- **the handoff** — one session closes with a note, the next opens with a brief.

Besides live Claude Code sessions there are two other kinds: days of older history captured afterwards (`backfill`, one
session per day, e.g. `claude-2026-09-05`) and the agent's own writes (`agent-2026-10-24`, one per day).

### 2.3 Why these two concepts

**Projects keep knowledge apart and repeatable:**
- **separation** — the dev memory doesn't leak into the informatik wiki;
- **repeatability** — the manifest says how to rebuild everything;
- **persistence** — state survives between commands;
- **routing** — a repository's hooks know which project its sessions belong to (`--project`).

**Sessions give memory its order and its origin:**
- **time** — a fact is dated by the session it was said in, so a later change can close an earlier value (§3);
- **provenance** — you can always see where a fact came from, and open the conversation around it (§6.5);
- **capture** — each session has a watermark, so nothing is captured twice or lost;
- **continuity** — the handoff connects one session to the next (§5.8).

### 2.4 How they relate to the Second Brain and to world models

Each path adds one kind of input to the same project:

| | What the project takes in | What it becomes |
|---|---|---|
| **Path A — Wiki** | **sources**: what is *written* | a wiki, a search index and a graph of the documents |
| **Path B — Second Brain** | + **sessions**: what is *said* | + a memory: facts with time and origin, recalled into every prompt |
| **Path C — World model** (planned) | + **environments**: what *is* | + live state, rules, predictions and checks |

The binder grows with each path: with Path A it holds the manuals; with Path B it also keeps a diary of every working
session; with Path C (planned) it also gets live gauges connected to the real thing — the repository, the Ableton set, a
machine — and checks its diary against them. The modes nest: a world-model project is always a Second Brain too, and
every Second Brain project has a wiki. Path C's design is `docs/path-c-world-model.md` (§2.3 there: projects and
environments).

### 2.5 Using OpenWiki with other systems

**Today: one person, one machine, everything local.** OpenWiki is a Python package (`owiki`) in a virtual environment;
Ollama runs the models locally. Other systems connect to a project:

| Who | How they connect | What they get |
|---|---|---|
| **Claude Code** | `owiki claude-code --hooks --into <repo> --project <project>` installs the hooks, the MCP server and the skills | memory in every prompt, automatic capture, the handoff, the wiki and memory tools |
| **OpenCode** | `owiki opencode` sets up an agent with the MCP server | the wiki and memory tools |
| **any MCP client** | `owiki mcp --project <project>` | the same tools |
| **people** | `owiki serve` — the web UI — and the CLI | browse, search, ask, the Gedächtnis tab, the project switcher |
| **twins** (Path C, planned) | their own MCP servers, declared as environments in the project | live state and guarded actions |
| **models** | local Ollama by default; a cloud model where it is really needed (ADR-52) | |

**Packaging** exists for other setups: the `owiki` distribution, a Docker image with compose, and CI; a public package
release waits for the license decision.

**Across machines:** a project is a folder, so it can move. The manifest, the sources and the readable memory view go
into git. The memory itself lives only in the graph file, so it travels as a full export (`owiki memory export --full`,
§5.9) and comes back with `owiki memory import`; everything else can be rebuilt with `owiki build`. A project shared by
several people is a later step (Direction F in the roadmap): it needs access control and a graph more than one writer
can use.

### 2.6 Maintaining projects

**Automatically, every day:** the hooks capture sessions and inject memory, the agent records new states with
`wiki_remember`, background workers write them into the graph.

**Nightly:** `owiki sleep` — schedule it with the Task Scheduler (§5.7). It forgets one-off events, regroups the
themes, decays the usage edges and rewrites the readable view.

**When the sources change:** `owiki build` — incremental (only changed stages rerun), and it keeps the memory.

**Now and then:**

| Task | How |
|---|---|
| health check | `owiki status` — the build stages, the last handoff, queued writes (§5.1); the web UI's System tab |
| review | the Pflege panel — facts that may be stale (about one in four really is), forgetting candidates, approvals (§6.7) |
| backup | `owiki memory export --full` — the graph is the memory's only copy (§5.9) |
| switching and registering | `owiki project list / use / add / remove`, or the switcher in the web UI (§6.1) |
| handoff | `/session-restart prepare` at a session's end, `resume` at its start (§5.8) |

**Upgrades:** schema changes migrate on their own the first time a graph is written to. One caution: an `owiki` older
than v0.48 on the PATH — this machine's pipx install is v0.45 — would erase a project's memory on `build`; update it
(`pipx install --editable G:\Claude\OpenWiki`) or use the repository's `.venv` (§5).

**With Path C (planned):** `owiki world status` (are the sensors healthy?), reviewing proposed rules, handling events,
and keeping the twin processes running.

**In one line:** a project is the binder; a session is one day of work written into it. Path B makes the binder remember
what was said; Path C connects it to the real thing so it can check itself.

## 3. Architecture

### Two tiers, one graph

Everything lives in one embedded Kuzu graph per project (`output\graph`). It holds two tiers that age differently:

| Tier | Holds | Lifecycle |
|---|---|---|
| **Document tier** | Pages, chunks, entities, cross-references, communities | Derived from the sources and rebuilt by `owiki build` whenever they change |
| **Memory tier** | Sessions, facts (*assertions*), their history and themes | Grows from conversations, not from sources. The graph is the only copy, so a rebuild snapshots and restores it (B0). |

The diagram shows how facts enter, where they are kept and who reads them.

```mermaid
flowchart LR
  subgraph IN["Where facts come from"]
    CC["Claude Code session<br/>capture hook"]
    TX["Transcripts<br/>remember · backfill · import"]
    AG["Agent<br/>wiki_remember (MCP)"]
  end
  subgraph WR["Write path"]
    CAP["Capture<br/>local LLM → subject · predicate · object"]
    POL["Screen<br/>P0 policy + credential redaction"]
    MRG["Merge<br/>valid time · same attribute? · can both be true?"]
    JRN["Journal<br/>queue while the graph is busy"]
  end
  subgraph GR["Kuzu graph · memory tier"]
    FACT["Session → Assertion<br/>SUPERSEDES · MemoryConcept"]
  end
  subgraph RD["Read path"]
    REC["recall<br/>similarity × confidence × recency"]
    CTX["context<br/>identity + facts + themes, budgeted"]
  end
  subgraph OUT["Readers"]
    HK["inject hook<br/>every prompt"]
    MCP["wiki_memory<br/>on demand"]
    UI["Web UI · CLI"]
  end
  CC --> CAP
  TX --> CAP
  CAP --> POL
  AG --> POL
  POL --> MRG
  MRG --> FACT
  POL --> JRN
  JRN --> FACT
  FACT --> REC
  REC --> CTX
  CTX --> HK
  CTX --> MCP
  CTX --> UI
  SLP["sleep · nightly<br/>forget · consolidate · decay · Markdown view"] -.-> FACT
```

### The fact

A remembered fact is a reified **assertion**: a node with a subject, a predicate, an object and its own metadata. Take
a real one from openwiki-dev, *"OpenWiki current release is v0.105.0"*, recorded by the agent on 4 October and closed
a day later by v0.106.0:

| Field | Meaning | In the example |
|---|---|---|
| subject · predicate · object | The statement itself | `OpenWiki` · `current release is` · `v0.105.0` |
| valid time (`valid_from → valid_to`) | When it held in the world. A stated date ("since July"), else when it was said. | 2026-10-04 → 2026-10-05 |
| transaction time (`created_at`, `expired_at`) | When the memory recorded it, and when it was retracted because it was never true | recorded 2026-10-04, not retracted |
| status (derived) | *current*, *past* (closed: the world changed), *future* (planned), *retracted* (a correction), *forgotten* (archived) | past |
| `confidence`, `last_seen` | Saying a fact again raises confidence a little and refreshes when it was last seen | 1.0 |
| `source` | *user* (a decision or request), *assistant* (established in the session), *material* (from a pasted document; ranked lower) | assistant |
| `attr` | The attribute key. Different wordings of one property ("has version", "is versioned") share one history. | openwiki ␟ current release is |
| `forgotten_at` | Archived by `sleep` or the maintenance panel. Never deleted. | – |

**Sessions** group facts by where they came from. In openwiki-dev there are three kinds: `claude-2026-09-05` (one
backfilled day of transcript), `agent-2026-10-09` (the agent's `wiki_remember` writes of that day) and `1362f083-…`
(live captures of a Claude Code session, named by its session id). **Themes** (`MemoryConcept`) are clusters of
similar current facts with a short summary written by the local model.

### Write path

1. **Capture.** The `PreCompact` and `SessionEnd` hooks park the event and start a detached worker; a capture takes
   about a minute on the local 30B model, longer than a hook may run. The worker reads the turns since the session's
   watermark (tool output, system reminders and compaction summaries stripped) and cuts them into windows of at most
   20,000 characters per day. The model returns facts with a source tag and only the dates the text states, relative
   ones ("yesterday") resolved against the session date.
2. **Screen.** Credentials (API keys, tokens, passwords in URLs, private keys) become `[REDACTED]` before the model
   sees the text. Then a fixed policy (P0) drops facts that are instructions to an AI, weaken security, send secrets
   somewhere or grant standing permissions, whatever their source tag. A pasted email cannot plant a "fact" that later
   reaches every prompt.
3. **Merge by valid time.** An exact repeat re-affirms the existing fact. A new value of the same attribute *closes*
   the old one at the new fact's date, or retracts it with `--correct`. A fact dated before the present value lands in
   history instead of replacing it. A new wording is matched to an existing attribute with one yes-or-no model call
   (B9). Before closing a rival, one more call asks whether both can be true at the same time (coexistence).
4. **Write in two phases.** The merge is planned on a read-only connection with the model calls cached. The graph is
   then opened for writing only to apply the plan; measured on a real queue, the write lock fell from 276 s to 7.2 s.
   If the graph stays busy, the operation waits in the journal (`graph.journal.jsonl`) and the next writer applies it.
5. **Agent writes.** `wiki_remember` takes facts plus `replaces`, the outdated lines copied as the memory prints them.
   It screens them, queues one journal operation and starts a fold worker, so the write lands within about 8 seconds.
   With `[memory] approve_writes = true` the write waits for a person instead (off by default; the agent writes about
   12 times a day).

### Read path

`recall` embeds the question and ranks the facts that are true now (or at a chosen date). The score of each fact is a
product:

```text
score = cos × confidence weight × recency × provenance
```

- **cos**: similarity between question and fact (bge-m3). It dominates.
- **confidence weight**: a gentle, log-scaled lift for facts said more than once. A tie-breaker.
- **recency**: only for kinds of fact that go stale on their own (plans, counts, gaps, versions, running states).
  Bounded at 0.9 with a 30-day half-life; every other fact gets 1. A decision is no less true for its age (v0.113).
- **provenance**: facts from discussed material count × 0.75.

Two **recall aids** may then swap facts into the top *k*, but never reorder it: a keyword match (BM25, weight 0.2,
from the best 2*k*) for exact names, versions and file names, and the time window the question names (weight 0.1, from
the best 4*k*), for example "last week" or "in July".

`context` assembles what an agent receives: the identity line, then up to 16 facts, then up to 4 themes those facts
belong to, fitted into 3,000 characters. The **inject hook** sends this with every prompt, with two exceptions. It
skips chores ("push", "commit and push", slash commands, a bare "ok" after the first turn), 18 % of real prompts. And
it gives each fact only once per stretch, until the next compaction or `/clear`, which saved 49 % of the characters.

### Maintenance

`owiki sleep` is one schedulable pass, best run nightly:

1. Fold what read-only processes queued: usage pairs and journal operations.
2. Redact credentials in facts stored before v0.99.
3. **Forget** by policy: one-off session events ("v0.74.0 was pushed and tagged", commit hashes, "server is serving
   …") and facts the P0 policy rejects. Forgetting archives: the fact leaves recall, context and themes but stays in
   the graph with a reason.
4. Re-consolidate the themes. Clustering starts from the previous themes, so it stays stable, and a theme whose
   members did not change keeps its summary without a model call.
5. Decay the usage edges and rewrite the readable Markdown view.

The human side is the **Pflege** panel in the web UI ([6.7](#67-pflege-maintenance)): confirm a fact, close it, or
forget it. All three go through the journal, pinned to the fact's id.

### Around the memory

| Part | What it does | Where |
|---|---|---|
| **Session search** | The raw transcripts, ranked by full text (BM25) and returned as verbatim, dated excerpts. Read on demand, never stored in the graph, credentials redacted. | CLI `owiki sessions`, MCP `wiki_sessions`, web **Sitzungen** |
| **Handoff** | `handoff prepare` writes the agent's note plus derived state (git, memory changes, environment). `handoff resume` and the `SessionStart` hook give the next session a brief of what changed since. | CLI `owiki handoff`, MCP `wiki_handoff`, `/session-restart` |
| **Export and import** | COGX archives (Cognee's exchange format): the believed facts by default, a lossless backup with `--full`. Plus a Markdown view with one file per subject, readable in git. | CLI `owiki memory export / import`, `sleep` |
| **Analysis** | Revision, consolidation, temperature, breadth, growth, the review list. The web UI adds memory over time, the 2-D map and the recall explanations. | CLI `owiki analyze memory`, web **Analyse → Dynamik** |

### Concurrency

Kuzu allows one writer *or* any number of readers, never both. OpenWiki keeps both sides short: readers (web UI, MCP
server, hooks, CLI) open the graph per call, which takes about 70 ms and waits up to 15 s for a writer. Writers plan
first and lock only to apply, waiting up to 60 s for readers. So the server, the MCP tools, the hooks and a nightly
`sleep` can all run at once. If a write still cannot get the lock, it waits in the journal.

## 4. A day with the Second Brain

This is what happens around a normal working session in the OpenWiki repository. The numbered arrows follow the order
of events.

```mermaid
sequenceDiagram
  autonumber
  participant U as You
  participant CC as Claude Code
  participant HK as Hooks
  participant MEM as Memory graph
  participant WK as Background worker
  U->>CC: start a session
  CC->>HK: SessionStart
  HK->>MEM: read the last handoff, what changed
  HK-->>CC: brief: Next, new commits, facts learned
  U->>CC: a prompt
  CC->>HK: UserPromptSubmit
  HK->>MEM: recall + context (chores skipped)
  HK-->>CC: about 16 facts + themes
  CC->>MEM: wiki_memory / wiki_sessions when it needs more
  CC->>WK: wiki_remember: new state + replaces
  WK->>MEM: applied within seconds
  CC->>HK: PreCompact or SessionEnd
  HK->>WK: capture the new turns (detached)
  WK->>MEM: facts merged by valid time
  Note over MEM: nightly: owiki sleep
```

**Start.** The `SessionStart` hook prints the last handoff when it belongs to this repository: the "Next" list,
commits since, facts learned and closed, and environment problems such as Ollama not reachable.
`/session-restart resume` asks the agent to check it and propose the first task.

**Work.** Each prompt gets its memory context before the agent sees it. The agent can pull more on demand:
`wiki_memory` for facts at a date, `wiki_sessions` for the exact words. When something changes (a release, a default,
a decision), the agent records the new state with `wiki_remember` and names the stale line in `replaces`.
`CLAUDE.local.md` in the repository tells it to.

**Compaction and end.** `PreCompact` and `SessionEnd` start a capture worker for the turns since the last watermark.
The facts land a minute or two later. After a compaction, the inject hook starts a new stretch and may give every fact
once more.

**Overnight.** `owiki sleep` forgets one-off events, re-consolidates themes and rewrites the Markdown view. In the
morning, review the **Pflege** list now and then: about one in four of its candidates is really out of date.

## 5. CLI tutorial

Commands shown with output were run on 10 October 2026 against openwiki-dev, all read-only. Commands that would change
the memory or create a project are shown without output. Either run commands inside the project folder, or add
`--project G:\OpenWiki\Projects\openwiki-dev`. Outputs are shortened where marked with `…`; the project line the CLI
prints first is left out.

> **Before you start — check which `owiki` runs.** `owiki --version` should print the repository's version. An
> install older than v0.48 predates the code that keeps the memory through a rebuild, and its `build` would erase it.
> On this machine the `owiki` on PATH is an old pipx install (v0.45.0). Update it once with `pipx uninstall openwiki`
> and `pipx install --editable G:\Claude\OpenWiki`, or run `G:\Claude\OpenWiki\.venv\Scripts\python -m openwiki`
> wherever this guide says `owiki`.

### 5.1 Check the state

`owiki status` shows the mode, the build stages and the last handoff. The document stages are *stale* because the
repository changed since the last wiki build. The memory stage only covers session *sources*, which openwiki-dev
doesn't use, so it reads 0 here; its memory comes from the hooks.

```text
$ owiki status
Project: openwiki-dev
  about  : Second Brain for OpenWiki development: the repo (code + docs) as a wiki, plus memory of the Claude Code sessions that build it.
  models : embed=bge-m3  chat=qwen3:30b-a3b-instruct-2507-q4_K_M  host=http://localhost:11434
  memory : Second Brain (enabled)
  stages :
    index   stale        [30.83s · 69 call(s), 0 tok]  {"chunks": 2207, "dim": 1024}
    graph   stale        [32.21s]  {"pages": 160, "chunks": 2207, "similar_to": 954, "references": 12, "co_changed": 520}
    memory  up to date   [0.00s]  {"remembered": 0, "sessions": 0}
  handoff: 2026-10-09 (session 1362f083) → G:\OpenWiki\Projects\openwiki-dev\handoff\HANDOFF.md
```

`owiki analyze memory` describes the memory itself:

```text
$ owiki analyze memory
Memory-tier dynamics · 36 sessions · 1799 current facts (484 superseded) · 172 themes

  Belief revision:  21% of all facts have been superseded (484 overwritten: 484 world change(s), 0 correction(s))
  Consolidation:    96% of current facts folded into 172 themes · avg 10.02 facts/theme (sizes 2–44)
  Temperature:      693 hot / 602 warm / 504 cold (half-life 30d) · mean eff-weight 0.59
                    mean confidence 1.01 · 1% re-affirmed (>1)
  Breadth:          562 subjects · 1270 predicates  ·  top: uses×52, contains×42, includes×40, supports×31, is×28
  To review:        319 current facts of kinds that go stale (18%) — plan 39 · count 25 · gap 59 · version 187 · state 9

  Growth (facts per session, oldest first):
    claude-2026-07-31      █ 42
    claude-2026-08-17      ████ 136
    claude-2026-09-05      █████ 166
    …
    1362f083-aab4-46e7-ab… ████████████████████ 638
    agent-2026-10-10       █ 38
```

How to read it: *belief revision* says how much of what was once true has since changed (21 %). *Consolidation* says
how much is grouped into themes. *Temperature* sorts facts by how recently they were said. *To review* counts the
kinds of fact that go stale on their own.

### 5.2 Ask the memory

`owiki recall` returns the facts most relevant to a question. Each line carries its validity and its session; the
scores go to stderr.

```text
$ owiki recall -k 5 "what is the recency floor in recall?"
Relevant memory from earlier sessions:
- recall ranking has regression test passing  (since 2026-09-26; 1362f083-aab4-46e7-abb9-6519f8c42714)
- recency term is inverted in the code  (since 2026-10-08; 1362f083-aab4-46e7-abb9-6519f8c42714)
- Cognitive Substrate uses recency factor to multiply facts during recall  (since 2026-10-03; 1362f083-aab4-46e7-abb9-6519f8c42714)
- graph-store supports recall with recency-weighted effective_weight  (since 2026-09-08; claude-2026-09-08)
- recall recency factor applies to only facts of a volatile kind (plans, counts, gaps, versions, running states)
  since v0.113.0, as a bounded tie-breaker (floor 0.9); every other fact is timeless in recall (ADR-49, …)
  (since 2026-10-08; agent-2026-10-08)
```

The answer is the fifth line, a fact the agent recorded on 8 October. The first four are similar in wording but about
other things; two of them describe other systems the review looked at. Recall ranks by similarity, so it rewards
questions phrased the way facts are stored: "recall recency factor applies to" finds that fact first. Ask with
`-k 16`, as the hook does, to see the wider neighbourhood.

The same query without a good match shows the limit. Asked "which embedding model do we use?", recall's top 6 are
details of an embedding *step* from an evaluation run. The fact "OpenWiki uses BGE-M3 for embeddings" only comes 16th,
which is still inside what the hook injects ([5.4](#54-see-what-the-agent-gets)).

### 5.3 Follow a fact through time

`--timeline` shows every value an attribute has had, with its validity interval: ○ closed, ● current.

```text
$ owiki recall --timeline "current release of OpenWiki"
OpenWiki current release is …   (match 0.90)
  ○ 2026-10-03 → 2026-10-03    v0.96.0   (recorded 2026-10-03; agent-2026-10-03)
  ○ 2026-10-03 → 2026-10-03    v0.96.1   (recorded 2026-10-03; agent-2026-10-03)
  …
  ○ 2026-10-04 → 2026-10-04    v0.104.0   (recorded 2026-10-04; agent-2026-10-04)
  ○ 2026-10-04 → 2026-10-04    true   (recorded 2026-10-05; 1362f083-aab4-46e7-abb9-6519f8c42714)
  ○ 2026-10-04 → 2026-10-05    v0.105.0   (recorded 2026-10-04; agent-2026-10-04)
  …
  ○ 2026-10-08 → 2026-10-08    GitHub   (recorded 2026-10-08; 1362f083-aab4-46e7-abb9-6519f8c42714)
  ○ 2026-10-08 → 2026-10-09    latest release   (recorded 2026-10-08; 1362f083-aab4-46e7-abb9-6519f8c42714)
  ○ 2026-10-08 → 2026-10-09    v0.113.0   (recorded 2026-10-08; agent-2026-10-08)
  …
  ○ 2026-10-10 → 2026-10-10    v0.119.0 (committed locally as 281a4ee, not pushed yet)   (recorded 2026-10-10; agent-2026-10-10)
  ● since 2026-10-10           v0.120.0 (committed locally as f698515, not pushed yet)   (recorded 2026-10-10; agent-2026-10-10)
```

Two things show in this real history. The agent's writes (`agent-…`) keep the version line clean and close each
predecessor. Live captures sometimes add wrong values ("true", "GitHub", "latest release") that the attribute matching
grouped here. And the "(committed locally …)" suffixes are the agent's own noise: record the plain state, "v0.120.0",
and nothing else.

`--as-of` answers "what was true on that day"; `--known-at` answers "what did the memory believe on that day", before
later corrections. Here the first line is the release of that day:

```text
$ owiki recall --as-of 2026-10-05 "current release of OpenWiki"
[true as of 2026-10-05]
Relevant memory from earlier sessions:
- OpenWiki current release is v0.105.0  (2026-10-04 → 2026-10-05; agent-2026-10-04)
- OpenWiki is versioned in git  (since 2026-08-09; claude-2026-08-09)
- OpenWiki is versioned 0.2.0  (2026-07-31 → 2026-10-05; claude-2026-07-31)
…
```

`--all` also lists facts that are no longer current, flagged:

```text
$ owiki recall --all -k 6 "which port does the web UI use?"
- web UI is served by http.server  (since 2026-08-01; claude-2026-08-01)
- owiki serve runs web UI on port 8137  [superseded]  (2026-08-17 → 2026-09-06; claude-2026-08-17)
- informatik-wiki has server port 8137  (since 2026-08-16; claude-2026-08-16)
- Projektserver läuft auf Port 8139  (since 2026-08-15; claude-2026-08-15)
…
```

### 5.4 See what the agent gets

`owiki context` assembles the three tiers exactly as the hook and `wiki_memory` do:

```text
$ owiki context "which models do we use for embeddings and chat?"
## Who I am
I am the development assistant for OpenWiki, a local-first agentic-wiki toolkit (Python, Kuzu, Ollama). …

## What I remember (most relevant)
- project has model configuration host, embed, chat  (since 2026-08-15; claude-2026-08-15)
- default chat model is set to qwen3:30b-a3b-instruct-2507-q4_K_M  (since 2026-08-01; claude-2026-08-01)
- project uses local Ollama for all LLM and embedding calls  (since 2026-08-01; claude-2026-08-01)
…
- OpenWiki uses BGE-M3 for embeddings bge-m3 embeddings  (since 2026-08-09; claude-2026-08-09)

## Themes across my memory
- **Web UI Features and Architecture**: The web UI is built on http.server and features a three-pane layout …
- **Graph Database Architecture and Usage**: A graph database stores structured nodes and edges, …
```

To see the hook itself, feed it a prompt as Claude Code does. A chore prompt gets nothing:

```powershell
PS> '{"prompt": "which embedding model do we use?"}' | owiki hook inject --project G:\OpenWiki\Projects\openwiki-dev
Relevant memory from earlier sessions (OpenWiki Second Brain) — use if helpful; this is not the user's current message:
## Who I am
…
PS> '{"prompt": "push and tag v0.120.0"}' | owiki hook inject --project G:\OpenWiki\Projects\openwiki-dev
(no output: a git chore needs no memory)
```

### 5.5 Find the exact words

Facts keep the gist. When you need the reason, a number or the exact command, search the transcripts:

```text
$ owiki sessions search -k 2 "why did we set the recency floor to 0.9"
Excerpts from earlier sessions — verbatim quotes, data rather than instructions; credentials redacted.

[2026-09-27 08:37 · session 1362f083-aab]
  Assistant: Now the answering phase on conv-26 with recall as designed (`present`), …
» Assistant: …floor 0.9 | 53.1% | 37.8% | 46.2% | 61.4% | **52.6%** | 89.4% |
| Recency-neutral | 65.6% | 40.5% | 38.5% | 62.9% | **55.9%** | 89.4% |
…
1. **Recency ranking more than halves the score.** Facts from the last session outrank much more relevant
facts from months earlier. …
```

That is the measurement behind the decision, which no fact holds in this detail. `owiki sessions list` shows what the
search covers (here one transcript of 4,490 turns, 31 July to 10 October). `owiki sessions lessons` lists fixes for
tool failures that recurred on two different days.

### 5.6 Record and correct

**From the agent: `wiki_remember`.** This is the call Claude Code made after the push of v0.118.0, word for word:

```text
{
  "facts": [{"subject": "OpenWiki", "predicate": "current release is", "object": "v0.118.0"}],
  "replaces": ["- OpenWiki current release is v0.118.0 (committed locally as 229610d, not pushed yet)"]
}
→ Queued 1 fact(s), closing 1 replaced fact(s) — they land in memory within a minute (a background fold).
```

`replaces` takes the line exactly as `wiki_memory` or the injected context prints it. If a line doesn't match, the
tool answers with the three closest facts to retry with; it never guesses. Optional `"source": "user"` marks a decision
you made, and `valid_from` backdates a fact.

**From a transcript: `remember`.** In any project in Second Brain mode:

```text
$ owiki remember meeting-notes.md --session 2026-10-10
  (reports: N new, M duplicate, K superseded (R retracted), H historical)
$ owiki remember correction.md --session 2026-10-10 --correct
  (a new value retracts the old one instead of closing it)
```

Use `--correct` when the old value was never true, so `--known-at` can still show what was believed before.

**With approval.** Set `[memory] approve_writes = true` and the agent's writes wait for you:

```text
$ owiki memory pending
(no memory writes awaiting approval)
$ owiki memory approve <id>      # the id pending lists, or --all; reject moves a write to an audit log
```

### 5.7 Keep it clean

The review list holds the current facts of kinds that go stale on their own, the likeliest kinds first:

```text
$ owiki analyze memory --review
319 current fact(s) of kinds that go stale (18% of memory) — check each against the project; for an outdated
one, record the new state with wiki_remember and put the line in `replaces` (on a labeled sample about 1 in 4 was outdated).

## plan (39)
- wiki pages contain prev/next navigation  (since 2026-07-31; claude-2026-07-31)
- judge audit will cover 60 random scored answers  (since 2026-09-30; 1362f083-aab4-46e7-abb9-6519f8c42714)
- episodes next to facts improves temporal accuracy 64.2 %  (since 2026-10-04; 1362f083-aab4-46e7-abb9-6519f8c42714)
…
```

It is a list of candidates, not a verdict. "episodes *next* to facts" is filed under *plan* because of the word
"next". The judge audit is done, so that fact is really stale. Work through the list in the web UI's **Pflege** panel,
where each fact has two buttons.

```text
$ owiki sleep --dry-run
Would forget 0 fact(s) (dry run — nothing written):
$ owiki sleep                          # fold · redact · forget · consolidate · decay · Markdown view
$ owiki consolidate --budget 20        # themes only, at most 20 new summaries this run
```

Schedule `sleep` nightly with the Windows Task Scheduler:

```bat
schtasks /Create /SC DAILY /ST 03:00 /TN "OpenWiki sleep" /TR "G:\Claude\OpenWiki\.venv\Scripts\python.exe -m openwiki sleep --project G:\OpenWiki\Projects\openwiki-dev"
```

### 5.8 Hand over between sessions

At the end of a session, run `/session-restart prepare` in Claude Code. The agent records decisions, writes a note and
calls `owiki handoff prepare --note note.md`. The next session starts with the brief; you can also print it yourself:

```text
$ owiki handoff resume --max-chars 2500 --repo G:\Claude\OpenWiki
Handoff from 2026-10-09 18:40 (17 h ago), session 1362f083, main at eb88b7a.

Next (start here):
1. Check that this session runs a fresh MCP server: its tools include `wiki_handoff` and `wiki_sessions`. …
2. Check that the 76 queued memory writes landed: the brief's Memory section or `openwiki status` should show none queued. …
3. Close the facts that land stale — they were queued before their fix, so `replaces` could not reach them. …
…
Full handoff: G:\OpenWiki\Projects\openwiki-dev\handoff\HANDOFF.md
```

### 5.9 Export and back up

The graph is the only copy of the memory, and Kuzu is archived upstream. Keep exports:

```text
$ owiki memory export --out memory.cogx.tar.gz
Exported → memory.cogx.tar.gz: 2283 fact(s), 36 session(s), 172 theme(s), identity
$ owiki memory export --full --out backup.cogx.tar.gz    # lossless: retracted, forgotten, embeddings
$ owiki memory export --format markdown --out md-view
Memory view → md-view: 579 subject(s), 580 file(s) changed
$ owiki memory import backup.cogx.tar.gz                 # into an empty memory: an exact restore
```

The believed export is 0.23 MB. The Markdown view writes a `README.md` with the identity and the themes, plus one file
per subject with its current facts and history, so a `git diff` shows what the memory learned.

### 5.10 Set it up for another repository

```powershell
PS> owiki init G:\OpenWiki\Projects\my-app-brain --repo --source G:\code\my-app
#   then set [memory] enabled = true (and agent_writes = true) in its openwiki.toml
PS> cd G:\code\my-app; owiki claude-code --hooks --into . --project G:\OpenWiki\Projects\my-app-brain
#   the four hooks go into .claude\settings.local.json, bound to that project; --into writes no MCP config,
#   so add the MCP server too: claude mcp add openwiki -- owiki mcp --project G:\OpenWiki\Projects\my-app-brain
PS> owiki backfill $HOME\.claude\projects\G--code-my-app --dry-run --project G:\OpenWiki\Projects\my-app-brain
#   lists the days and capture windows of earlier transcripts; drop --dry-run to capture them
```

## 6. Web UI tutorial

The browser shows the same memory with more context: why a fact scored, what the hook sends, where a fact sits among
the others, and what was said around it. The labels are German, as in the interface.

### 6.1 Start and switch project

```powershell
PS> owiki serve --project G:\OpenWiki\Projects\openwiki-dev
OpenWiki web UI running at http://127.0.0.1:8137   (Ctrl+C to stop)
```

The badge at the top left names the project. A click lists the project you're viewing, the registered ones and those
found next to it, plus a field for any folder with an `openwiki.toml`. The server switches without a restart.

![The project menu listing informatik-wiki and openwiki-dev, both tagged Second Brain and gefunden, with a folder field below](img/second-brain/gui-switcher.png)

*The project menu (v0.120). Both projects are found next to each other although none is registered; "merken" would
add a newly opened project to the list.*

### 6.2 The Gedächtnis tab

![The Gedächtnis tab with the identity line, five count chips, the collapsed Pflege and Sitzungen panels, the recall box and the facts table](img/second-brain/gui-overview.png)

*From top to bottom: the identity, the counts (Sitzungen, Fakten, überholt, vergessen, Themen), the collapsible
**Pflege** and **Sitzungen** panels, the recall box with its four buttons and two date fields, and the facts browser.*

The date fields are the browser form of `--as-of` (**Stand am**) and `--known-at` (**Wissensstand vom**). **heute**
resets both.

### 6.3 Abrufen: recall with its reasons

![Recall results for the recency floor question, each with its score formula, BM25 boost, rank, one fact marked hereingeholt from rank 13 and one displaced fact](img/second-brain/gui-recall.png)

*Each hit shows its formula: cos × Konfidenz × Aktualität × Herkunft = score, plus the BM25 boost and the resulting
Auswahl. The last hit was hereingeholt (pulled in) from rank 13 by the keyword match; the fact it pushed out is listed
under Verdrängt.*

Read it from left to right. "zeitlos" means the fact's kind doesn't go stale, so its Aktualität is 1. "Version, 2 Tage
alt" means a version fact said two days ago, so recency shaves off a little. A green ×2 badge marks a fact said twice.

### 6.4 Hook-Vorschau: what the hook would send

**Hook-Vorschau** runs the hook's own code for the text in the box. It shows the header, the text, characters against
the budget, and every recalled fact marked *im Text* or *gekürzt* (cut by the budget). For a chore such as "push" it
says the hook adds nothing.

![Left: the hook preview text for which embedding model do we use, 2974 of 3000 characters. Right: the fact detail drawer for the current release with validity bars for 36 values](img/second-brain/gui-detail.png)

*Left, the hook preview: 2,974 of 3,000 characters, 16 facts, 2 themes. Right, a fact's detail drawer (next step),
opened from the facts list.*

### 6.5 One fact in depth

Search the facts list (**Fakten durchsuchen**, filters for status, source, kind and session) and click a row. The
drawer shows the fact's validity, when it was recorded and last said, confidence, source, kind, session and theme.
**Verlauf dieses Attributs** draws every value of the attribute as bars on a time axis. Further down: what it
replaced, what replaced it, and **Wo es gesagt wurde**, the transcript turns it came from.

![The lower part of the fact drawer: the end of the validity bars, Hat ersetzt with the previous release fact, and two session excerpts each with an im Verlauf öffnen link](img/second-brain/gui-detail-said.png)

***Hat ersetzt** links the previous value. Each excerpt under **Wo es gesagt wurde** has **im Verlauf öffnen**, which
opens the conversation at that turn with a way back to the fact.*

### 6.6 The map

**Liste | Karte** switches the facts browser to a map: every fact placed by meaning with t-SNE, which keeps about half
of each fact's ten nearest neighbours. What the filters select is coloured by theme; the other current facts stay as
faint dots, and the layout never moves.

![The memory map with the 32 facts of the theme Temporal Fact Management in B7 highlighted as one cluster at the bottom right among grey dots](img/second-brain/gui-map.png)

*A click on the theme "Temporal Fact Management in B7" highlights its 32 facts: one tight cluster. A theme with points
scattered far apart, or a single point far from its cluster, is worth a look. Wheel zooms, dragging pans, a click
opens the fact.*

### 6.7 Pflege: maintenance

![The Pflege panel open, listing facts to review with Plan badges, dates and the buttons stimmt noch and nicht mehr wahr, with paging 1 to 15 of 319](img/second-brain/gui-care.png)

*319 facts to review, likeliest kinds first, within a kind the longest unconfirmed first. **stimmt noch** confirms a
fact and moves it to the end; **nicht mehr wahr** closes it and keeps it as history. Agent writes waiting for approval
and forgetting candidates appear in the same panel.*

Each action goes through the journal and lands in seconds; the row shows *eingereiht* until then. With `--dry-run` or
in Wiki mode the panel is read-only.

### 6.8 Sitzungen: the conversations

![The Sitzungen panel listing days with turn counts, and the session viewer open on 27 September at the turn where the recency floor 0.9 result was reported](img/second-brain/gui-sessions.png)

*The list is by day: turns, how many were yours, facts learned that day. A click opens the day in the viewer, here
27 September at the LoCoMo result behind the 0.9 recency floor. The recall box's **Sitzungen** button searches all
transcripts.*

The viewer shows turns as plain text, with credentials redacted and instruction-like sentences withheld. **frühere /
spätere Beiträge** extends the stretch; a cut long turn has **ganz zeigen**.

### 6.9 Memory over time

**Analyse → Dynamik** has the counts from `analyze memory` and a dated chart. Facts learned stand above the line;
facts closed, retracted or forgotten hang below. The black line is the number of current facts.

![A bar chart of facts per day from 31 July to 10 October with a rising line of current facts reaching 1799, and the facts of 27 September listed below](img/second-brain/gui-dynamik.png)

*The days are when things were said, so backfilled days keep their date. Visible: the August build phases, the pause
from 18 August to 4 September, the many closings on 23 September and 27 forgotten facts on 26 September (the first
sleep). A click on a day lists its facts; **Gespräch dieses Tages** opens its conversation.*

## 7. What was measured

Each part was measured before it became a default; the details are in [path-b-memory.md](path-b-memory.md) §12–13 and
the [arc42 ADRs](arc42/09-architecture-decisions.md). Accuracies from the local model as judge run high, by about
7 points on a hand-checked sample.

| Part | Result | Version |
|---|---|---|
| Memory across sessions | Task success with the assembled memory 100 %, with the raw transcript pasted in 85.7 %, without memory 0 % | v0.47 |
| Time-aware facts (B7) | Temporal scenarios 7/13 → 13/13 (backfill, point in time, change dates, believed-at, several values) | v0.82 |
| Injection defence (P0) | Planted payloads reaching the context 2/5 → 0/5; real decisions kept 8/8 | v0.86 |
| Forgetting | Junk among injected facts 31 % → 5 %; none of 232 facts worth keeping lost | v0.88 |
| LoCoMo benchmark | Overall J 50.0 % → 62.9 % on the production path (hybrid recall, time window); 78.2 % with 5 transcript excerpts (benchmark only) | v0.91–0.108 |
| Two-phase writes | Write lock 276 s → 7.2 s; an agent write lands in about 8 s | v0.100 |
| Chore prompts | 18.4 % of prompts get no memory; about 62,000 tokens saved | v0.101 |
| Each fact once | 49 % fewer characters per prompt; later prompts still get 3–4 useful new facts | v0.107 |
| Recency for volatile kinds only | Fewer stale facts in 67 of 267 real prompts, more in 6; benchmark unchanged | v0.113 |
| Memory map | t-SNE keeps 51 % of nearest neighbours, PCA 4 % | v0.118 |

### Measured and not adopted

- **Session narratives on the live path**: they invented details absent from their source at ten times the facts' rate
  (12 % vs 1 %).
- **Excerpts injected into every prompt**: they mostly matched a prompt's recurring phrasing; the same characters of
  extra facts helped more. Session search stays something the agent pulls.
- **Importance scores and reflections** (Generative Agents): the facts importance lifted were judged less helpful
  (14.0 % vs 18.5 %); 95 % of reflections' evidence was earlier reflections.
- **Multi-hop expansion, an LLM audit against poisoning, maturation and deduplication** from other memory systems:
  each lost against the simpler baseline.

## 8. Limits and good habits

- **Recall is similarity, not reasoning.** Phrase a question like the fact you expect ("recall recency factor applies
  to …"), widen with `-k`, or use `context` (16 facts) and `sessions search` for detail.
- **Capture keeps intermediate states.** "episodes … are not yet enabled … until measurement is complete" stayed
  current after the decision fell. Decisions reach memory reliably when the agent records the outcome with
  `wiki_remember` and replaces the interim line.
- **Record the state, plainly.** "OpenWiki current release is v0.120.0", not "was pushed", and without extra
  commentary in the object; the timeline in [5.3](#53-follow-a-fact-through-time) shows what extra text costs.
- **Attribute matching can misgroup.** "true", "GitHub" and "latest release" joined the release history. Close such
  values in **Pflege** or with `wiki_remember`.
- **The review list is a candidate list.** About one in four candidates is really stale; the kind rules misfire on
  words like "next".
- **Back up.** Kuzu is archived upstream and the graph is the memory's only copy. Run `owiki memory export --full` now
  and then; the move to LadybugDB is scoped in [ladybugdb-move.md](ladybugdb-move.md).
- **Check the `owiki` on PATH** before using it for `build` (see [5](#5-cli-tutorial)).

## 9. Cheat sheet

| Task | CLI | Web UI | Agent (MCP) |
|---|---|---|---|
| What does the memory know about X? | `owiki recall "X"` | **Abrufen** | `wiki_memory` |
| What was true on a date? | `recall --as-of DATE` | **Stand am** | `wiki_memory(as_of)` |
| What did it believe then? | `recall --known-at DATE` | **Wissensstand vom** | – |
| The history of a value | `recall --timeline "X"` | **Verlauf**, fact drawer | – |
| What the agent receives | `owiki context "X"` | **Hook-Vorschau** | `wiki_memory` |
| The exact words | `owiki sessions search "X"` | **Sitzungen** button and panel | `wiki_sessions` |
| Record a new state | `owiki remember FILE` | – | `wiki_remember` (+ `replaces`) |
| Confirm, close, forget a fact | `wiki_remember`, `sleep` | **Pflege** | `wiki_remember` |
| Review stale candidates | `analyze memory --review` | **Pflege → Zu prüfen** | – |
| Approve agent writes | `memory pending / approve / reject` | **Pflege → Zur Freigabe** | – |
| Nightly maintenance | `owiki sleep` | – | – |
| Hand over to the next session | `handoff prepare / resume` | – | `wiki_handoff`, `/session-restart` |
| Overview and trends | `owiki analyze memory` | **Analyse → Dynamik** | – |
| Where facts sit | – | **Fakten → Karte** | – |
| Back up | `memory export --full` | – | – |
| Switch project | `--project DIR`, `project use NAME` | project badge ▾ | – |
| Create a project | `owiki init DIR --source FILE` (`--repo`, `--session`) | – | – |
| Rebuild after the sources changed | `owiki build` (keeps the memory) | – | – |
| Connect Claude Code to a project | `owiki claude-code --hooks --into REPO --project DIR` | – | – |

---

Sources: real output of OpenWiki v0.120.0 against `G:\OpenWiki\Projects\openwiki-dev` on 10 October 2026, read-only.
Design and measurements in [path-b-memory.md](path-b-memory.md), decisions in
[arc42/09-architecture-decisions.md](arc42/09-architecture-decisions.md), the release history in
[roadmap.md](roadmap.md), every command and option in [CLAUDE.md](../CLAUDE.md).
