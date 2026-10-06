"""The approval step (v0.110, ``[memory] approve_writes``): the agent's ``wiki_remember`` writes are staged next to
the journal until a person approves (they land — valid from when they were staged, recorded when approved) or rejects
them (an audit log, never applied). Pure staging offline; the MCP / fold round trip Kuzu-gated."""

from __future__ import annotations

import json
import time

import numpy as np
import pytest

from openwiki.graph import journal
from openwiki.graph.memory import MemoryFact


def test_staging_approve_and_reject(tmp_path):
    staged, jpath, log = tmp_path / "g.staged.jsonl", tmp_path / "g.journal.jsonl", tmp_path / "g.rejected.jsonl"
    n1, a = journal.stage_remember(staged, "agent-1", [MemoryFact("web UI", "has", "ten tabs")], now=1000,
                                   session_date=1000, retire=["old-1"])
    n2, b = journal.stage_remember(staged, "agent-1", [("server", "listens on", "port 9000")], now=2000)
    assert (n1, n2) == (1, 1) and a != b and len(a) == 8
    assert journal.stage_remember(staged, "agent-1", [], now=3000) == (0, "")          # nothing to stage
    assert [r["id"] for r in journal.read_staged(staged)] == [a, b] and not jpath.exists()

    done = journal.approve_staged(staged, jpath, [a, "nope"], now=5000)
    assert [r["id"] for r in done] == [a] and [r["id"] for r in journal.read_staged(staged)] == [b]
    rec = journal.read_journal(jpath)[0]
    assert rec["t"] == 5000 and rec["valid_at"] == 1000 and rec["approved"] == a and "id" not in rec
    assert rec["facts"][0][:4] == ["web UI", "has", "ten tabs", 1000]       # valid from when it was staged
    assert rec["retire"] == ["old-1"] and rec["agent"] is True

    gone = journal.reject_staged(staged, log, None, now=6000)
    assert [r["id"] for r in gone] == [b] and not staged.exists()           # emptied → removed
    assert json.loads(log.read_text(encoding="utf-8"))["rejected_at"] == 6000
    assert len(journal.read_journal(jpath)) == 1                              # a rejected write never lands
    assert not (tmp_path / "g.staged.jsonl.lock").exists()


class _Emb:
    VOCAB = ["web", "tabs", "server", "port"]
    name = "fake:approval"

    def _vec(self, text):
        v = np.array([float(text.lower().count(w)) for w in self.VOCAB], dtype=np.float32)
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
    pages = [WikiPage(slug="000-a", title="A", level=1, order=0, pdf_page_start=1, pdf_page_end=1,
                      text="web tabs server port")]
    wiki = Wiki(title="T", pages=pages, source="x.pdf", split_level=1)
    write_wiki(wiki, tmp_path / "wiki")
    index = SemanticIndex.build(wiki, _Emb(), size_words=50, overlap_words=10)
    GraphBuilder(tmp_path / "graph").build(wiki, index)
    return index, GraphStore(tmp_path / "graph", writable=True)


def _call(server, args):
    res = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                         "params": {"name": "wiki_remember", "arguments": args}})
    return res["result"]["content"][0]["text"]


def test_staged_writes_land_only_when_approved_valid_from_staging(tmp_path):
    from openwiki.mcp_server import build_server
    index, store = _setup(tmp_path)
    emb = index.embedder
    try:
        long_ago = int(time.time()) - 40 * 86400
        store.remember("s1", [MemoryFact("openwiki", "has web UI with", "six tabs")], emb, now=long_ago)
        server = build_server(tmp_path / "wiki", index=index, graph=store, memory_writes=True, approve_writes=True)
        spec = next(t for t in server.tools if t["name"] == "wiki_remember")
        assert "staged for a person's approval" in spec["description"]
        out = _call(server, {"facts": [{"subject": "the web UI", "predicate": "has", "object": "ten tabs"}],
                             "replaces": ["openwiki | has web UI with | six tabs"]})
        assert "Staged 1 fact(s), closing 1 replaced fact(s) for approval (id " in out
        assert store.pending_ops() == 0 and len(store.staged_writes()) == 1
        assert store.fold_journal(emb)["remembered"] == 0                   # nothing to fold: not approved
        assert "six tabs" in store.context_for("web tabs", emb)
        staged_at = store.staged_writes()[0]["t"]

        approved_at = staged_at + 3600
        journal.approve_staged(store._staged_path, store._journal_path, None, now=approved_at)
        assert store.staged_writes() == [] and store.pending_ops() == 1
        assert store.fold_journal(emb)["retired"] == 1
        new = next(a for a in store.list_assertions() if a["object"] == "ten tabs")
        old = next(a for a in store.list_assertions() if a["object"] == "six tabs")
        assert new["valid_from"] == staged_at and new["created_at"] == approved_at     # valid then, believed now
        assert old["valid_to"] == staged_at and old["status"] == "past"                # the world changed then
        between = store.recall("web tabs", emb, k=5, known_at=staged_at + 60)       # staged, not yet approved
        assert all(h["object"] != "ten tabs" for h in between)
    finally:
        store.close()


def test_an_approval_closes_only_the_fact_that_was_reviewed(tmp_path):
    from openwiki.mcp_server import build_server
    index, store = _setup(tmp_path)
    emb = index.embedder
    try:
        long_ago = int(time.time()) - 40 * 86400
        store.remember("s1", [MemoryFact("server", "listens on", "port 8137")], emb, now=long_ago)
        server = build_server(tmp_path / "wiki", index=index, graph=store, memory_writes=True, approve_writes=True)
        _call(server, {"facts": [{"subject": "server", "predicate": "listens on", "object": "port 9000"}],
                       "replaces": ["server | listens on | port 8137"]})
        target = store.staged_writes()[0]["retire"][0]
        closed_meanwhile = long_ago + 86400
        store.retire([target], at=closed_meanwhile)                         # someone else closed it first
        journal.approve_staged(store._staged_path, store._journal_path, None)
        assert store.fold_journal(emb)["retired"] == 0                      # nothing re-closed
        old = next(a for a in store.list_assertions() if a["id"] == target)
        assert old["valid_to"] == closed_meanwhile
    finally:
        store.close()


def test_the_review_commands(tmp_path, monkeypatch, capsys):
    from openwiki import cli
    index, store = _setup(tmp_path)
    try:
        store.remember("s1", [MemoryFact("server", "listens on", "port 8137")], index.embedder,
                       now=int(time.time()) - 86400)
        old_id = store.list_assertions()[0]["id"]
        _, a = store.stage_remember("agent-x", [MemoryFact("server", "listens on", "port 9000")], retire=[old_id])
        _, b = store.stage_remember("agent-x", [MemoryFact("web UI", "has", "ten tabs")])
    finally:
        store.close()
    monkeypatch.chdir(tmp_path)
    graph = str(tmp_path / "graph")
    assert cli.main(["memory", "pending", "--graph", graph]) == 0
    out = capsys.readouterr().out
    assert f"[{a}]" in out and "+ server listens on port 9000" in out and "− - server listens on port 8137" in out
    assert "2 write(s) awaiting approval" in out
    assert cli.main(["memory", "approve", "--graph", graph]) == 2                       # say which
    capsys.readouterr()
    assert cli.main(["memory", "approve", a, "--graph", graph]) == 0
    assert "1 staged write(s) approved" in capsys.readouterr().out
    assert cli.main(["memory", "reject", "--all", "--graph", graph]) == 0
    assert "1 staged write(s) rejected" in capsys.readouterr().out
    assert cli.main(["memory", "pending", "--graph", graph]) == 0
    assert "no memory writes awaiting approval" in capsys.readouterr().out
    approved = journal.read_journal(journal.journal_path(tmp_path / "graph"))
    assert [r["approved"] for r in approved] == [a]
    assert json.loads(journal.rejected_path(tmp_path / "graph").read_text(encoding="utf-8"))["id"] == b


def test_approve_writes_is_opt_in(tmp_path):
    from openwiki.project import Project
    root = tmp_path / "p"
    root.mkdir()
    (root / "openwiki.toml").write_text('[project]\nname = "p"\n\n[memory]\nenabled = true\n', encoding="utf-8")
    assert Project.load(root).approve_writes is False
    (root / "openwiki.toml").write_text('[project]\nname = "p"\n\n[memory]\nenabled = true\napprove_writes = true\n',
                                        encoding="utf-8")
    assert Project.load(root).approve_writes is True


def test_the_memory_tab_lists_staged_writes_and_decides(tmp_path):
    from openwiki.web.server import WikiWebApp
    index, store = _setup(tmp_path)
    try:
        store.remember("s1", [MemoryFact("server", "listens on", "port 8137")], index.embedder,
                       now=int(time.time()) - 86400)
        old_id = store.list_assertions()[0]["id"]
        _, a = store.stage_remember("agent-x", [MemoryFact("server", "listens on", "port 9000")], retire=[old_id])
        _, b = store.stage_remember("agent-x", [MemoryFact("web UI", "has", "ten tabs")])
        folds = []
        app = WikiWebApp(tmp_path / "wiki", index=index, graph=store, on_approved=lambda: folds.append(1))
        staged = app.memory_info()["staged"]
        assert [w["id"] for w in staged] == [a, b]
        assert staged[0]["facts"] == ["server listens on port 9000"]
        assert staged[0]["closes"][0].startswith("- server listens on port 8137  (")
        assert app.memory_decide("approve", [a]) == {"approved": [a], "folding": True} and folds == [1]
        assert app.memory_decide("reject", None) == {"rejected": [b]}
        assert app.memory_info()["staged"] == [] and store.pending_ops() == 1
    finally:
        store.close()
