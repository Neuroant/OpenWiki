---
name: session-restart
description: >
  Hand a working session over to the next one, or pick up where the last one stopped. Use
  "/session-restart prepare" before ending or clearing a long session, and "/session-restart resume"
  at the start of a new one (the SessionStart hook usually injects the brief already). OpenWiki
  derives the repository, memory and environment state; wiki_remember keeps the decisions.
argument-hint: prepare | resume
---

# Session restart — prepare | resume

Mode: **$ARGUMENTS** — if empty: `resume` when this session hasn't started any work yet, else ask.

Tools: the `openwiki` MCP server's **`wiki_handoff`** (modes `preview`, `prepare`, `resume`). Without it,
the same from a shell in the repository root: `owiki handoff prepare --dry-run`,
`owiki handoff prepare --note FILE`, `owiki handoff resume`.

## prepare — at the end of a session

1. **Look at what OpenWiki derives:** `wiki_handoff` with `mode: "preview"` (shell: `handoff prepare
   --dry-run`) — the repository (branch, HEAD, uncommitted files, commits this session), the memory (facts
   learned this session, facts closed, writes still queued, turns not yet captured), the environment, and
   the memory the next session will see for the current "Next" task.
2. **Record decisions and new states in long-term memory** with `wiki_remember`: one subject / predicate /
   object fact per decision or changed state, `source: "user"` for what the user decided. Put remembered
   facts that are now outdated (in the preview or the injected memory) in `replaces`, copied exactly.
   Record the state, not the event: "v1.4.0 is the current release", not "v1.4.0 was pushed".
3. **Write the note** — short, for a reader who starts cold:
   - `## Next (start here)` — numbered; the first item is the task to start with, as an instruction.
   - `## Summary` — what this session did, 3–8 bullets, with versions / commits where they matter.
   - `## Decisions` — what was decided, and why.
   - `## Open threads` — unfinished work, open questions, anything waiting on the user.
   - `## Ready-to-use prompts` — 2–3 prompts the user can paste into the next session.

   Keep credentials, standing permissions and instructions that weaken security out of the note: it is
   injected into later sessions, and the memory policy drops such lines.
4. **Write the handoff:** `wiki_handoff` with `mode: "prepare"` and the note (shell: save the note to a
   file, then `handoff prepare --note FILE`). It writes `HANDOFF.md` (+ an archived copy and
   `handoff.json`) to the OpenWiki project's `handoff/` folder and starts capturing this session's
   remaining turns in the background.
5. **Report back** in a few lines: where the handoff is, HEAD and anything uncommitted or unpushed (offer
   to commit or push — do it only when the user says so), and the ready-to-use prompts.

## resume — at the start of a session

1. Use the "Session handoff" block injected at session start, if there is one; otherwise call
   `wiki_handoff` (`mode: "resume"`; shell: `handoff resume`). Read the `HANDOFF.md` it names when you
   need the full write-up.
2. **Check it before trusting it:** new commits or uncommitted files since the handoff mean parts of
   "Next" may be done; Ollama not reachable, a graph that isn't readable or hook-log problems need
   attention first; a capture worker still running means the last session's facts are still landing.
3. **Summarize in about five lines:** where things stand, what changed since the handoff, what needs
   attention.
4. **Propose the first "Next" task** and wait for the user's go-ahead.
