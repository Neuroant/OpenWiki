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
import re
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
the wiki pages it returns. For a broad, thematic question ("main themes", "how do X
and Y relate across the whole corpus"), use `wiki_global` instead. If the answer
isn't in the wiki, say so plainly instead of guessing — do not fall back on prior
knowledge of some other product.

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

MCP tools (this wiki): `wiki_ask`, `wiki_global`, `wiki_search`, `wiki_read_page`,
`wiki_list_pages`, `wiki_graph_neighbors`, `wiki_find_path`, `wiki_find_entity`. Full docs: `README.md`,
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
- **`wiki_global`** — for high-level, *thematic* questions about the whole corpus
  ("what are the main themes", "how do X and Y relate across the wiki"); answers
  from topical community summaries rather than a few pages.
- **`wiki_search`** — find relevant pages by meaning; returns page slugs.
- **`wiki_read_page`** — read a page's full Markdown (pass a slug).
- **`wiki_graph_neighbors`** / **`wiki_find_path`** / **`wiki_find_entity`** —
  explore relationships: a page's related pages, how two topics connect, and every
  page that mentions a named concept.

Always cite the page slugs the tools return so the user can verify. For how to use
or build OpenWiki itself, see the **`/openwiki-help`** command.
"""


def _text_of(content) -> str:
    """Plain text of a Claude message ``content`` — a string, or a list of blocks
    (keep ``{"type": "text"}`` blocks; skip tool_use/tool_result)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block.strip())
            elif isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                parts.append(str(block["text"]).strip())
        return " ".join(p for p in parts if p).strip()
    return ""


# Host-injected blocks inside user messages — system reminders (they carry e.g. CLAUDE.md), slash
# command echoes, local command output. Not the user's words: stripped before capture, else they'd
# be remembered as "facts".
_HOST_BLOCK = re.compile(
    r"<(system-reminder|command-name|command-message|command-args|local-command-stdout|"
    r"local-command-stderr|local-command-caveat)>.*?</\1>", re.DOTALL)


def _clean(body: str) -> str:
    return _HOST_BLOCK.sub("", body or "").strip()


def iter_claude_turns(text: str):
    """Yield ``(timestamp, "User: …" / "Assistant: …")`` per text-bearing message of a Claude
    Code transcript (JSONL). Lenient: skips unparseable lines, non-text content (tool
    calls/results), host-injected blocks (system reminders, command echoes) and compaction
    summaries (they restate earlier turns). ``timestamp`` is the line's ISO string or ``""``."""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        # isCompactSummary restates earlier turns; isMeta marks host-expanded skill / slash-command
        # bodies ("Please analyze this codebase…") — instructions *to* the model, not the user's words
        if not isinstance(obj, dict) or obj.get("isCompactSummary") or obj.get("isMeta"):
            continue
        msg = obj.get("message") if isinstance(obj.get("message"), dict) else obj
        role = str(msg.get("role") or obj.get("type") or "").lower()
        if role not in ("user", "assistant"):
            continue
        body = _clean(_text_of(msg.get("content")))
        if body:
            yield str(obj.get("timestamp") or ""), f"{role.capitalize()}: {body}"


def parse_claude_transcript(text: str, max_chars: int = 20000) -> str:
    """Convert a Claude Code transcript (JSONL — one message per line) into a plain
    ``User: … / Assistant: …`` transcript for memory capture (B6 host hook) — see
    :func:`iter_claude_turns` for what is skipped; returns the **last** ``max_chars`` (the
    most recent conversation, bounded)."""
    transcript = "\n\n".join(turn for _, turn in iter_claude_turns(text)).strip()
    return transcript[-max_chars:] if len(transcript) > max_chars else transcript


def split_transcripts_by_window(texts, max_chars: int = 20000) -> list:
    """B7 backfill: Claude Code transcripts (JSONL texts) → ``[(YYYY-MM-DD, [(start, window), …]),
    …]``, oldest day first — every turn grouped by the UTC day of its timestamp (turns without one
    are dropped: they can't be dated), each day cut into windows of at most ``max_chars`` at turn
    boundaries (an over-long single turn is truncated), so each window is one capture call.
    ``start`` is the window's first turn's ISO timestamp — its facts are valid from *then*, so a
    change later the same day orders after it instead of colliding at midnight."""
    dated = sorted((ts, turn) for text in texts for ts, turn in iter_claude_turns(text)
                   if len(ts) >= 10)
    days: dict = {}
    for ts, turn in dated:
        days.setdefault(ts[:10], []).append((ts, turn[:max_chars]))
    out = []
    for day in sorted(days):
        windows, cur, start = [], "", ""
        for ts, turn in days[day]:
            if cur and len(cur) + 2 + len(turn) > max_chars:
                windows.append((start, cur))
                cur, start = turn, ts
            else:
                cur, start = (f"{cur}\n\n{turn}", start) if cur else (turn, ts)
        if cur:
            windows.append((start, cur))
        out.append((day, windows))
    return out


def split_transcripts_by_day(texts, max_chars: int = 20000) -> list:
    """:func:`split_transcripts_by_window` without the start timestamps: ``[(day, [window, …])]``."""
    return [(day, [text for _, text in windows])
            for day, windows in split_transcripts_by_window(texts, max_chars)]


def install_hooks(settings_file, inject_command: str, capture_command: str) -> Path:
    """Merge the memory hooks into a Claude Code settings file (created if absent; other
    settings preserved). Returns the path written."""
    settings_file = Path(settings_file)
    existing: dict = {}
    if settings_file.is_file():
        try:
            existing = json.loads(settings_file.read_text(encoding="utf-8"))
        except ValueError:
            existing = {}
    merged = merge_hooks(existing, inject_command, capture_command)
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return settings_file


def hooks_config(inject_command: str, capture_command: str) -> dict:
    """The Claude Code ``hooks`` block wiring OpenWiki memory into the session lifecycle
    (B6): inject recalled context on every prompt, capture the session on end/compaction.
    Capture is best-effort; inject must be fast + fail-soft (it never exits non-zero)."""
    return {
        "UserPromptSubmit": [
            {"hooks": [{"type": "command", "command": inject_command, "timeout": 30}]}],
        "SessionEnd": [
            {"hooks": [{"type": "command", "command": capture_command, "timeout": 120}]}],
        "PreCompact": [
            {"hooks": [{"type": "command", "command": capture_command, "timeout": 120}]}],
    }


def merge_hooks(existing: dict, inject_command: str, capture_command: str) -> dict:
    """Merge the OpenWiki memory hooks into an existing ``.claude/settings.json`` dict,
    preserving other settings and other hook events (our three events are overwritten)."""
    settings = dict(existing) if isinstance(existing, dict) else {}
    hooks = dict(settings.get("hooks") or {}) if isinstance(settings.get("hooks"), dict) else {}
    hooks.update(hooks_config(inject_command, capture_command))
    settings["hooks"] = hooks
    return settings


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


def scaffold_claude_code(root, *, chat_model: str, embed_model: str, mcp_command: list,
                         force: bool = False, inject_command: str = "",
                         capture_command: str = "") -> tuple[list, list]:
    """Write the Claude Code config into ``root``. Existing files are left untouched
    unless ``force``. When ``inject_command``/``capture_command`` are given, also **merge**
    the B6 memory hooks into ``.claude/settings.json`` (preserving other settings — always
    applied, so a re-run refreshes the commands). Returns ``(written, skipped)``."""
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
    if inject_command and capture_command:
        written.append(install_hooks(root / ".claude" / "settings.json", inject_command, capture_command))
    return written, skipped
