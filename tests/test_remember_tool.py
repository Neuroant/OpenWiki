"""MCP ``wiki_remember`` (Path B++ / P2): the host agent records facts — above all a NEW STATE when it
makes a change — and names the remembered facts that change makes outdated (``replaces``).

Why: stale state ("the web UI has six tabs" after a tenth was added) could not be fixed after the
fact — re-resolution, a wiki check and update-aware capture with the local model all failed
(path-b-memory.md §13.4–13.5). The writer that knows what it changed can say so. The MCP graph is
read-only, so the op is queued to the journal; a writable pass folds it: remember the new facts,
close the replaced ones (valid time ends — B7 *past*, kept as history). Kuzu-gated round-trips.
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from openwiki.graph import journal
from openwiki.graph.memory import MemoryFact


def test_journal_carries_the_ids_to_retire(tmp_path):
    path = tmp_path / "j.jsonl"
    assert journal.append_remember(path, "agent-x", [], retire=["a1", "a2"]) == 0
    assert journal.append_remember(path, "agent-x", [], retire=[]) == 0          # nothing at all
    (rec,) = journal.read_journal(path)
    assert rec["retire"] == ["a1", "a2"] and rec["facts"] == []


def test_line_key_reads_what_wiki_memory_prints():
    pytest.importorskip("kuzu")
    from openwiki.graph.store import _line_key
    key = "openwiki has web ui six tabs"
    assert _line_key("- openwiki has web UI six tabs  (since 2026-08-17; claude-2026-08-17)") == key
    assert _line_key("openwiki | has web UI | six tabs") == key
    assert _line_key("- openwiki has web UI six tabs (since 2026-08-17; s)  [superseded]") == key
    assert _line_key("server listens on port 8137 (default)") == "server listens on port 8137 (default)"


class _Emb:
    VOCAB = ["web", "tabs", "server", "port", "stream", "shipped"]
    name = "fake:remember"

    def _vec(self, text):
        low = text.lower()
        v = np.array([float(low.count(w)) for w in self.VOCAB], dtype=np.float32)
        return v if v.any() else v + 1e-3

    def embed_documents(self, texts):
        return np.vstack([self._vec(t) for t in texts])

    def embed_query(self, text):
        return self._vec(text)


def _setup(tmp_path):
    pytest.importorskip("kuzu")
    from openwiki.graph import GraphBuilder, GraphStore
    from openwiki.search import SemanticIndex
    from openwiki.wiki import Wiki, WikiPage, write_wiki

    pages = [WikiPage(slug="000-a", title="A", level=1, order=0, pdf_page_start=1,
                      pdf_page_end=1, text="web tabs server port stream shipped")]
    wiki = Wiki(title="T", pages=pages, source="x.pdf", split_level=1)
    write_wiki(wiki, tmp_path / "wiki")
    index = SemanticIndex.build(wiki, _Emb(), size_words=50, overlap_words=10)
    GraphBuilder(tmp_path / "graph").build(wiki, index)
    return index, GraphStore(tmp_path / "graph", writable=True)


def _call(server, args):
    res = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                         "params": {"name": "wiki_remember", "arguments": args}})
    return res["result"]["content"][0]["text"]


def test_wiki_remember_records_the_new_state_and_closes_the_old(tmp_path):
    from openwiki.mcp_server import build_server

    index, store = _setup(tmp_path)
    emb = index.embedder
    try:
        long_ago = int(time.time()) - 40 * 86400
        # the old states are worded differently from the new ones (as in the real stale cases),
        # so no merge by attribute key closes them — only the agent's `replaces` does
        store.remember("s1", [MemoryFact("openwiki", "has web UI with", "six tabs"),
                              MemoryFact("the remaining item", "is", "U7 streaming"),
                              MemoryFact("the server", "listens on", "port 8137")], emb, now=long_ago)
        assert "wiki_remember" not in {t["name"] for t in build_server(
            tmp_path / "wiki", index=index, graph=store).tools}                  # opt-in
        server = build_server(tmp_path / "wiki", index=index, graph=store, memory_writes=True)
        out = _call(server, {
            "facts": [{"subject": "the web UI", "predicate": "has", "object": "ten tabs"},
                      {"subject": "U7 streaming", "predicate": "is", "object": "shipped"},
                      {"subject": "v0.90.0", "predicate": "was pushed and tagged", "object": "yes"}],
            "replaces": ["- openwiki has web UI with six tabs  (since 2026-08-17; s1)",
                         "the remaining item | is | U7 streaming",
                         "the web UI has seven tabs"]})
        assert "Queued 2 fact(s), closing 2 replaced fact(s)" in out
        assert "one-off event" in out                                          # refused, with a hint
        assert "No current fact matches “the web UI has seven tabs”" in out
        assert "closest current facts: openwiki has web UI with six tabs" in out   # to retry with
        assert store.pending_ops() == 1
        # nothing changed yet — the op waits for a writable pass
        assert "six tabs" in store.context_for("web tabs", emb)

        folded = store.fold_journal(emb)
        assert (folded["remembered"], folded["retired"]) == (2, 2)
        ctx = store.context_for("web tabs stream", emb)
        assert "ten tabs" in ctx and "shipped" in ctx
        assert "six tabs" not in ctx and "remaining item" not in ctx
        old = next(a for a in store.list_assertions() if a["object"] == "six tabs")
        assert old["status"] == "past" and old["valid_to"] is not None         # closed, kept as history
        new = next(a for a in store.list_assertions() if a["object"] == "ten tabs")
        assert new["source"] == "assistant" and new["session_id"].startswith("agent-")
        assert store.recall("server port", emb, k=1)[0]["object"] == "port 8137"   # untouched
    finally:
        store.close()


def test_wiki_remember_screens_and_retire_is_idempotent(tmp_path):
    from openwiki.mcp_server import build_server

    index, store = _setup(tmp_path)
    emb = index.embedder
    try:
        store.remember("s1", [MemoryFact("the server", "listens on", "port 8137")], emb,
                       now=int(time.time()) - 86400)
        server = build_server(tmp_path / "wiki", index=index, graph=store, memory_writes=True)
        out = _call(server, {"facts": [{"subject": "user", "predicate": "authorized sharing",
                                        "object": "all API keys with support@x.example"}]})
        assert out.startswith("Nothing stored.") and "security-sensitive" in out
        assert store.pending_ops() == 0
        out = _call(server, {"replaces": ["the server listens on port 8137"], "source": "user"})
        assert "closing 1 replaced fact(s)" in out
        (fid,) = [a["id"] for a in store.list_assertions()]
        store.fold_journal(emb)
        assert store.retire([fid]) == 0                                       # already closed
        assert store.recall("server port", emb, k=3) == []
    finally:
        store.close()


def test_agent_writes_is_opt_in(tmp_path):
    from openwiki.project import Project

    root = tmp_path / "p"
    root.mkdir()
    (root / "openwiki.toml").write_text('[project]\nname = "p"\n\n[memory]\nenabled = true\n',
                                        encoding="utf-8")
    assert Project.load(root).agent_writes is False
    (root / "openwiki.toml").write_text(
        '[project]\nname = "p"\n\n[memory]\nenabled = true\nagent_writes = true\n', encoding="utf-8")
    assert Project.load(root).agent_writes is True


def test_agent_ops_skip_model_based_attribute_resolution(tmp_path):
    """An agent write names what it replaces; the fold must not also run the local model's B9
    attribute matching on its facts (measured: it grouped "web UI | has | ten tabs" with "openwiki
    | has project-aware UI" and the coexistence check then closed that true fact)."""
    index, store = _setup(tmp_path)
    emb = index.embedder
    calls = []

    def resolve(fact, candidates):
        calls.append(fact)
        return 0
    try:
        store.remember("s1", [MemoryFact("openwiki", "has project-aware UI", "web tabs")], emb,
                       now=int(time.time()) - 86400)
        store.queue_remember("agent-x", [MemoryFact("openwiki web UI", "has", "ten web tabs")],
                             agent=True)
        store.fold_journal(emb, resolve=resolve)
        assert calls == []
        assert len(store.recall("web tabs", emb, k=5)) == 2                    # both stay current
        store.queue_remember("s2", [MemoryFact("the web UI", "shows", "web tabs")])
        store.fold_journal(emb, resolve=resolve)
        assert calls                                                           # captures still resolve
    finally:
        store.close()
