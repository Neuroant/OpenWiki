# Coding-agent examples

Config, commands, and a skill for using OpenWiki's **RAG + GraphRAG** from coding
agents via the `openwiki mcp` server. Full guide: [`../../docs/coding-agents.md`](../../docs/coding-agents.md).

Replace `<OPENWIKI>` with the absolute path to your checkout, and use the venv's
Python (`.venv/bin/python`, or `.venv\Scripts\python.exe` on Windows). Build the
artifacts first (`ingest` → `build-wiki` → `index` → `graph-build`) and have
Ollama running.

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
