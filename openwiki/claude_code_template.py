"""Scaffold a ready-to-run Claude Code configuration into an OpenWiki project.

Writes a project-scoped ``.mcp.json`` (registers *this* project's wiki as the
``openwiki`` MCP server) plus a few slash commands and an auto-applied skill under
``.claude/``. Like the OpenCode scaffolder, the MCP is wired as ``owiki mcp`` with
**project discovery** (Claude Code runs the server from the project folder), so the
config carries no hardcoded paths and can't drift to another project's corpus.

Pure string/JSON rendering + guarded file writes; the CLI picks the MCP command
and the model names (only used in the help cheat-sheet text). Parallels
``opencode_template.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

CLAUDE_CODE_FILES = (
    ".mcp.json",
    ".claude/commands/wiki-ask.md",
    ".claude/commands/wiki-explore.md",
    ".claude/commands/openwiki-help.md",
    ".claude/skills/openwiki/SKILL.md",
)


def _mcp_json(command: str, args: list) -> str:
    config = {"mcpServers": {"openwiki": {"command": command, "args": list(args)}}}
    return json.dumps(config, indent=2, ensure_ascii=False) + "\n"


_WIKI_ASK = """\
---
description: Ask the OpenWiki knowledge base (RAG + GraphRAG)
argument-hint: <question>
---
Use the `openwiki` MCP server's `wiki_ask` tool to answer the following, and cite
the wiki pages it returns. If the answer isn't in the wiki, say so plainly instead
of guessing — do not fall back on prior knowledge of some other product.

$ARGUMENTS
"""

_WIKI_EXPLORE = """\
---
description: Explore how a topic connects in the OpenWiki knowledge graph
argument-hint: <page slug or topic>
---
Explore the OpenWiki knowledge graph around: $ARGUMENTS

1. If needed, use `wiki_search` to find the most relevant page slug.
2. Use `wiki_graph_neighbors` on that slug to list related pages (hierarchy,
   cross-references, similar pages, shared concepts).
3. Optionally use `wiki_find_entity` to find every page mentioning a concept, or
   `wiki_find_path` to explain how two pages connect.

Summarise the relationships and cite the page slugs.
"""


def _help_md(chat_model: str, embed_model: str) -> str:
    return f"""\
---
description: Quick OpenWiki help — commands, options, or a "how do I …" usage answer
argument-hint: [question]
---
Answer the user's OpenWiki **usage** question concisely: **$ARGUMENTS** (if empty,
print the cheat-sheet below verbatim and stop). If the question is about the wiki's
*content* rather than how to use OpenWiki, use the `openwiki` MCP tools instead —
`wiki_ask` for a grounded, cited answer.

### Cheat-sheet (`owiki …`; Ollama running with `{embed_model}` + `{chat_model}`)
Project-aware (run inside the project folder):
- `status` — sources, settings, per-stage build state.
- `build` — run the whole pipeline (ingest → wiki → index → graph), incremental. `--force`, `--only STAGES`.
- `serve --port 8137` — web UI (Projekt / Wiki / Graph / Tutorial / Hilfe).
- `ask "question"` — RAG + citations; GraphRAG when a graph exists.
- `ontology` — propose a domain entity-type ontology (review, then `--write`).

MCP tools (this wiki): `wiki_ask`, `wiki_search`, `wiki_read_page`, `wiki_list_pages`,
`wiki_graph_neighbors`, `wiki_find_path`, `wiki_find_entity`. Full docs: `README.md`,
`CLAUDE.md`, `docs/coding-agents.md`, or the web UI **Hilfe** tab.
"""


_SKILL = """\
---
name: openwiki
description: >
  Consult this project's OpenWiki knowledge base via its MCP tools for grounded,
  cited answers. Use whenever the user asks about the domain the wiki covers — its
  concepts, structure, or how to accomplish a task — instead of answering from
  memory or prior knowledge of some other product.
---

# OpenWiki knowledge base

When a question concerns the material this wiki was built from, use the `openwiki`
MCP tools rather than guessing:

- **`wiki_ask`** — start here for "what / how / why" questions. It returns a
  grounded answer with citations (RAG, graph-augmented). If it says the answer
  isn't in the wiki, relay that instead of inventing one.
- **`wiki_search`** — find relevant pages by meaning; returns page slugs.
- **`wiki_read_page`** — read a page's full Markdown (pass a slug).
- **`wiki_graph_neighbors`** / **`wiki_find_path`** / **`wiki_find_entity`** —
  explore relationships: a page's related pages, how two topics connect, and every
  page that mentions a named concept.

Always cite the page slugs the tools return so the user can verify. For how to use
or build OpenWiki itself, see the **`/openwiki-help`** command.
"""


def render_files(chat_model: str, embed_model: str, mcp_command: list) -> dict:
    """Return ``{relative_path: content}`` for the Claude Code setup."""
    command, *args = list(mcp_command)
    return {
        ".mcp.json": _mcp_json(command, args),
        ".claude/commands/wiki-ask.md": _WIKI_ASK,
        ".claude/commands/wiki-explore.md": _WIKI_EXPLORE,
        ".claude/commands/openwiki-help.md": _help_md(chat_model, embed_model),
        ".claude/skills/openwiki/SKILL.md": _SKILL,
    }


def scaffold_claude_code(root, *, chat_model: str, embed_model: str,
                         mcp_command: list, force: bool = False) -> tuple[list, list]:
    """Write the Claude Code config into ``root``. Existing files are left untouched
    unless ``force``. Returns ``(written, skipped)`` as lists of ``Path``."""
    root = Path(root)
    written: list = []
    skipped: list = []
    for rel, content in render_files(chat_model, embed_model, mcp_command).items():
        target = root / rel
        if target.exists() and not force:
            skipped.append(target)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(target)
    return written, skipped
