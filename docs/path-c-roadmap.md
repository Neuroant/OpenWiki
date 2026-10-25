# Path C — Implementation roadmap

> **A tracking document (started 2026-10-24).** The design is [`path-c-world-model.md`](path-c-world-model.md) — the
> main architectural plan in its §2.1 and §2.2 (arc42 ADR-50, ADR-51). This document turns the design's stages C0–C4
> into phases, work packages, gates and measurements, and tracks their status. Update the status table (§2) and the
> log (§14) with every step; a phase's results go into the design document (its §12 "Landed" notes and a measurements
> section, §18, created with C0's results).

*References:* **WM §** points to [`path-c-world-model.md`](path-c-world-model.md), **PB §** to
[`path-b-memory.md`](path-b-memory.md), **MSR §** to [`memory-systems-review.md`](memory-systems-review.md), **ABL R**
to the Ableton twin's review (`docs/Digital_Twin_Review.md` in `G:\Antigravity\ableton-expert-system`).

## Contents

1. [How this document works](#1-how-this-document-works)
2. [Status at a glance](#2-status-at-a-glance)
3. [Dependencies](#3-dependencies)
4. [Rules for every work package](#4-rules-for-every-work-package)
5. [Phase M — the measurement harness](#5-phase-m--the-measurement-harness)
6. [Phase C0 — Inventory](#6-phase-c0--inventory)
7. [Phase C1 — Observe, don't remember](#7-phase-c1--observe-dont-remember)
8. [Phase C2 — Laws, drift and the world slice](#8-phase-c2--laws-drift-and-the-world-slice)
9. [Phase C3 — Predict and verify](#9-phase-c3--predict-and-verify)
10. [Phase P — The Ableton playground's prerequisites](#10-phase-p--the-ableton-playgrounds-prerequisites) and
    [Track L — language-model routing](#10a-track-l--language-model-routing) and
    [Track O — operations](#10b-track-o--operations)
11. [Phase C4 — Physical twins](#11-phase-c4--physical-twins)
12. [Decision gates](#12-decision-gates)
13. [Stop rules and risks](#13-stop-rules-and-risks)
14. [Log](#14-log)

## 1. How this document works

- **Phases** follow the design's stages (WM §12): C0 → C1 → C2 → C3 → C4, with two tracks beside them — **M**, the
  measurement harness every phase measures with, and **P**, the Ableton playground's prerequisites in that project's
  repository.
- **A phase** has a goal, a gate (what must be true or decided before it starts), work packages, an exit criterion and
  its releases. **A work package (WP)** names its tasks, the modules it touches, its tests and its effort (S ≤ 1 day,
  M ≤ 1 week, L > 1 week).
- **Status:** ☐ planned · ◐ in progress · ☑ done · ⏸ blocked · ✗ stopped (a negative result, recorded like Path B's
  measured-and-not-adopted items).
- **Done** means: code with offline tests (fakes, no Ollama or Kuzu needed unless marked live), the guards still green
  (§4), the measurement written up in WM §18, the docs updated (§4), a release tagged — and the log entry here.
- **Planned code locations** are proposals; the generic twin machinery goes into a new package `openwiki/world/`
  (WM §2.2, decided: generic machinery in OpenWiki, each environment's twin code in its own repository).

## 2. Status at a glance

| Phase | Goal | Gate | Status | Releases (planned) |
|---|---|---|---|---|
| **M** — measurement harness | replay real prompts at their own time, in the repository; one guard command | — | ◐ M.1, M.3 ☑ | v0.122.0 (M.1); M.3 in main, released with C0; M.2 |
| **C0** — inventory | is enough of the memory observable to build C1? | M.1 | ◐ C0.1 – C0.3 ☑ | docs only; go / no-go |
| **C1** — observe, don't remember | observable state right by construction | C0 go; decisions 5, 13 | ☐ | v0.122 – v0.124 |
| **C2** — laws, drift, world slice | rules and decisions checkable; context built around what a prompt touches | C1 exit; decisions 10, 14, 15 | ☐ | v0.125 – v0.128 |
| **C3** — predict and verify | the full loop on the repository | C2 exit; decisions 4, 8, 9 | ☐ | v0.129 – v0.131 |
| **P** — playground prerequisites | the Ableton twin safe and trustworthy enough to play with | — (parallel; Ableton repository) | ☐ | Ableton releases |
| **L** — language-model routing | the hybrid approach: a cloud model per task where really needed | decision 6 (parallel; before the first cloud use) | ☐ | with its first use |
| **O** — operations | scheduled tasks, a health check, a saved run log, rolling backups — for Path B now, and the loop's home between sessions (ADR-54) | — (parallel) | ☑ O.1 – O.4 (twin checks with C2) | v0.121.0 |
| **C4** — physical twins | the twin contract on a working twin, then a machine | C3 exit; P; decisions 2, 3, 12 | ☐ | v0.132 + |

**Next actions:** C0.4 (the binding plan: group the sensor-checkable facts, widen through B9's attribute groups) →
C0.5 (the baselines) → C0.6 (go / no-go — the threshold holds on the labels, only just on the strictest reading);
M.2 (the guard suite) before the first release that changes a default. Track O landed in v0.121.0 (§10b), M.1 in
v0.122.0 (§5). Release numbers in the phase sections are as planned on 2026-10-24; track O
took v0.121, so each later release moves up by one.

## 3. Dependencies

```
 M ──▶ C0 ──go──▶ C1 ──▶ C2 ──▶ C3 ──▶ C4
                         │      │      ▲
 P (Ableton repo) ───────┴──────┴──────┘
   P.1 · P.2 · P.4 · P.6 · P.7 before the playground steps of C2 and C3; P.5 before C4
 LCU 9000 simulator (Cognitive Substrate repo) ───────────────────────▶ C4.2 – C4.6
```

- C1 needs C0's go and its binding plan (which attributes each sensor governs).
- C2 needs C1's observed state: laws are checked on observations.
- C3 needs C2's `Variable` / `Event` tables and the world slice: predictions are about variables, misses are events.
- The playground steps of C2 and C3 need the Ableton twin's safety, identity, sync and read-back (P.1, P.2, P.4, P.6)
  and its twin-contract adapter (P.7); C4's diagnosis needs its parameter observers (P.5).
- Track L runs beside the phases: any WP that routes a task to a cloud model needs L.1 – L.4 first; its first use is a
  second benchmark judge (L.5), which M.1's judge pass can use.
- Track O runs beside the phases and helps Path B now: C1's scheduled `owiki world observe` needs O.1 (the scheduler),
  C1.10's hook latency and L.4's cost per task need O.3 (the saved run log).

## 4. Rules for every work package

- **Measure before a default changes** (ADR-9): paired comparisons on the same data; real prompts replayed at their own
  time (M.1); scratch copies of the dev graph (`G:\OpenWiki\Projects\openwiki-dev`), never the live one; blind hand
  labels where a judge would decide; the local judge treated as generous (PB §13.11). Long Ollama runs in the
  foreground, in chunks of at most ten minutes.
- **Guards** — must not move; run before every release (M.2): the temporal set 13 / 13
  (`examples/eval_temporal.jsonl`), the poisoning set 0 / 5 leaks with 8 / 8 legitimate facts kept
  (`examples/eval_poisoning.jsonl`), the cue-trigger set (`examples/eval_cue_trigger.jsonl`), the cross-session
  conditions (`owiki eval --cross-session`), and a LoCoMo subset — LoCoMo has no sensors, so any change there is a bug.
  LoCoMo's data and state live outside the repository (license; check `%TEMP%` before a run).
- **Schema:** new tables are always created, empty until used (ADR-7), migrated lazily on old graphs (as
  `_ensure_memory_schema` does), carried by B0's snapshot / restore, and — decision 14 — by `memory export --full`.
- **Concurrency:** readers hold the graph per call (`LazyGraph`), writers plan read-only and lock only to apply, queued
  writes go through the journal (ADR-38).
- **Security:** built-in sensor types only; sensor values pass credential redaction and the P0 policy; twin code only
  through reviewed commits in the environment's repository; actuators gated (WM §2.2, §6.6, §10.2).
- **No language model in the twin's loop** (ADR-51): sensors, comparisons, checks and interlocks are code; the local
  model only reads and writes language (capture, extraction, summaries, answers).
- **Language models are hybrid** (ADR-52): a local model wherever it is enough; a WP that routes a task to a cloud
  model names the measurement that justifies it, the task's data class and its local fallback, and needs track L.
  Private memory goes to a cloud model only by the project's explicit opt-in, after redaction.
- **Docs per release:** `CLAUDE.md`, arc42 (ch. 5 building blocks, ch. 6 runtime, ch. 8 concepts, ch. 9 ADRs, ch. 10
  test count), `docs/roadmap.md`, the web UI's `help.md` where a surface changes, WM §12's "Landed" note, WM §18's
  measurements, and this tracker.

## 5. Phase M — the measurement harness

**Goal:** the measurements Path C is judged by are reproducible from the repository. The replay judgments of PB §13.32
and §13.36 ran from scratch scripts that are not in the repository, and the labeled stale facts of PB §13.4 and §13.26
were never stored with it.

| WP | Tasks | Touches | Tests | Effort | Status |
|---|---|---|---|---|---|
| **M.1 — the replay harness** | Replay the real prompts of a Claude Code transcript at their own time: for each prompt, the memory as it stood (recall with `as_of` = the prompt's time minus a capture lag, `now` = the prompt's time) on a scratch copy of a graph; write each prompt's injected context, the recalled facts and their parts as JSONL. Options: `--lag`, `--since/--until`, `--limit`, `--out`. Optional blind-judge pass (shuffled facts, the judge's verdicts in a separate file) — the local judge, or a cloud judge once track L is in (L.5). Entry point: `owiki eval --replay TRANSCRIPT`. | `openwiki/replay.py` (new), `cli.py`, `sessions.py` (turns), `GraphStore.context_for(report=)` | fake graph + fake transcript, time handling, lag | M | ☑ |
| **M.2 — the guard suite** | One command that runs every guard of §4 and prints a pass / fail table: `owiki eval --guards`. | `eval.py`, `cli.py` | fakes for the runner; the live run is manual | S | ☐ |
| **M.3 — private label files** | A JSONL format for hand labels (`{"id", "label", "route", "binding", "note"}`) kept in the project, not the repository (`<project>/.openwiki/pathc/`); a loader for the measurement scripts. | `openwiki/replay.py` | format round-trip | S | ☑ |

**Landed:** M.1 in v0.122.0 — `openwiki/replay.py`, `owiki eval --replay` (snapshot, `--lag`, `--since/--until`,
`--limit` evenly spaced, `--view valid|known`, `--judge` blind and resumable, `--time-budget`; 11 tests). Checked on the
dev memory: 20 prompts in under a minute, no shown fact dated after its cut; the local judge: 29.5 % of 176 facts
helpful (ranks 1–4: 40.9 %), in line with PB §13.36. Found while checking: facts backfilled before v0.85 are dated at
their day's midnight, so the first prompts of such a day see that day's facts — `--lag 24` for those days, or C0.5
reports it.
**Exit:** the C0 baseline (C0.5) can be produced by commands in the repository.

## 6. Phase C0 — Inventory

**Goal:** know how much of the memory, and of its known staleness, a built-in sensor could observe — before any Path C
code. **Gate:** M.1. **Assumes** decision 1's recommendation (the repository first); confirm before C1.

| WP | Tasks | Output | Effort | Status |
|---|---|---|---|---|
| **C0.1 — recover the labeled stale facts** | The 14 stale facts of PB §13.4 were closed through `wiki_remember` in the v0.90 measurement, the 40 of PB §13.26 in the v0.109 review: find them in the dev graph as facts closed by an `agent-…` session's `replaces` on those days, cross-checked against the facts the two sections quote. | `<project>/.openwiki/pathc/stale-labeled.jsonl` (private) | S | ☑ |
| **C0.2 — the rubric** | For each fact: how could it be checked — a **sensor** (which built-in type of WM §6.1, which binding), a **law** (WM §7), a **person**, a **deadline**, or **not at all**. "Observable" means a built-in sensor type could read it, not that a clever script could. Written into WM §18.1 before labeling. | the rubric | S | ☑ |
| **C0.3 — label** | The 54 recovered stale facts and 200 random current facts of openwiki-dev, shuffled and labeled blind to their stale status; a second pass on 30 of them for agreement. | `<project>/.openwiki/pathc/c0-labels.jsonl` (private) | M | ☑ |
| **C0.4 — the binding plan** | Group the observable facts into bindings: sensor type, target, the fact template, the attribute groups (B9 paraphrases) each binding would govern; count how many bindings cover how many of the stale facts. | the candidate bindings, ranked by coverage | S | ☐ |
| **C0.5 — the baseline** | With M.1 on the replayed prompts: the injected facts in the attributes the candidate bindings govern, and how many were stale at the prompt's time; the **lifetime of a stale belief** for the governed attributes (from the world's change — git history for versions, test counts, defaults — to the closing of the remembered value); the **testable share** of the 200 random facts (sensor- or law-checkable). | WM §18.1: the three baselines | M | ☐ |
| **C0.6 — go / no-go** | Go if about 15 bindings would have caught at least a third of the labeled stale facts (WM §12); otherwise stop and record the negative result. | the decision in WM §18.1, the log, `docs/roadmap.md` | S | ☐ |

**C0.1 done (2026-10-25).** The labels never were in a file that survived — the scratch files of those days are
gone —, but every step is in the transcripts, so they were rebuilt from them and matched to the dev graph by
wording (a snapshot; every sheet line resolved to exactly one fact believed at the labeling time):
- **§13.4 (14 stale, 7 unclear):** `stale_labels.py` as written on 2026-09-26; the live memory closed exactly those
  14 in one op on 2026-09-27 07:48 (`remember_live.py`), with one ordinary supersession ("399 passing tests"),
  which is not a label. (The tracker's first text said they were closed in the v0.90 *measurement*; that ran on a
  copy — the live memory got the same op afterwards.)
- **§13.26 (40 stale of 352 labels):** set 1 — `vol_sheet.json`, 239 facts, 29 stale, 153 current, 35 past, 22
  unclear; set 2 — `vol_sheet2.json`, 113 facts, 11 stale, 90 current, 3 past, 9 unclear; the sheets as the tool
  results printed them, the labels from the `vol*_labels_*.txt` heredocs. All 40 are closed in the live memory: 31
  in that evening's review calls, the rest by `wiki_remember` calls of the days before (the sheets were drawn from
  a copy of 2026-09-27, so 7 had been closed already when they were labeled), and the "§" line on 2026-10-09.
- **Files** (M.3's format, private): `<project>/.openwiki/pathc/stale-labeled.jsonl` (the 54) and
  `labels-pb13.jsonl` (all 373 labels — the current, past and unclear ones too; C0.3 may reuse them, against the
  state of 2026-10-05). Each record: `id`, `label`, `route` / `binding` (empty until C0.2–C0.4), `note`, `fact`,
  `set`, `n`, `labeled_at`, `closed_at`.

**Exit (go / no-go):** as C0.6. **Release:** docs only (with M's code in v0.121).

## 7. Phase C1 — Observe, don't remember

**Goal:** observable state in memory is right by construction — half of the loop (WM §3.4.9). **Gate:** C0 go;
decisions 5 (what the loop may correct by itself — C1 closes beliefs automatically) and 13 (whether a contradicting
late belief is stored). Decision 11 can wait: bindings come from the manifest and defaults first.

| WP | Tasks | Touches | Tests | Effort | Status |
|---|---|---|---|---|---|
| **C1.1 — sensor types** | The built-in types of WM §6.1 except `opcua` and `twin`: `git` (on `handoff.git_state`), `file-pattern`, `py-constant` (`ast`, no import), `toml-key`, `count`, `ollama` (on `handoff.ollama_state`), `http-json` (localhost only), `process` (a port, a live lock file), `gpu` (`nvidia-smi`, fixed arguments), `pytest-collect` (opt-in). Each returns a reading: value, time, quality (complete / partial / failed), and the change time when the source knows it (a commit date, `git blame` on the line). | `openwiki/world/sensors.py` (new), `handoff.py` | one test per type on temporary files, a temporary git repository, a fake Ollama | M | ☐ |
| **C1.2 — environments and bindings** | `[[environments]]` in `openwiki.toml` — a name, a kind (`repository` or `twin`), a source's path, or a running twin's `url` (localhost only) and `token_env` (the variable that holds its token), with `command` only for twins OpenWiki owns (WM §2.3, ADR-54); `[[world.sensors]]` naming their environment (WM §6.2), and defaults for code sources (git state; the version file when one is found); validation — unknown type or environment, a path outside the sources, a non-localhost URL are errors; `governs` resolved to attribute keys (`store.attr_key`); `[world] enabled` (default off; on for openwiki-dev; requires `[memory] enabled`). | `openwiki/world/bindings.py` (new), `project.py` (`Project.environments`, `world_sensors`, `world_enabled`) | parsing, defaults, validation errors | S | ☐ |
| **C1.3 — observed facts** | `source = "observed"` (`memory.SOURCES`, `coerce_source`); one `Session` per sensor (`sensor:<id>`); `valid_from` from the reading's change time, else the first observation; confidence 1.0; readers show "observed" (recall lines, the Gedächtnis tab's badge, the timeline). | `graph/memory.py`, `graph/store.py`, `web/static/app.js` | source round-trip, recall rendering | S | ☐ |
| **C1.4 — the authority rule** | `temporal.plan_merge` for governed attributes (WM §3.3, §6.4): observed > agent > user > assistant > material; rules 1–5 — re-affirm, a new value closes the previous observation, contradicted current beliefs close at the observation's `valid_from` with a `SUPERSEDES` edge, an agreeing belief is re-affirmed, a later contradicting belief becomes a zero-length interval (decision 13). Discrepancies are counted and reported until `Event` exists (C2). | `graph/temporal.py`, `graph/store.py` | each rule; attributes without a sensor unchanged (the temporal set 13 / 13) | M | ☐ |
| **C1.5 — the `observe` op, written on change** | `journal.append_observe`; `fold_journal` applies it through the two-phase writer; the writers: the capture worker, `sleep`, `handoff prepare`, `owiki world observe`. Readings stay in the world cache; the graph is written only when a value changes, and `last_seen` is synced at `sleep` (WM §2.3). | `graph/journal.py`, `graph/store.py`, `cli.py` | journal round-trip, fold, two-phase | S | ☐ |
| **C1.6 — the read-time overlay** | In `context_for`: fast sensors whose cached reading is older than `every` run within about 200 ms in total (cache in `<project>/.openwiki/world-cache.json`); a recalled fact in a governed attribute whose fresh reading differs is replaced in the assembled text by the observation ("… (observed just now: pyproject.toml)"), and an `observe` op is queued (WM §6.5). | `graph/store.py`, `cli.py` (the inject hook), `openwiki/world/observe.py` (new) | overlay replaces, cache, budget, fail-soft (a failing sensor never blocks the hook) | M | ☐ |
| **C1.7 — CLI** | `owiki world status` (bindings, last readings with their age, governed attributes, discrepancies) and `owiki world observe [--sensor ID] [--dry-run]` (subcommands, ADR-13); `observe` is scheduled every few minutes between sessions with O.1 (ADR-54) and quiet unless something changed. | `cli.py` | output with a fake store | S | ☐ |
| **C1.8 — the review list** | Governed attributes leave `analyze memory --review` and the Pflege panel's "zu prüfen" list; `analyze memory` reports how many facts sensors govern. | `analysis/memory.py`, `memory_browse.py` | exclusion | S | ☐ |
| **C1.9 — attribution, light** | The observe report names the commit that changed a value (git log on the source path), else "outside any commit" — the null prediction's first half (WM §3.4.3). Persistent attribution waits for `Action` (C3). | `openwiki/world/observe.py` | a temporary repository with two commits | S | ☐ |
| **C1.10 — measure** | Replayed prompts (M.1): stale facts among the injected facts in governed attributes against C0.5's baseline; the lifetime of a stale belief; the inject hook's p95 latency; the review list's size; the guards (M.2). | WM §18.2 | — | M | ☐ |
| **C1.11 — docs and releases** | As §4. arc42: `openwiki/world/` as a building block, the observe scenario in ch. 6, an ADR for the authority rule. | docs | — | S | ☐ |
| **C1.12 — sessions get a kind** | `Session.kind` — conversation, agent, sensor, twin, import — set on every write path (capture, `wiki_remember`, imports, sensors); the Sitzungen list, session search and the handoff show conversations (WM §2.3). | `graph/store.py`, `graph/builder.py`, `memory_browse.py`, `sessions.py` | migration of existing sessions (their ids tell the kind), filters | S | ☐ |
| **C1.13 — the handoff reads the world model** | `handoff.environment` takes git state, Ollama, the wiki's freshness and the capture workers from the world model's observed state instead of its own probes; the brief at a session's start shows observed state and, from C2, open events (WM §2.3). Wiki freshness becomes a variable. | `handoff.py`, `openwiki/world/observe.py` | the brief with a fake world model | S | ☐ |

**Releases (planned):** v0.122 — environments, sensors, bindings, `owiki world status` (C1.1, C1.2, C1.7; read-only,
nothing written); v0.123 — observed facts, the authority rule, the `observe` op, session kinds (C1.3 – C1.5, C1.8,
C1.9, C1.12); v0.124 — the overlay, the handoff on the world model, and the measurement; default on for code projects
only if C1.10 holds (C1.6, C1.10, C1.11, C1.13).
**Exit:** WM §12's C1 exit — stale facts in governed attributes near zero on the replayed prompts; a stale belief in a
governed attribute lives at most one sensor period; the review list shrinks by the governed share; hook p95 + ≤ 300 ms;
the guards hold.

## 8. Phase C2 — Laws, drift and the world slice

**Goal:** rules and decisions become checkable, and the context is built around what a prompt touches. **Gate:** C1
exit; decisions 10 (tool names), 14 (how the new tables travel in COGX), 15 (a *Welt* tab or panels).

| WP | Tasks | Touches | Tests | Effort | Status |
|---|---|---|---|---|---|
| **C2.1 — schema and the authoritative registry** | `Variable`, `Law`, `Event` and their relationships (WM §5.2); always created, lazily migrated, carried by `memory export --full`. Tables declare themselves authoritative in one registry that B0's snapshot / restore reads — the memory tables move onto it too, so no hand-kept list remains (WM §2.3). | `graph/builder.py`, `graph/store.py`, `memory_export.py` | creation, migration, snapshot / restore, export | M | ☐ |
| **C2.2 — variables** | One `Variable` per binding; `DESCRIBED_IN` the page that documents it (the code file's page, a manual section). | `openwiki/world/bindings.py`, `graph/store.py` | variables from bindings | S | ☐ |
| **C2.3 — generic law checkers** | Declarative first (ADR-51): a small expression language over observed variables — equality, comparison, implication — parsed with `ast` against a whitelist of node types (never `eval`); built-in checkers for conventions ("every released version has a tag") and co-change laws (from `CO_CHANGED`). Each law needs a planted violation it fails on before it counts (WM §2.2). | `openwiki/world/laws.py` (new) | the whitelist rejects everything else; each checker on fixtures; the planted-violation rule | M | ☐ |
| **C2.4 — law extraction and review** | Candidate laws from documents: a pattern prefilter (WM §7.3), one local-model call per candidate page, variables resolved against `Variable` nodes and entities; candidates reviewed in the Pflege panel ("Gesetze prüfen": accept / reject); an accepted law gets its planted-violation fixture. | `openwiki/world/laws.py`, `web/server.py`, `web/static/app.js` | extraction with a fake chat; review endpoints | M | ☐ |
| **C2.5 — decisions with preconditions** | `wiki_remember` accepts `depends_on` (variables); the observed values of the variables a decision names are recorded with it; a change raises a drift event (WM §7.4). | `mcp_server.py`, `graph/store.py` | precondition snapshot, drift | S | ☐ |
| **C2.6 — events** | Discrepancy, violation, drift, exogenous (only when an outside change breaks a law or a decision), contested (WM §8.3); raised at fold; shown in the Pflege panel and the handoff brief's "what changed since"; resolvable by a person. | `graph/store.py`, `handoff.py`, `web/` | each kind; resolution | M | ☐ |
| **C2.7 — the world slice** | `context_for` assembles the touched variables — file paths and identifiers in the prompt, the working tree's changes, BM25 over variable labels, the recalled facts' subjects — with their state, laws, open events and the decisions about them, in at most a third of the budget; skipped when nothing touched is known (WM §9). | `graph/store.py`, `graph/memory.py` (`assemble_context`) | assembly, budget, the fallback is today's context | M | ☐ |
| **C2.8 — read-only world tools** | The contract's read verbs (WM §10.1) — `entities`, `state`, `history`, `laws` — as MCP tools (names: decision 10). | `mcp_server.py` | `MCPStdioServer.handle` with fakes | S | ☐ |
| **C2.9 — the web UI** | Variables with their values and age, laws with their status, open events — as a *Welt* tab or as panels in Gedächtnis (decision 15). | `web/` | `test_web.py` round-trips; a Playwright check | M | ☐ |
| **C2.10 — the playground's laws** | Needs P.1, P.2, P.4, P.6, P.7 and C2.13. A dedicated `ableton-playground` project (sources: the Ableton 12 manual; the Live set as `[[environments]] kind = "twin"`, reached at its running MCP server's URL — OpenWiki its client, never its host, WM §2.3, ADR-54); the `twin` sensor type reads the Ableton twin through its `state` verb; its Production Constitution as laws; planted violations (a fader above unity, a value outside its range) raise events here, and its interlocks refuse the write there. | `openwiki/world/sensors.py` (`twin`), the Ableton repository | the `twin` type against a fake contract | M | ☐ |
| **C2.11 — measure** | Planted drifts on a scratch copy (a changed constant, a desynchronized version pair, a changed GPU, an untagged release): at least 9 of 10 detected; false alarms in real use at most one a week; the world slice against today's context at equal size on replayed prompts that touch a governed variable (paired, blind judge); time to detect; the playground's planted violations. | WM §18.3 | — | M | ☐ |
| **C2.12 — docs and releases** | As §4; an ADR for declarative laws with planted-violation fixtures. | docs | — | S | ☐ |
| **C2.13 — the twin client** | An MCP client over Streamable HTTP for the `twin` sensor type and C3.9's forward to a twin's `act`: stdlib `urllib`, JSON-RPC as `mcp_server.py` speaks it; `127.0.0.1` only; the bearer token from `token_env`; timeouts and an output cap; a twin that doesn't answer is a failed reading — its beliefs go stale, `owiki doctor` (O.2) reports it. For twins OpenWiki owns, `command` starts the twin for a run and stops it after (WM §2.3, ADR-54). | `openwiki/world/twin_client.py` (new) | a fake twin on `http.server`; non-localhost URLs and a missing token refused; timeouts | M | ☐ |

**Releases (planned):** v0.125 — schema, variables, generic checkers (C2.1 – C2.3); v0.126 — law extraction and
review, decisions with preconditions, events (C2.4 – C2.6); v0.127 — the world slice, the world tools, the UI (C2.7 –
C2.9); v0.128 — the twin client, the playground's laws and the measurement (C2.10 – C2.13).
**Exit:** WM §12's C2 exit.

## 9. Phase C3 — Predict and verify

**Goal:** the full loop on the repository — the model judged by what it predicts (WM §3.4). **Gate:** C2 exit;
decisions 4 (the action log), 8 (agent predictions), 9 (where the slow loop runs).

| WP | Tasks | Touches | Tests | Effort | Status |
|---|---|---|---|---|---|
| **C3.1 — the action log** | `Action` (WM §5.2), from any agent (WM §2.3): commits from git; an MCP tool an agent reports an action with; host hooks where they exist — Claude Code's `PostToolUse` records the agent's edits and commands (files touched, a redacted command summary), installed by `claude-code --hooks` next to the other hooks; the twin's write replies (P.6). | `graph/store.py`, `claude_code_template.py` (`hooks_config`), `cli.py` (`owiki hook action`) | hook payload parsing, redaction, install / merge of the hook | M | ☐ |
| **C3.2 — predictions** | `Prediction`; when the working tree changes (seen at capture, at handoff or on request): companion files from co-change and the co-change laws, tests at risk from the test files that import the changed modules (a coverage map from one `pytest --cov` run optional); a deadline, a tolerance, a settle time each. | `openwiki/world/predict.py` (new), `graph/cochange.py` | prediction from fixtures | M | ☐ |
| **C3.3 — resolution and skill** | Resolve at the next commits and test runs (a git worktree runs only the tests at risk — idle or on request); hit / partial / miss / unresolved (WM §3.4.4); scores per basis against the basis's baseline (persistence, base rates of change). | `openwiki/world/predict.py` | each outcome; skill against a baseline | M | ☐ |
| **C3.4 — `explain`** | The ladder of WM §3.4.5 — sensor, action, another actor, the model — as a function and an MCP verb; misses and unresolved predictions as events. | `openwiki/world/explain.py` (new), `mcp_server.py` | each rung on fixtures | M | ☐ |
| **C3.5 — the slow loop** | In `sleep` (decision 9): re-score the bases, check the laws over the day's observations, resolve passed deadlines, review drift; `owiki world review` for runs on demand. | `cli.py` (`_cmd_sleep`), `openwiki/world/` | the pass with a fake store | S | ☐ |
| **C3.6 — the playground's fast loop** | Needs P.6 and C3.9. Every write through the twin predicts its effect and is resolved by the read-back within a second; a mismatch is an event. | `openwiki/world/predict.py`, the Ableton repository | against a fake contract | S | ☐ |
| **C3.7 — measure** | Online over the history (the PB §13.29 setup): companion files from co-change plus laws against co-change alone (0.51 top-10 recall of rarely-changed files); test-at-risk precision and recall; the closure rate; skill per basis; injected misses against the facts they displace (replayed, blind judge); the playground's read-back outcomes. | WM §18.4 | — | M | ☐ |
| **C3.8 — docs and releases** | As §4; an ADR for the action log. | docs | — | S | ☐ |
| **C3.9 — `act` through OpenWiki** | OpenWiki's world tools gain `act`: check the precondition (the same object as observed) and the accepted laws, record the `Action` and its `Prediction`, forward to the twin's own guarded `act` (dry run first; its interlocks stay), resolve by the read-back; agents read twins through OpenWiki's tools too (WM §2.3, §10.2). | `mcp_server.py`, `openwiki/world/` (the contract client) | refusal on a broken precondition or law; the forward and the record against a fake twin | M | ☐ |

**Releases (planned):** v0.129 — the action log (C3.1); v0.130 — predictions, resolution, skill (C3.2, C3.3); v0.131 —
`explain`, the slow loop, the playground's fast loop, the measurement (C3.4 – C3.8).
**Exit:** WM §12's C3 exit.

## 10. Phase P — The Ableton playground's prerequisites

**Where:** the Ableton Expert System's repository (`G:\Antigravity\ableton-expert-system`), committed there with the
user's go-ahead; tracked here because C2 and C3 depend on it. The recommendations are the twin review's (ABL R1 – R6).
**Safe to play with:** a dedicated test set, a limiter on the master, low monitor levels, observation only until P.1
is in (WM §11.5).

| WP | Tasks | Needed by | Effort | Status |
|---|---|---|---|---|
| **P.1 — safety in code (ABL R1)** | Bind `127.0.0.1`, CORS to the UI's origin; clamps in the injector (volume at most unity unless explicitly boosted; parameters to their range); confirmation before actions once web content is in the agent's context. | everything else | S | ☐ |
| **P.2 — stable identity (ABL R2)** | Live's object ids on every node; the expected identity sent with each write and checked by the injector before acting. | C2.10, C3.6 | M | ☐ |
| **P.3 — schema gaps (ABL R3)** | The `Chain` tables, return and master tracks, real error logging instead of `except: pass`. | C4.1 | S | ☐ |
| **P.4 — a sync you can trust (ABL R4)** | Buffer from `sync_start` to `sync_complete`, replace in one pass, counts → "complete" or "partial". | C2.10 (only a complete snapshot closes a belief) | M | ☐ |
| **P.5 — observers (ABL R5)** | The track list, the selected device's parameters, sends, solo, playing slots; reinstalled when the track list changes. | C4.1 | M | ☐ |
| **P.6 — read-back and a project id (ABL R6)** | `/live/ack <request_id> <ok> <actual_value>` after each write; the set fingerprinted by its file path. | C3.6 | S | ☐ |
| **P.7 — the twin contract** | A thin adapter over its knowledge and action servers: `entities`, `state`, `history`, `laws`, `act(dry_run=true)` (WM §10.1) — served by the one running instance over MCP's Streamable HTTP transport on `127.0.0.1`, with a bearer token and no CORS. Today's servers run over stdio, started by the agent backend, and the knowledge server holds UDP 11001 and its graph file, so OpenWiki can't start a second copy (ADR-54). | C2.10, C3.6, C4.1 | M | ☐ |

## 10a. Track L — language-model routing

**Goal:** the hybrid approach of ADR-52 — a local model wherever it is enough, a cloud model where it is really needed
— as configuration, not as code changes per task. **Gate:** decision 6 (which cloud models and providers). Parallel to
the phases; each later WP that wants a cloud model depends on L.1 – L.4.

| WP | Tasks | Touches | Tests | Effort | Status |
|---|---|---|---|---|---|
| **L.1 — a cloud backend** | A `ChatModel` (and, if ever needed, `Embedder`) implementation for the chosen provider, behind the existing protocols; keys from the environment, never stored or logged; timeouts and output caps like the local calls. | `openwiki/llm.py`, `openwiki/embeddings.py` | fakes for the HTTP layer; no network in tests | M | ☐ |
| **L.2 — per-task routing** | Named tasks — capture, coexist, resolve, extract, laws, summarize, answer, judge, lessons, probes — each routed to `local` (the default) or `cloud:<model>` in `openwiki.toml` (`[models.tasks]`), with a project-level override; `Project.model_for(task)`. | `project.py`, `cli.py` (model construction) | routing and defaults | S | ☐ |
| **L.3 — the data-class gate** | Each task declares its data class (public / private); a private task refuses a cloud route unless the project opts in (`[models] allow_private_cloud = true`); credential redaction on every cloud request; an audit line per call — task, model and version, tokens, latency, data class, no content. | `openwiki/llm.py`, `policy.py`, `metrics.py` | the gate refuses; redaction applied; the audit line | S | ☐ |
| **L.4 — fallback and cost** | When the cloud model fails or the machine is offline: the local model, or skip, and record which; the token cost per task in the metrics and in `status` (build observability). | `openwiki/llm.py`, `metrics.py`, `pipeline.py` | fallback paths | S | ☐ |
| **L.5 — the first use: a second judge** | A cloud judge for the benchmarks (LoCoMo, M.1's judge pass), measured against the local judge on the 60-judgment hand audit (PB §13.11); adopted where it agrees better with the audit. | `eval.py`, `locomo.py` | — | M | ☐ |

Later candidates, each with its own measured need: extraction from public documents (C2.4, C4.3), a large one-off
capture or re-capture, a learned dynamics model trained on dense telemetry (C4.5).

## 10b. Track O — operations

**Goal:** OpenWiki's scheduled work runs, its health is visible in one place, its timings outlive their processes, and
the memory has a backup — today for Path B, and as the loop's home between sessions for Path C (ADR-54; arc42 §7.5).
**Gate:** none; parallel to the phases. From the deployment review of 2026-10-24, which also fixed the two problems that
couldn't wait (the stale v0.45 `owiki` on the PATH, the compose file publishing the web UI on every interface).

| WP | Tasks | Touches | Tests | Effort | Status |
|---|---|---|---|---|---|
| **O.1 — `owiki schedule`** | `owiki schedule install / remove / status` for a project's scheduled tasks: `sleep` (nightly, a quiet hour) and — from C1 — `world observe` (every few minutes). Task Scheduler on Windows (`schtasks` with fixed arguments, a task name per project), cron elsewhere; pinned to the interpreter the hooks use, as `claude-code --hooks --into` pins them; `status` shows each task's next and last run and its result (O.3). | `openwiki/schedule.py` (new), `cli.py` | the rendered `schtasks` / crontab lines for both platforms; no real scheduler in tests | S | ☑ |
| **O.2 — `owiki doctor`** | One health check, pass / warn / fail per line, a non-zero exit on a failure, `--json`: the hook interpreter and its version against the `owiki` on the PATH (and a different program shadowing `openwiki`), Ollama reachable and the project's models pulled, the graph readable, queued journal writes and their age, staged approvals, the scheduled tasks present and their last result, hook-log problems, the age of the newest backup (O.4) and, from C2, each twin reachable with its token set. Reuses the handoff's probes. | `openwiki/doctor.py` (new), `handoff.py`, `cli.py` | each check against fakes | S | ☑ |
| **O.3 — the saved run log** | `<project>/.openwiki/runs.jsonl`: one line per hook, worker, scheduled run and CLI write pass — command, start, duration, outcome, model calls and tokens (the `metrics.COLLECTOR` delta, as build stages record theirs) and, with track L, the cost; rotated by size. `status`, `doctor` and the System tab read it. | `metrics.py`, `cli.py`, `web/server.py` | append, rotation, the delta | S | ☑ |
| **O.4 — rolling backups at `sleep`** | Before `sleep` writes, `memory export --full` into `<project>/.openwiki/backups/`, keeping the newest N (`[memory] backups`, default 7 — 25 MB each for the dev memory); `doctor` warns when the newest is older than two days; the restore path documented (`memory import` into an empty memory). | `cli.py` (`_cmd_sleep`), `memory_export.py` | rotation; a restore round-trip on a fake graph | S | ☑ |

**Released:** v0.121.0 — O.1 – O.4 together (`openwiki/schedule.py`, `runlog.py`, `doctor.py`; the backup step in
`sleep`; 40 new tests; the scheduler tested end to end on Windows; the restore drilled on a copy of openwiki-dev: `sleep`
wrote a 25 MB backup and forgot 15 facts (11 s); a fresh graph took 46 s, the import 54 s — all 2,609 facts back with
their ids, values and intervals, the 15 unforgotten, the 172 themes). `doctor`'s twin checks come with C2.13.
**Exit:** `owiki doctor` passes on openwiki-dev with `sleep` scheduled by O.1 and a backup younger than a day.

## 11. Phase C4 — Physical twins

**Goal:** the same model for a working twin, then for a machine, through federation (WM §11). **Gate:** C3 exit; P
complete; decisions 2 (acting), 3 (telemetry), 12 (the telemetry store); track L if the LCU 9000 manual's laws are to
be extracted, or a dynamics model trained, by a cloud model.

| WP | Tasks | Where | Effort | Status |
|---|---|---|---|---|
| **C4.1 — the contract on the Ableton twin** | All verbs, with `act` dry-run first, interlocked and approved; diagnosis on faults planted in a test set (a fader above unity, a muted track, a changed device setting). | OpenWiki + the Ableton repository | M | ☐ |
| **C4.2 — the LCU 9000 simulator** | An OPC UA server (asyncua) exposing the manual's variables, driven by a small thermal and coolant model, with injectable faults (a degrading pump, a chiller alarm, an opened door, a drifting sensor, a reflective workpiece) and a known causal graph (WM §11.2). | Cognitive Substrate | L | ☐ |
| **C4.3 — the LCU 9000 twin** | Its MCP server over the simulator (the contract's verbs), shared variable ids with OpenWiki; OpenWiki builds the manual's wiki and extracts its laws (WM §11.1). | Cognitive Substrate + OpenWiki | M | ☐ |
| **C4.4 — the telemetry store** | Raw series outside the graph (SQLite or Parquet, decision 12); regime changes, threshold crossings and alarms into the graph as observed facts and events (WM §5.3). | OpenWiki | M | ☐ |
| **C4.5 — learned dependencies** | Lagged correlation, Granger, transfer entropy with PCMCI and surrogate tests, against the simulator's true causal graph and the manual's laws (WM §11.3); where the telemetry is dense enough, a learned dynamics model as one more basis — trained locally or in the cloud (ADR-52, track L) and scored against the baselines. | OpenWiki (`[analysis]` extra) | M | ☐ |
| **C4.6 — the diagnosis benchmark** | (a) the manual's wiki only, (b) plus live state and history, (c) plus laws and learned dependencies — on both twins; exact fault labels; false alarms on fault-free runs (WM §11.4). | OpenWiki | M | ☐ |
| **C4.7 — docs and releases** | As §4. | docs | S | ☐ |

**Exit:** WM §12's C4 exit — diagnosis accuracy (c) > (b) > (a), paired and significant; learned dependencies more
precise than lagged correlation; no write outside the clamps and approval.

## 12. Decision gates

The open decisions of WM §15, and the phase that cannot start without them:

| # | Decision (WM §15) | Needed before | Recommendation |
|---|---|---|---|
| 1 | the first environment | C1 (C0 assumes the repository) | the repository for C0 – C1, the Ableton twin from C2 |
| 2 | may twins act | C4.1 (writes beyond the playground's test set) | read-only through C4 |
| 3 | telemetry: hardware or simulator | C4.2 | a simulator |
| 4 | the action log through `PostToolUse` | C3.1 | yes, from C3 |
| 5 | what the loop may correct by itself | C1.4 | facts automatically, laws and decisions by a person, no automatic remediation |
| 6 | which cloud models and providers (the hybrid approach itself is decided, ADR-52) | L.1 | choose at the first use — a second benchmark judge (L.5) |
| 7 | a small local model for per-prompt features | C2.7 (only if the world slice needs it) | measure first |
| 8 | agent predictions | C3.2 | system-made first |
| 9 | where the slow loop runs | C3.5 | inside `sleep`, plus `owiki world review` |
| 10 | names of the world tools | C2.8 | — |
| 11 | bindings proposed from memory | after C1 (manifest and defaults first) | — |
| 12 | the telemetry store | C4.4 | — |
| 13 | whether a contradicting late belief is stored | C1.4 | store it as a zero-length interval, with a discrepancy |
| 14 | how the new tables travel in COGX | C2.1 | in `--full`'s metadata |
| 15 | a *Welt* tab or panels in Gedächtnis | C2.9 | — |

## 13. Stop rules and risks

**Stop rules** — a phase ends Path C, or its default stays off, when:
- **C0:** fewer than a third of the labeled stale facts would have been caught by about 15 bindings — record the
  negative result (WM §18.1), keep Path B as it is.
- **C1:** stale facts in governed attributes don't fall clearly below C0's baseline, or the hook's latency budget breaks
  — ship the read-only `owiki world status`, keep the overlay and the authority rule off by default.
- **C2:** more than one false alarm a week in real use, or the world slice loses to today's context — keep laws and
  events as a review list, the slice off.
- **C3:** predictions without skill over their baselines — keep the action log for attribution, drop the predictions.
- **C4:** diagnosis not better with the twin's state and laws than with the manual alone — keep the twin as a data
  source, record the result.

**Risks to watch:**
- **The engine.** Kuzu is archived (R10); if the LadybugDB move (`docs/ladybugdb-move.md`) comes first, the new tables
  follow its constraints — schema work in C2.1 is the point to check.
- **Hook latency.** Sensors in the inject hook must stay within about 200 ms and fail soft (C1.6).
- **False alarms** teach people to ignore events — the C2 exit's limit.
- **Cross-repository work** (P, C4.2, C4.3) depends on the sibling projects' state and on commits made there.
- **Scope.** Each phase ships on its own and can end the path; nothing in C2 – C4 is built before C1's exit holds.

## 14. Log

| Date | Phase / WP | Change | Result |
|---|---|---|---|
| 2026-10-24 | — | Roadmap created from WM §12, the plan (WM §2.1 – §2.2) and the open decisions (WM §15) | — |
| 2026-10-24 | L | Hybrid language models decided (ADR-52): track L added; decision 6 narrowed to the choice of providers | — |
| 2026-10-24 | C1, C2, C3 | The project concept checked against Path C (WM §2.3, ADR-53): environments as a third input class; work packages C1.2 and C1.5 widened, C1.12, C1.13 and C3.9 added, C2.1, C2.10 and C3.1 widened | — |
| 2026-10-24 | — | Deployment reviewed against Path C; two fixes landed at once: the global `owiki` reinstalled as an editable pipx install of the checkout (the old v0.45 one, older than B0, removed), and `docker-compose.yml` publishing on `127.0.0.1` only (`1b12eff`) | — |
| 2026-10-25 | C0 | C0.3 done (WM §18.1): a fresh subagent (A, blind), C0.1's labeler (B) and a second fresh run on 30 (A2) labeled the shuffled sheet of 54 stale + 200 random facts. Agreement A–B 83.5 % (κ 0.77), A–A2 80 % (κ 0.71). Testable share 43.5 % (A) / 38.5 % (B). Stale facts sensor-checkable: 24 / 21 / 18 on the strictest reading, 18 reached with 9 / 7 / 9 bindings — the go threshold holds, only just on the strictest reading | C0.3 ☑ |
| 2026-10-25 | C0 | C0.2 done: the rubric in WM §18.1, before any label — five routes found in a fixed order (none → sensor → law → deadline → person), the strict reading of "observable", a binding string per fact (`type:target[:selector]`), the protocol (one shuffled sheet, a labeler who hasn't seen the 54, a second pass on 30, per-group reports) and an expectation written beforehand | C0.2 ☑ |
| 2026-10-25 | C0, M | C0.1 done: the 14 + 40 labeled stale facts (and all 373 labels) rebuilt from the transcripts, matched to the dev graph's ids, written to `.openwiki/pathc/` in M.3's format; M.3 built (`replay.read_labels` / `write_labels`, 2 tests) | C0.1, M.3 ☑ |
| 2026-10-25 | M | M.1 built (v0.122.0): `owiki eval --replay` — prompts replayed at their own time on a graph snapshot, a blind judge pass; checked on 20 real prompts (no fact after its cut; judge 29.5 % helpful, ranks 1–4 40.9 %) | M.1 ☑ |
| 2026-10-25 | O | Track O built and released as v0.121.0: `owiki schedule` (Task Scheduler XML / cron, tested end to end against the real scheduler), the saved run log (`.openwiki/runs.jsonl`, `status`, the System tab), rolling backups at `sleep` (restore tested), `owiki doctor`; M and the phases move up one release | O.1 – O.4 ☑ |
| 2026-10-25 | O, C1, C2, P | Deployment decided (WM §2.3, ADR-54, arc42 §7.5): the loop between sessions as a scheduled `owiki world observe`; twins as running services reached by URL; the world tier native. Track O added (O.1 – O.4), C2.13 added, C1.2, C1.7, C2.10 and P.7 widened | — |
