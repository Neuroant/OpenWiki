"""Tests for the stdio MCP server (protocol dispatch + tool wiring), offline."""

from __future__ import annotations

import io
import json

import numpy as np

from openwiki.mcp_server import MCPStdioServer, build_server
from openwiki.search import SemanticIndex
from openwiki.wiki import Wiki, WikiPage, write_wiki


def _echo_call(name, args):
    if name != "echo":
        raise ValueError(f"unknown tool '{name}'")
    return str(args.get("x", ""))


def _echo_server() -> MCPStdioServer:
    tools = [{"name": "echo", "description": "echoes x",
              "inputSchema": {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}}]
    return MCPStdioServer("t", "9", tools, _echo_call)


# -- JSON-RPC protocol ------------------------------------------------------

def test_initialize_echoes_version_and_serverinfo():
    r = _echo_server().handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}})
    assert r["result"]["protocolVersion"] == "2024-11-05"
    assert r["result"]["serverInfo"] == {"name": "t", "version": "9"}
    assert "tools" in r["result"]["capabilities"]


def test_tools_list():
    r = _echo_server().handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert [t["name"] for t in r["result"]["tools"]] == ["echo"]


def test_tools_call_ok():
    r = _echo_server().handle(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "echo", "arguments": {"x": "hi"}}})
    assert r["result"]["content"][0] == {"type": "text", "text": "hi"}
    assert r["result"]["isError"] is False


def test_tools_call_unknown_is_error_not_transport_failure():
    r = _echo_server().handle(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "nope", "arguments": {}}})
    assert r["result"]["isError"] is True and "unknown tool" in r["result"]["content"][0]["text"]


def test_initialized_notification_gets_no_reply():
    assert _echo_server().handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_unknown_method_returns_error():
    r = _echo_server().handle({"jsonrpc": "2.0", "id": 5, "method": "foo/bar"})
    assert r["error"]["code"] == -32601


def test_serve_stdio_roundtrip():
    inp = "\n".join(json.dumps(m) for m in [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},   # no reply
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "echo", "arguments": {"x": "hey"}}},
    ]) + "\n"
    out = io.StringIO()
    _echo_server().serve(stdin=io.StringIO(inp), stdout=out)
    responses = [json.loads(l) for l in out.getvalue().splitlines() if l.strip()]
    assert [r["id"] for r in responses] == [1, 2, 3]           # notification produced no line
    assert responses[2]["result"]["content"][0]["text"] == "hey"


# -- OpenWiki toolset -------------------------------------------------------

class _FakeEmbedder:
    VOCAB = ["alpha", "beta", "gamma"]
    name = "fake"

    def _v(self, t):
        v = np.array([float(t.lower().count(w)) for w in self.VOCAB], dtype=np.float32)
        return v if v.any() else v + 1e-6

    def embed_documents(self, ts):
        return np.vstack([self._v(t) for t in ts])

    def embed_query(self, t):
        return self._v(t)


def test_build_server_advertises_tools_by_availability(tmp_path):
    pages = [WikiPage(slug="000-a", title="Alpha", level=1, order=0,
                      pdf_page_start=1, pdf_page_end=1, text="alpha beta gamma")]
    wiki = Wiki(title="T", pages=pages, source="x.pdf", split_level=1)
    wdir = tmp_path / "wiki"
    write_wiki(wiki, wdir)
    index = SemanticIndex.build(wiki, _FakeEmbedder(), size_words=50, overlap_words=10)

    server = build_server(wdir, index=index, graph=None, agent=None)
    names = {t["name"] for t in server.tools}
    assert {"wiki_search", "wiki_read_page", "wiki_list_pages"} <= names
    assert "wiki_ask" not in names               # no agent
    assert "wiki_graph_neighbors" not in names   # no graph

    listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": "wiki_list_pages", "arguments": {}}})
    assert "000-a" in listed["result"]["content"][0]["text"]
    found = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                           "params": {"name": "wiki_search", "arguments": {"query": "alpha"}}})
    assert "000-a" in found["result"]["content"][0]["text"]


def test_wiki_global_gated_on_communities_and_agent(tmp_path):
    import types

    import pytest
    pytest.importorskip("kuzu")
    from openwiki.graph import GraphBuilder, GraphStore, detect_communities

    pages = [WikiPage(slug=f"00{i}-p", title=t, level=1, order=i, pdf_page_start=i + 1,
                      pdf_page_end=i + 1, text=txt)
             for i, (t, txt) in enumerate(
                 [("Alpha", "alpha beta"), ("Beta", "beta gamma"), ("Gamma", "alpha gamma")])]
    wiki = Wiki(title="T", pages=pages, source="x.pdf", split_level=1)
    wdir = tmp_path / "wiki"
    write_wiki(wiki, wdir)
    index = SemanticIndex.build(wiki, _FakeEmbedder(), size_words=50, overlap_words=10)
    GraphBuilder(tmp_path / "graph", similar_k=3).build(wiki, index)

    class _Chat:
        name = "fake"

        def chat(self, messages):
            return "Überblick [1]."

    store = GraphStore(tmp_path / "graph", writable=True)
    try:
        agent = types.SimpleNamespace(chat=_Chat())
        # no communities yet → wiki_global not advertised even with an agent
        names0 = {t["name"] for t in build_server(wdir, index=index, graph=store, agent=agent).tools}
        assert "wiki_global" not in names0

        pg = store.page_graph()
        assignment = detect_communities(pg["edges"], list(pg["pages"]))
        cids = set(assignment.values())
        store.upsert_communities(assignment, {c: f"summary {c}" for c in cids},
                                 {c: f"Thema {c}" for c in cids})

        # with communities + an agent → advertised, and callable
        server = build_server(wdir, index=index, graph=store, agent=agent)
        assert "wiki_global" in {t["name"] for t in server.tools}
        # ...but not without an agent (no chat model)
        assert "wiki_global" not in {t["name"] for t in build_server(wdir, index=index, graph=store, agent=None).tools}

        r = server.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                           "params": {"name": "wiki_global", "arguments": {"question": "Themen?"}}})
        text = r["result"]["content"][0]["text"]
        assert r["result"]["isError"] is False
        assert "[1]" in text and "* = cited" in text   # answer + cited-community list
    finally:
        store.close()
