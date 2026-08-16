# Coding-agent examples

Config, commands, and a skill for using OpenWiki's **RAG + GraphRAG** from coding
agents via the `openwiki mcp` server. Full guide: [`../../docs/coding-agents.md`](../../docs/coding-agents.md).

**The easy way — scaffold it per project.** From inside an OpenWiki project run:

```
owiki claude-code     # writes .mcp.json + .claude/ (commands + skill)
owiki opencode        # writes opencode.json + .opencode/ (agent + commands)
```

Both wire the MCP as `owiki mcp` with **project discovery** (the agent runs it from
the project folder), so there are no hardcoded paths and the tools always point at
*that* project's wiki. Build the artifacts first (`owiki build`) and have Ollama
running. The files below are the same setup, for reference / manual copying — the
`.mcp.json` / `opencode.json` use `owiki mcp` (install it globally with
`install-openwiki.ps1` / `.sh`, or swap in `<repo>/.venv/bin/python -m openwiki`).

```
claude-code/
  .mcp.json                       # register the MCP server (or: claude mcp add …)
  commands/openwiki-tutorial.md   # /openwiki-tutorial [module]  — guided, hands-on tour of every feature
  commands/openwiki-help.md       # /openwiki-help [question]    — quick cheat-sheet / usage answers
  commands/wiki-ask.md            # /wiki-ask <question>
  commands/wiki-explore.md        # /wiki-explore <topic>        — graph traversal
  skills/openwiki/SKILL.md        # auto-applied: when to consult the wiki
opencode/
  opencode.json                   # register the MCP server (mcp.openwiki)
  command/openwiki-tutorial.md    # /openwiki-tutorial [module]
  command/openwiki-help.md        # /openwiki-help [question]
  command/wiki-ask.md             # /wiki-ask <question>
```

New to OpenWiki? Run **`/openwiki-tutorial`** — an interactive, module-by-module
walkthrough that teaches every feature (ingest → wiki → index → semantic search →
RAG → knowledge graph), demoing the live `wiki_*` tools as it goes. Use
**`/openwiki-help`** for a quick command cheat-sheet or one-off "how do I …" answers.

Copy these into your target project (Claude Code reads `.mcp.json` / `.claude/…`
from the project; OpenCode reads `opencode.json` / `.opencode/command/…`).
