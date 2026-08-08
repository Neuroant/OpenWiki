# Coding-agent examples

Config, commands, and a skill for using OpenWiki's **RAG + GraphRAG** from coding
agents via the `openwiki mcp` server. Full guide: [`../../docs/coding-agents.md`](../../docs/coding-agents.md).

Replace `<OPENWIKI>` with the absolute path to your checkout, and use the venv's
Python (`.venv/bin/python`, or `.venv\Scripts\python.exe` on Windows). Build the
artifacts first (`ingest` → `build-wiki` → `index` → `graph-build`) and have
Ollama running.

```
claude-code/
  .mcp.json                     # register the MCP server (or: claude mcp add …)
  commands/wiki-ask.md          # /wiki-ask <question>
  commands/wiki-explore.md      # /wiki-explore <topic>  (graph traversal)
  skills/openwiki/SKILL.md      # auto-applied: when to consult the wiki
opencode/
  opencode.json                 # register the MCP server (mcp.openwiki)
  command/wiki-ask.md           # /wiki-ask <question>
```

Copy these into your target project (Claude Code reads `.mcp.json` / `.claude/…`
from the project; OpenCode reads `opencode.json` / `.opencode/command/…`).
