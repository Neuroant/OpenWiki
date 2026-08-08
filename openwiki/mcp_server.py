"""A tiny, dependency-free MCP server exposing OpenWiki's RAG + GraphRAG.

Speaks the Model Context Protocol over **stdio** (newline-delimited JSON-RPC 2.0)
so coding agents — Claude Code, OpenCode, Cursor, … — can query the wiki as tools.
Hand-rolled with the standard library only, in the same spirit as the web server.

Tools (all read-only; advertised only when their backing artifact is present):
  wiki_ask            grounded, cited answer over the wiki (RAG, graph-augmented)
  wiki_search         semantic search -> ranked page excerpts
  wiki_read_page      full Markdown of a page
  wiki_list_pages     every page slug + title
  wiki_graph_neighbors a page's related pages (hierarchy, refs, similar, concepts)
  wiki_find_path      shortest relationship chain between two pages
  wiki_find_entity    pages that mention a named concept

Run via ``openwiki mcp`` (see cli.py). stdout carries the protocol — everything
else (logs, errors) must go to stderr.
"""

from __future__ import annotations

import json
import sys
from typing import Callable, Optional

PROTOCOL_VERSION = "2024-11-05"


class MCPStdioServer:
    """Minimal MCP server: `initialize`, `tools/list`, `tools/call`, `ping`."""

    def __init__(self, name: str, version: str,
                 tools: list[dict], call_tool: Callable[[str, dict], str]) -> None:
        self.name = name
        self.version = version
        self.tools = tools
        self.call_tool = call_tool

    # -- JSON-RPC dispatch (pure; unit-testable without stdio) ----------

    def handle(self, msg: dict) -> Optional[dict]:
        """Return a JSON-RPC response, or None for notifications."""
        mid = msg.get("id")
        method = msg.get("method")
        if method is None:
            return None
        if method == "initialize":
            client_ver = (msg.get("params") or {}).get("protocolVersion")
            return self._ok(mid, {
                "protocolVersion": client_ver or PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": self.name, "version": self.version},
            })
        if method in ("notifications/initialized", "initialized"):
            return None  # notification — no reply
        if method == "ping":
            return self._ok(mid, {})
        if method == "tools/list":
            return self._ok(mid, {"tools": self.tools})
        if method == "tools/call":
            params = msg.get("params") or {}
            name = params.get("name", "")
            args = params.get("arguments") or {}
            try:
                text = self.call_tool(name, args)
                is_error = False
            except Exception as exc:  # surface tool errors as content, not transport errors
                text, is_error = f"Error: {exc}", True
            return self._ok(mid, {"content": [{"type": "text", "text": text}], "isError": is_error})
        if mid is None:
            return None  # unknown notification — ignore
        return {"jsonrpc": "2.0", "id": mid,
                "error": {"code": -32601, "message": f"Method not found: {method}"}}

    @staticmethod
    def _ok(mid, result) -> dict:
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    # -- stdio transport ------------------------------------------------

    def serve(self, stdin=None, stdout=None) -> None:
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        print(f"openwiki MCP server ready ({len(self.tools)} tools) — stdio",
              file=sys.stderr, flush=True)
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            response = self.handle(msg)
            if response is not None:
                stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                stdout.flush()


# ---------------------------------------------------------------------------
# Build the OpenWiki toolset over the existing library.
# ---------------------------------------------------------------------------

def _tool(name, description, properties, required):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties, "required": required}}


def build_server(wiki_dir, index=None, graph=None, agent=None, name="openwiki",
                 version="0") -> MCPStdioServer:
    """Assemble the MCP server from already-loaded OpenWiki components.

    `index` (SemanticIndex) enables search/ask; `graph` (GraphStore) enables the
    graph tools; `agent` (RAGAgent) powers `wiki_ask`. Read-only `WikiTools` back
    the rest.
    """
    from .tools import WikiTools

    tools_impl = WikiTools(wiki_dir, index=index, graph=graph)
    slug = {"type": "string", "description": "A page slug, e.g. '025-smooth-sound-transitions-sst'."}

    specs: list[dict] = []
    handlers: dict[str, Callable[[dict], str]] = {}

    if agent is not None:
        specs.append(_tool(
            "wiki_ask",
            "Answer a question grounded in the wiki (RAG, graph-augmented). Returns a "
            "cited answer with the source pages. Prefer this for 'what/how/why' questions.",
            {"question": {"type": "string"}}, ["question"]))
        handlers["wiki_ask"] = lambda a: _format_answer(agent.answer(str(a["question"])))

    if index is not None:
        specs.append(_tool(
            "wiki_search",
            "Semantic search over the wiki; returns the most relevant page excerpts.",
            {"query": {"type": "string"}, "k": {"type": "integer", "description": "Max results (default 5)."}},
            ["query"]))
        handlers["wiki_search"] = lambda a: _format_search(index.search(str(a["query"]), int(a.get("k", 5))))

    specs.append(_tool("wiki_read_page", "Return the full Markdown of a wiki page.", {"slug": slug}, ["slug"]))
    handlers["wiki_read_page"] = lambda a: tools_impl.read_page(str(a["slug"]))

    specs.append(_tool("wiki_list_pages", "List every wiki page (slug — title).", {}, []))
    handlers["wiki_list_pages"] = lambda a: tools_impl.list_pages()

    if graph is not None:
        specs.append(_tool(
            "wiki_graph_neighbors",
            "A page's related pages in the knowledge graph (hierarchy, reading order, "
            "cross-references, similar pages, shared concepts).", {"slug": slug}, ["slug"]))
        handlers["wiki_graph_neighbors"] = lambda a: tools_impl.graph_neighbors(str(a["slug"]))

        specs.append(_tool(
            "wiki_find_path",
            "Shortest relationship chain between two pages — how two topics connect.",
            {"from_slug": slug, "to_slug": slug}, ["from_slug", "to_slug"]))
        handlers["wiki_find_path"] = lambda a: tools_impl.find_path(str(a["from_slug"]), str(a["to_slug"]))

        if tools_impl._graph_has_entities():
            specs.append(_tool(
                "wiki_find_entity",
                "Find every page that mentions a named concept (Mode, Effect, Feature, "
                "Parameter, …).", {"name": {"type": "string"}}, ["name"]))
            handlers["wiki_find_entity"] = lambda a: tools_impl.find_entity(str(a["name"]))

    def call_tool(tool_name: str, args: dict) -> str:
        handler = handlers.get(tool_name)
        if handler is None:
            raise ValueError(f"unknown tool '{tool_name}'")
        return handler(args)

    return MCPStdioServer(name, version, specs, call_tool)


def _format_answer(ans) -> str:
    lines = [ans.answer, ""]
    if ans.sources:
        cited = ans.cited_markers()
        lines.append("Sources:")
        for s in ans.sources:
            mark = "*" if s.marker in cited else " "
            rel = "+" if s.kind == "related" else " "
            lines.append(f" {mark}{rel}[{s.marker}] {s.page_title}  ·  pages/{s.page_slug}.md")
    return "\n".join(lines).strip()


def _format_search(results) -> str:
    if not results:
        return "No results."
    return "\n".join(
        f"[{r.score:.3f}] {r.page_slug} — {r.page_title} "
        f"(PDF p.{r.pdf_page_start}-{r.pdf_page_end}): "
        f"{(' '.join(r.text.split()))[:200]}"
        for r in results
    )
