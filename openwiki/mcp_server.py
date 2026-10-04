"""A tiny, dependency-free MCP server exposing OpenWiki's RAG + GraphRAG.

Speaks the Model Context Protocol over **stdio** (newline-delimited JSON-RPC 2.0)
so coding agents — Claude Code, OpenCode, Cursor, … — can query the wiki as tools.
Hand-rolled with the standard library only, in the same spirit as the web server.

Tools (all read-only; advertised only when their backing artifact is present):
  wiki_ask            grounded, cited answer over the wiki (RAG, graph-augmented)
  wiki_global         thematic answer over the whole corpus (community summaries)
  wiki_search         semantic search -> ranked page excerpts
  wiki_read_page      full Markdown of a page
  wiki_list_pages     every page slug + title
  wiki_graph_neighbors a page's related pages (hierarchy, refs, similar, concepts)
  wiki_find_path      shortest relationship chain between two pages
  wiki_find_entity    pages that mention a named concept
  wiki_memory         assemble cross-session memory for a query (Path B, Second Brain)

Run via ``openwiki mcp`` (see cli.py). stdout carries the protocol — everything
else (logs, errors) must go to stderr.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Callable, Optional

PROTOCOL_VERSION = "2024-11-05"


def _utf8(stream, errors=None):
    """``stream`` switched to UTF-8 (a no-op for streams that can't be reconfigured)."""
    try:
        if errors:
            stream.reconfigure(encoding="utf-8", errors=errors)
        else:
            stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    return stream


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
        # JSON-RPC over stdio is UTF-8; on Windows a pipe defaults to the locale's code page, which
        # garbled every non-ASCII argument ("Lautstärke" → "LautstÃ¤rke") until v0.96.1
        stdin = stdin or _utf8(sys.stdin, errors="replace")
        stdout = stdout or _utf8(sys.stdout)
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

def _remember(graph, index, a: dict, on_queued=None) -> str:
    """``wiki_remember``: screen the agent's facts (P0 security policy; one-off events are
    refused with a hint to record the resulting state), resolve ``replaces`` against the
    believed facts *now* (exact lines only — near misses come back with suggestions), and
    queue one journal op; ``on_queued`` (the CLI's fold worker) folds it within seconds —
    remember + close the replaced — else the next writable pass does."""
    import time as _time
    from .graph.memory import MemoryFact, is_ephemeral, is_secret_only, is_unsafe_instruction, redact_fact
    from .graph.temporal import parse_date

    now = int(_time.time())
    source = "user" if str(a.get("source") or "").lower() == "user" else "assistant"
    facts, notes = [], []
    for f in a.get("facts") or []:
        f = f if isinstance(f, dict) else {}
        s, p, o = (str(f.get(k) or "").strip() for k in ("subject", "predicate", "object"))
        if not (s and p and o):
            notes.append("skipped a fact without subject, predicate and object")
            continue
        vf = parse_date(f.get("valid_from")) if f.get("valid_from") else None
        fact, kinds = redact_fact(MemoryFact(s, p, o, valid_from=vf if vf is not None else now, source=source))
        text = f"{fact.subject} {fact.predicate} {fact.object}"
        if kinds and is_secret_only(fact):
            notes.append(f"not stored — a credential, which memory never keeps: {text}")
        elif is_unsafe_instruction(fact):
            notes.append(f"not stored — security-sensitive (restate it in the session instead): {text}")
        elif is_ephemeral(fact):
            notes.append(f"not stored — a one-off event; record the resulting state instead: {text}")
        else:
            if kinds:
                notes.append(f"a credential was redacted before storing: {text}")
            facts.append(fact)
    matched, unmatched = graph.match_facts(a.get("replaces") or [])
    retire = [i for ids in matched.values() for i in ids]
    out = []
    if facts or retire:
        session = "agent-" + _time.strftime("%Y-%m-%d", _time.gmtime(now))
        graph.queue_remember(session, facts, session_date=now, retire=retire, agent=True)
        folding = False
        if on_queued is not None:
            try:
                folding = on_queued() is not False
            except Exception:
                folding = False
        out.append(f"Queued {len(facts)} fact(s)"
                   + (f", closing {len(retire)} replaced fact(s)" if retire else "")
                   + (" — they land in memory within a minute (a background fold)." if folding else
                      " — they land in memory at the next write pass (session end or `openwiki sleep`)."))
        out += [f"  + {f.subject} {f.predicate} {f.object}" for f in facts]
        out += [f"  − {line}" for line in matched]
    else:
        out.append("Nothing stored.")
    for line in unmatched:
        hint = ""
        if index is not None:
            try:
                near = graph.recall(line, index.embedder, k=3)
                if near:
                    hint = "; closest current facts: " + " | ".join(
                        f"{h['subject']} {h['predicate']} {h['object']}" for h in near)
            except Exception:
                hint = ""
        out.append(f"No current fact matches “{line}” — nothing closed{hint}")
    return "\n".join(out + notes)


def _tool(name, description, properties, required):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties, "required": required}}


def build_server(wiki_dir, index=None, graph=None, agent=None, name="openwiki",
                 version="0", identity="", context_budget=None, context_k: int = 16,
                 memory_probes: bool = False, memory_writes: bool = False, memory_lexical: float = 0.0,
                 memory_temporal: float = 0.0,
                 handoff: Optional[Callable] = None,
                 on_remember: Optional[Callable] = None) -> MCPStdioServer:
    """Assemble the MCP server from already-loaded OpenWiki components.

    `index` (SemanticIndex) enables search/ask; `graph` (GraphStore) enables the
    graph tools; `agent` (RAGAgent) powers `wiki_ask`. Read-only `WikiTools` back
    the rest. `identity` + `context_budget` + `context_k` seed/bound the B6 `wiki_memory` context;
    `memory_probes` (P1 cue-trigger, needs the agent's chat model) probes it for the
    user's implicit constraints. `memory_writes` (``[memory] agent_writes``) adds
    `wiki_remember` — the agent records facts / new states, queued to the journal.
    `handoff` (``handoff(mode, note, repo) -> str``, from the CLI) adds `wiki_handoff` — the
    session handoff (resume / preview / prepare) for the project the server belongs to.
    `on_remember` (from the CLI) is called after `wiki_remember` queued a write — it starts the
    fold worker, so the write lands within seconds.
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

        # B6 agent memory: assemble this session's memory context (identity + recalled
        # facts + relevant themes). Needs the embedder (index) + a non-empty memory tier.
        if index is not None and _graph_has_memory(graph):
            from .graph.temporal import parse_date   # a graph is present → the graph package loads
            specs.append(_tool(
                "wiki_memory",
                "Assemble what you remember relevant to a query/topic across sessions "
                "(Path B): your identity, the most relevant remembered facts, and the "
                "consolidated themes. Call this at the start of a session to load memory. "
                "Facts carry their validity dates; pass as_of (YYYY-MM-DD) to load the memory "
                "as it was true at that date.",
                {"query": {"type": "string"},
                 "as_of": {"type": "string", "description": "Optional ISO date (point-in-time)."}},
                ["query"]))
            probe_chat = getattr(agent, "chat", None) if memory_probes else None

            def _wiki_memory(a):
                query = str(a["query"])
                probes = None
                if probe_chat is not None:
                    from .graph.memory import constraint_probes
                    probes = constraint_probes(probe_chat, query)     # fail-soft → []
                return (graph.context_for(query, index.embedder, identity=identity, k=context_k,
                                          lexical=memory_lexical, temporal=memory_temporal,
                                          max_chars=context_budget, as_of=parse_date(a.get("as_of")),
                                          probes=probes)
                        or "(no relevant memory yet)")
            handlers["wiki_memory"] = _wiki_memory

        # Agent-initiated writes (opt-in): record a decision or a NEW STATE when the agent makes a
        # change, closing the facts it replaces — the local model can't infer staleness afterwards
        # (path-b-memory.md §13.4–13.6). Read-only graph → queued to the journal, folded later.
        if memory_writes:
            specs.append(_tool(
                "wiki_remember",
                "Record facts in your long-term memory NOW — decisions, conventions, and above all "
                "a NEW STATE whenever you change something (\"the web UI has ten tabs\", \"U7 "
                "streaming chat is shipped\"), so later sessions don't act on the old one. Each fact "
                "is a subject / predicate / object triple. Put the remembered facts your change makes "
                "outdated in `replaces`, copied as wiki_memory shows them — they are closed (kept as "
                "history), not deleted. Record the resulting state, not the event (\"v1.2 was "
                "pushed\" is not kept). Facts land in memory within a minute (a background fold), at the "
                "latest at the next write pass.",
                {"facts": {"type": "array", "description": "Facts to remember.", "items": {
                    "type": "object", "properties": {
                        "subject": {"type": "string"}, "predicate": {"type": "string"},
                        "object": {"type": "string"},
                        "valid_from": {"type": "string",
                                       "description": "Optional ISO date it became true (default: now)."}},
                    "required": ["subject", "predicate", "object"]}},
                 "replaces": {"type": "array", "items": {"type": "string"},
                              "description": "Remembered facts this makes outdated, as wiki_memory "
                                             "prints them."},
                 "source": {"type": "string", "enum": ["assistant", "user"],
                            "description": "Who established it: you (default) or the user's decision."}},
                []))
            handlers["wiki_remember"] = lambda a: _remember(graph, index, a, on_queued=on_remember)

        # Global search needs a chat model (from the agent) + community summaries.
        if agent is not None and _graph_has_communities(graph):
            specs.append(_tool(
                "wiki_global",
                "Answer a high-level, thematic question about the WHOLE corpus from its "
                "topical community summaries (global search). Prefer this for 'what are the "
                "main themes' / 'how do X and Y relate across the corpus' questions; use "
                "wiki_ask for a specific fact on a page.", {"question": {"type": "string"}}, ["question"]))
            handlers["wiki_global"] = lambda a: _global_answer(agent.chat, graph, str(a["question"]))

    # Session handoff (the CLI wires it when the server belongs to a project): the previous session's
    # Next steps + what changed since, or write one for the next session.
    if handoff is not None:
        specs.append(_tool(
            "wiki_handoff",
            "Session handoff between coding sessions. mode 'resume' (default): the previous session's "
            "handoff — its Next steps, summary, decisions, open threads and ready-to-use prompts — and "
            "what changed since (commits, memory, environment). mode 'preview': what a handoff written "
            "now would record (repository, memory, environment, the memory for the next task) — look "
            "before writing. mode 'prepare': write the handoff for the next session; pass `note`, "
            "Markdown with '## Next (start here)', '## Summary', '## Decisions', '## Open threads', "
            "'## Ready-to-use prompts'.",
            {"mode": {"type": "string", "enum": ["resume", "preview", "prepare"]},
             "note": {"type": "string", "description": "prepare / preview: the handoff note (Markdown)."},
             "repo": {"type": "string",
                      "description": "The session's working directory (default: the server's)."}},
            []))
        handlers["wiki_handoff"] = lambda a: handoff(str(a.get("mode") or "resume"), a.get("note"),
                                                     a.get("repo"))

    def call_tool(tool_name: str, args: dict) -> str:
        handler = handlers.get(tool_name)
        if handler is None:
            raise ValueError(f"unknown tool '{tool_name}'")
        return handler(args)

    return MCPStdioServer(name, version, specs, call_tool)


def _graph_has_communities(graph) -> bool:
    try:
        return bool(graph.has_communities())
    except Exception:
        return False


def _graph_has_memory(graph) -> bool:
    try:
        return bool(graph.has_memory())
    except Exception:
        return False


def _global_answer(chat, graph, question: str) -> str:
    """Global search for MCP: a thematic answer from the community summaries, with the
    cited communities listed (mirrors the CLI ``ask --global`` / web ``ask_global``)."""
    from .graph.community import answer_global

    comms = graph.communities()
    if not comms:
        return "No communities in the graph. Run `openwiki communities` first."
    answer = answer_global(chat, question, [(c["label"], c["summary"]) for c in comms])
    cited = {int(m) for m in re.findall(r"\[(\d+)\]", answer)}
    lines = [answer, "", "Communities (* = cited):"]
    for i, c in enumerate(comms, 1):
        lines.append(f" {'*' if i in cited else ' '}[{i}] {c['label']}  ({c['size']} pages)")
    return "\n".join(lines).strip()


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
