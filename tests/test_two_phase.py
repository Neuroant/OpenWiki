"""Writes that land during a session (v0.100): two-phase memory writes, the lazy read-only graph, the fold
worker. Kuzu-gated where a real graph is needed; the model checks are counting fakes."""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from openwiki.graph.memory import MemoryFact
from openwiki.graph.store import memoized


class _Emb:
    name = "fake:2p"
    VOCAB = ["project", "uses", "kuzu", "ollama", "python", "port"]

    def __init__(self):
        self.calls = 0

    def _v(self, t):
        low = t.lower()
        v = np.array([float(low.count(w)) for w in self.VOCAB], dtype=np.float32)
        return v if v.any() else v + 1e-6

    def embed_documents(self, texts):
        self.calls += 1
        return np.vstack([self._v(t) for t in texts])

    def embed_query(self, text):
        self.calls += 1
        return self._v(text)


class _Coexist:
    """A counting coexistence check: different tools never coexist (the older value is closed)."""

    def __init__(self):
        self.calls = 0

    def __call__(self, older, newer, subjects=None):
        self.calls += 1
        return False


def _graph(tmp_path):
    from openwiki.graph import GraphBuilder
    from openwiki.search import SemanticIndex
    from openwiki.wiki import Wiki, WikiPage
    wiki = Wiki(title="T", pages=[WikiPage(slug="000-a", title="A", level=1, order=0, pdf_page_start=1,
                                           pdf_page_end=1, text="project uses kuzu")], source="x.pdf", split_level=1)
    GraphBuilder(tmp_path / "graph").build(wiki, SemanticIndex.build(wiki, _Emb(), size_words=50, overlap_words=10))
    return tmp_path / "graph"


def _seed(path):
    from openwiki.graph import GraphStore
    g = GraphStore(path, writable=True)
    try:
        g.remember("s0", [MemoryFact("project", "uses", "Kuzu")], _Emb(), session_date=1_700_000_000)
    finally:
        g.close()


def test_memoized_keeps_answers_by_arguments():
    calls = []

    def check(fact, labels, **kw):
        calls.append(fact)
        return len(labels)
    m = memoized(check)
    assert m("a", ["x", "y"]) == 2 and m("a", ["x", "y"]) == 2 and m("a", ["x"], k=[1]) == 1
    assert calls == ["a", "a"] and memoized(None) is None


def test_dry_run_plans_without_writing_and_the_write_pass_asks_nothing_new(tmp_path):
    pytest.importorskip("kuzu")
    from openwiki.embeddings import CachingEmbedder
    from openwiki.graph import GraphStore
    path = _graph(tmp_path)
    _seed(path)
    coexist, emb = _Coexist(), _Emb()
    check, cache = memoized(coexist), CachingEmbedder(emb)
    new = [MemoryFact("project", "uses", "Ollama")]
    reader = GraphStore(path)                                   # read-only is enough for the plan
    try:
        planned = reader.remember("s1", new, cache, session_date=1_750_000_000, coexist=check, dry_run=True)
        assert len(reader.list_assertions()) == 1               # nothing was written
    finally:
        reader.close()
    assert (planned["added"], planned["superseded"]) == (1, 1) and coexist.calls == 1
    asked, embedded = coexist.calls, emb.calls
    writer = GraphStore(path, writable=True)
    try:
        done = writer.remember("s1", new, cache, session_date=1_750_000_000, coexist=check)
        current = [a["object"] for a in writer.list_assertions(include_superseded=False)]
    finally:
        writer.close()
    assert done == planned                                      # the same merge
    assert coexist.calls == asked and emb.calls == embedded     # answered from the caches
    assert current == ["Ollama"]


def test_fold_journal_dry_run_keeps_the_journal(tmp_path):
    pytest.importorskip("kuzu")
    from openwiki.graph import GraphStore
    path = _graph(tmp_path)
    _seed(path)
    reader = GraphStore(path)
    try:
        reader.queue_remember("agent-1", [MemoryFact("project", "uses", "Python")], retire=["x"], agent=True)
        res = reader.fold_journal(_Emb(), dry_run=True)
        assert res["records"] == 1 and res["remembered"] == 1 and res["retired"] == 0
        assert reader.pending_ops() == 1 and len(reader.list_assertions()) == 1
    finally:
        reader.close()


def test_lazy_graph_holds_the_graph_only_while_used(tmp_path):
    pytest.importorskip("kuzu")
    from openwiki.graph import GraphStore
    from openwiki.graph.lazy import LazyGraph
    path = _graph(tmp_path)
    lazy = LazyGraph(path, wait=0.5, log_usage=True)
    assert lazy.has_memory() is False and lazy.writable is False
    GraphStore(path, writable=True).close()                     # idle: a writer gets in
    with lazy.session() as store:
        assert store.log_usage is True
        with pytest.raises(Exception):
            GraphStore(path, writable=True)                     # in use: the writer must wait
    with pytest.raises(AttributeError):
        lazy.not_a_method
    lazy.close()


def test_lazy_graph_waits_for_a_writer_to_finish(tmp_path):
    pytest.importorskip("kuzu")
    from openwiki.graph import GraphStore
    from openwiki.graph.lazy import LazyGraph
    path = _graph(tmp_path)
    writer = GraphStore(path, writable=True)
    threading.Timer(0.6, writer.close).start()                  # the writer applies, then lets go
    started = time.monotonic()
    assert LazyGraph(path, wait=10.0).has_memory() is False
    assert 0.5 <= time.monotonic() - started < 10.0


def test_write_memory_plans_read_only_and_applies_from_the_cache(tmp_path):
    pytest.importorskip("kuzu")
    from openwiki import cli
    from openwiki.graph import GraphStore
    path = _graph(tmp_path)
    _seed(path)
    reader = GraphStore(path)
    try:                                                        # an agent write waiting in the journal
        reader.queue_remember("agent-1", [MemoryFact("project", "listens on", "port 8137")], agent=True)
    finally:
        reader.close()
    coexist = _Coexist()
    seen = []

    def counting(*a, **kw):
        seen.append("call")
        return coexist(*a, **kw)
    res = cli._write_memory(path, _Emb(), session_id="s1", facts=[MemoryFact("project", "uses", "Ollama")],
                            session_date=1_750_000_000, coexist=counting)
    assert res["remembered"]["added"] == 1 and res["folded"]["records"] == 1 and res["lock_s"] >= 0
    assert len(seen) == 1                                       # asked once, in the read-only plan pass
    g = GraphStore(path)
    try:
        current = sorted(a["object"] for a in g.list_assertions(include_superseded=False))
        assert current == ["Ollama", "port 8137"] and g.pending_ops() == 0
    finally:
        g.close()


def test_write_memory_queues_when_the_graph_stays_locked(tmp_path):
    pytest.importorskip("kuzu")
    from openwiki import cli
    from openwiki.graph.lazy import LazyGraph
    path = _graph(tmp_path)
    lazy = LazyGraph(path)
    with lazy.session():                                        # a reader that never lets go
        res = cli._write_memory(path, _Emb(), session_id="s9", facts=[MemoryFact("a", "b", "c")], wait=0.5)
    assert res is None
    from openwiki.graph.journal import journal_path, read_journal
    assert read_journal(journal_path(path))[0]["facts"] == [["a", "b", "c"]]


def test_fold_worker_folds_the_queue(tmp_path, monkeypatch):
    from openwiki import cli
    from openwiki.project import Project
    root = tmp_path / "brain"
    root.mkdir()
    (root / "openwiki.toml").write_text('[project]\nname = "brain"\n\n[memory]\nenabled = true\n', encoding="utf-8")
    project = Project.load(root)
    project.graph_path.parent.mkdir(parents=True, exist_ok=True)
    project.graph_path.write_text("", encoding="utf-8")
    from openwiki.graph.journal import append_remember, journal_path
    append_remember(journal_path(project.graph_path), "agent-1", [("a", "b", "c")], agent=True)
    calls = []

    def fake_write(path, embedder, **kw):
        calls.append(kw.get("fold"))
        journal_path(path).unlink()                             # folded
        return {"folded": {"records": 1, "remembered": 1, "retired": 0}, "lock_s": 0.1}
    monkeypatch.setattr(cli, "FOLD_DEBOUNCE_S", 0)
    monkeypatch.setattr(cli, "_hook_embedder", lambda project: object())
    monkeypatch.setattr(cli, "_coexist_check", lambda *a: None)
    monkeypatch.setattr(cli, "_attribute_resolver", lambda *a: None)
    monkeypatch.setattr(cli, "_write_memory", fake_write)
    cli._run_hook("fold", {}, str(root))
    assert calls == [True] and not (project.state_dir / "fold.lock").exists()


def test_wiki_remember_starts_the_fold():
    from openwiki.mcp_server import _remember

    class _Graph:
        def match_facts(self, lines):
            return {}, []

        def queue_remember(self, *a, **kw):
            pass
    started = []
    out = _remember(_Graph(), None, {"facts": [{"subject": "web UI", "predicate": "has", "object": "ten tabs"}]},
                    on_queued=lambda: started.append(True))
    assert started == [True] and "within a minute" in out
    assert "next write pass" in _remember(_Graph(), None, {"facts": [{"subject": "x", "predicate": "y", "object": "z"}]})
