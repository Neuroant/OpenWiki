# Using OpenWiki from coding agents (MCP)

OpenWiki exposes its **RAG + GraphRAG** as an [MCP](https://modelcontextprotocol.io)
server, so any MCP-capable coding agent — **Claude Code**, **OpenCode**, Cursor,
Zed, … — can query the wiki as tools. The agent gets grounded, cited answers and
graph traversal over your document, instead of guessing.

The server is `openwiki mcp`: a dependency-free stdio (newline-delimited
JSON-RPC 2.0) server, in the same spirit as the rest of the project.

## Quickest setup — scaffold it per project

If your wiki is an OpenWiki **project** (an `openwiki.toml` folder), let the CLI write
the config for you, from inside the project:

```bash
owiki claude-code     # → .mcp.json + .claude/ (commands + skill)
owiki opencode        # → opencode.json + .opencode/ (agent + commands)
```

Both wire the MCP as **`owiki mcp` with project discovery**: the agent launches the
server *from the project folder*, so it resolves that project's wiki/index/graph from
the manifest — no absolute paths, and it can't drift to another project's corpus.
Everything below explains the same wiring by hand (and for non-project wikis).

## The MCP server

```bash
openwiki mcp --wiki output/wiki --index output/index --graph output/graph
```

It advertises these **read-only** tools (each appears only when its backing
artifact exists):

| Tool | Needs | Purpose |
| --- | --- | --- |
| `wiki_ask` | index + graph + Ollama | Grounded, **cited** answer (RAG, graph-augmented) — best for what/how/why |
| `wiki_global` | graph + communities + Ollama | Thematic answer over the **whole** corpus (community summaries) — best for "main themes / how do X and Y relate" |
| `wiki_search` | index | Semantic search → ranked page excerpts |
| `wiki_read_page` | wiki | Full Markdown of a page (by slug) |
| `wiki_list_pages` | wiki | Every page slug + title |
| `wiki_graph_neighbors` | graph | A page's related pages (hierarchy, refs, similar, shared concepts) |
| `wiki_find_path` | graph | Shortest relationship chain between two pages |
| `wiki_find_entity` | graph `--entities` | Pages that mention a named concept |

Flags: `--model` (chat model for `wiki_ask`/`wiki_global`), `--host` (Ollama URL),
`--no-ask` (disable `wiki_ask` — and `wiki_global`, which also needs a chat model —
when you don't want one loaded).

**Prerequisites**

1. Build the artifacts once (see the main README): `ingest` → `build-wiki` →
   `index` → `graph-build` (add `--entities` for `wiki_find_entity`).
2. **Ollama** running with the models pulled (`bge-m3` for search/ask,
   `qwen3:30b-a3b-instruct-2507-q4_K_M` for `wiki_ask`).

**Paths matter.** The coding agent launches the server with *its own* working
directory, so pass **absolute paths** to `--wiki/--index/--graph` and point the
command at the **venv's Python** — then it works from anywhere. Below, replace
`<OPENWIKI>` with the absolute path to your checkout, and use the right Python:

- Windows: `<OPENWIKI>\.venv\Scripts\python.exe`
- macOS/Linux: `<OPENWIKI>/.venv/bin/python`

**Smoke-test it** without any agent (pipe JSON-RPC to stdin):

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | openwiki mcp --no-ask
```

You should see an `initialize` result and a `tools/list` with the tools above.

---

## Claude Code

### 1. Register the MCP server

For a **project**, just run `owiki claude-code` (above) — it writes the `.mcp.json`
below. To do it by hand, add it to the project's **`.mcp.json`**
(see `examples/coding-agents/claude-code/.mcp.json`):

```json
{
  "mcpServers": {
    "openwiki": {
      "command": "owiki",
      "args": ["mcp"]
    }
  }
}
```

`owiki mcp` (no path flags) discovers the project from the folder Claude Code runs it
in. No global `owiki`? Use the venv Python and explicit paths instead:
`"command": "<OPENWIKI>/.venv/bin/python", "args": ["-m","openwiki","mcp","--wiki","<OPENWIKI>/output/wiki", …]`.

…or from the CLI:

```bash
claude mcp add openwiki --scope project -- owiki mcp
```

Claude will now call `wiki_ask`, `wiki_search`, etc. on its own when relevant.

### 2. A slash command

`.claude/commands/wiki-ask.md` (see `examples/coding-agents/claude-code/commands/`):

```markdown
---
description: Ask the OpenWiki knowledge base (RAG + GraphRAG)
argument-hint: <question>
---
Use the `openwiki` MCP server's `wiki_ask` tool to answer the following, and cite
the wiki pages it returns. If the answer isn't in the wiki, say so.

$ARGUMENTS
```

Then: `/wiki-ask Wie richte ich Smooth Sound Transitions ein?`

### 3. A skill (auto-applied)

`.claude/skills/openwiki/SKILL.md` (see `examples/coding-agents/claude-code/skills/`)
tells Claude *when* to consult the wiki, so you don't have to invoke it manually:

```markdown
---
name: openwiki
description: Consult the OpenWiki knowledge base (the manual this wiki was built
  from) via its MCP tools for grounded, cited answers. Use whenever the user asks
  about the device/domain the wiki covers instead of answering from memory.
---
Use the `openwiki` MCP tools rather than guessing:
- `wiki_ask` first for what/how/why questions (returns a cited answer).
- `wiki_global` for broad, thematic questions about the whole corpus.
- `wiki_search` to find pages, `wiki_read_page` to read one.
- `wiki_graph_neighbors` / `wiki_find_path` / `wiki_find_entity` to explore
  relationships. Always cite the page slugs you used.
```

### 4. Learn OpenWiki: the tutorial & help commands

Two more commands teach OpenWiki *itself* (copy from
`examples/coding-agents/claude-code/commands/`):

- **`/openwiki-tutorial [module]`** — an interactive, module-by-module walkthrough
  of every feature (ingest → wiki → index → semantic search → RAG → knowledge
  graph). It detects which artifacts exist, demos the live `wiki_*` tools, and
  gives "your turn" exercises. Omit the argument to start at the beginning, pass a
  module number/name to jump, or `all` to run straight through.
- **`/openwiki-help [question]`** — a quick command cheat-sheet and one-off
  "how do I …" answers.

### 5. Agent memory hooks (Second Brain — optional)

If the project runs in **Second Brain mode** (`[memory] enabled` in `openwiki.toml`), you can wire
OpenWiki's cross-session memory into Claude Code's session lifecycle:

```bash
owiki claude-code --hooks     # merges memory hooks into .claude/settings.json
```

This adds four hooks (`owiki hook` reads the event JSON on stdin, and is **fail-soft** — it never
blocks a prompt):

- **`UserPromptSubmit` → `owiki hook inject`** — assembles the three-tier memory context
  (identity + recalled facts + relevant themes) for your prompt and injects it, so the agent starts
  each turn already oriented. Chore prompts get none — "push", "commit and push", "push and tag v1.2", slash commands,
  and a bare "ok" / "continue" once the session is under way (`[memory] skip_prompts` adds your own, `skip_chores =
  false` turns the gate off). Each fact is injected once until the next compaction or `/clear` — the agent still has
  it in context — so later prompts get only what is new (`repeat_facts = true` repeats them). Also available on demand
  as `owiki context "<query>"` and the `wiki_memory` MCP tool.
- **`SessionEnd` / `PreCompact` → `owiki hook capture`** — captures the conversation transcript into
  memory (`remember`), so what you discussed persists into the next session.
- **`SessionStart` → `owiki hook resume`** — on a new session (and after `/clear`) injects the brief of
  the last **session handoff**: its next steps and what changed since (below).

**Let the agent write memory (opt-in).** With `agent_writes = true` under `[memory]`, the MCP server also offers
**`wiki_remember`**: the agent records facts — above all the **new state** when it changes something ("the web UI
has ten tabs") — and lists in `replaces` the remembered facts that change makes outdated, copied as `wiki_memory`
shows them. They are queued and land within seconds — the MCP server starts a background fold (at the latest at the
next write pass, if the graph is busy): the new facts are
remembered, the replaced ones closed (kept as history). This is how stale memory gets fixed — the local model can't
reliably infer it afterwards (`docs/path-b-memory.md` §13.4–13.6). A line such as *"When you change something the
memory describes, record the new state with wiki_remember and list the outdated facts in `replaces`"* in your
`CLAUDE.md` makes the agent use it.

**Search earlier sessions.** In a Second Brain project the MCP server also offers **`wiki_sessions`**: full-text
search over the transcripts of earlier sessions — every user and assistant turn, as capture sees it — returning dated,
verbatim excerpts. Where `wiki_memory` holds the decision, the session holds the detail: the exact error, the number a
run produced, the reason something was dropped. It covers the sessions the hooks have captured and the repository's
own Claude Code folder when its hooks are bound to the project; `[memory] transcripts` adds more (files or folders).
Credentials are redacted, and sentences the memory policy flags are withheld. On the shell: `owiki sessions search
"…"` and `owiki sessions list`.

**Hand a session over to the next one.** At the end of a long session, `/session-restart prepare` (the skill
`owiki claude-code` installs) has the agent look at what OpenWiki derives (`wiki_handoff` with `mode: "preview"`:
the repository, the memory, the environment), record decisions with `wiki_remember`, write a short note — Next (start
here), Summary, Decisions, Open threads, Ready-to-use prompts — and save the handoff (`wiki_handoff` with `mode:
"prepare"`, or `owiki handoff prepare --note FILE`). The next session gets the brief from the `SessionStart` hook —
Next, what changed since (commits, facts learned or closed, Ollama / graph / capture state) and the memory for the
first Next task — or asks for it with `/session-restart resume` (`owiki handoff resume`). The handoff lives in the
project's `handoff/` folder; the note passes the same memory policy as captured facts, since it is injected into later
sessions.

Memory only accumulates if you also **capture + consolidate** (`owiki remember` / `owiki consolidate`,
or let the capture hook do it) and the project has a built graph. In Wiki mode (the default) the hooks
are no-ops. See `docs/path-b-memory.md` for the full agent-memory design.

---

## OpenCode

### 1. Register the MCP server

For a **project**, `owiki opencode` (above) writes this (plus the `openwiki` agent).
By hand, add it to **`opencode.json`** (project root); see
`examples/coding-agents/opencode/opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "openwiki": {
      "type": "local",
      "command": ["owiki", "mcp"],
      "enabled": true
    }
  }
}
```

`owiki mcp` discovers the project from the folder OpenCode runs it in. No global
`owiki`? Use `["<OPENWIKI>/.venv/bin/python", "-m", "openwiki", "mcp", "--wiki", …]`
with explicit absolute paths instead. The `wiki_*` tools are then available to
OpenCode's agents automatically.

### 2. A command

`.opencode/command/wiki-ask.md` (see `examples/coding-agents/opencode/`):

```markdown
---
description: Ask the OpenWiki knowledge base (RAG + GraphRAG)
---
Use the openwiki `wiki_ask` tool to answer the following and cite the pages it
returns:

$ARGUMENTS
```

Then: `/wiki-ask …`

The same **`/openwiki-tutorial`** and **`/openwiki-help`** commands ship for
OpenCode too (`examples/coding-agents/opencode/command/`); their frontmatter sets
`agent: openwiki` so they run under the wiki-scoped agent.

### Note on "skills"

Agent **Skills** are a Claude-Code concept. OpenCode has no direct equivalent —
its MCP tools are available to every agent by default. To nudge the model toward
using them, add guidance to your **`AGENTS.md`** (OpenCode reads it as project
rules), e.g. *"For questions about the NAUTILUS, use the openwiki MCP tools
(`wiki_ask` first) and cite the pages."*

---

## Notes

- The tools are **read-only** — coding agents consult the wiki, they don't edit
  it. (Editing is what `openwiki chat`/`serve` are for.)
- One `openwiki mcp` process is started per agent connection and loads the index
  + graph read-only, so several agents can point at the same wiki at once.
- `wiki_ask` runs the full graph-augmented RAG pipeline locally (Ollama); the
  other tools are fast and don't need a chat model (`--no-ask` skips loading one).
