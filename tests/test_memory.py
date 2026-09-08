"""Tests for the Path B remembered tier (capture → merge → recall).

Pure capture/parse/format tests run without Kuzu; the remember/recall round-trips
(including the two-session proof-of-loop) are gated on Kuzu.
"""

from __future__ import annotations

import numpy as np

from openwiki.graph.memory import MemoryFact, capture_session, format_memory, parse_facts


# -- pure: capture / parse / format --------------------------------------------

def test_parse_facts_extracts_triples():
    raw = ('<think>weigh…</think> here you go: '
           '[{"subject":"the project","predicate":"uses","object":"Python 3.13"},'
           ' {"subject":"x","predicate":"y","object":"z"}]')
    facts = parse_facts(raw)
    assert [f.text() for f in facts] == ["the project uses Python 3.13", "x y z"]


def test_parse_facts_dedups_and_skips_bad():
    raw = ('[{"subject":"A","predicate":"is","object":"B"},'
           ' {"subject":"a","predicate":"IS","object":"b"},'      # normalized dup of the first
           ' {"subject":"","predicate":"p","object":"o"},'        # blank field → skip
           ' "not-a-dict"]')
    assert len(parse_facts(raw)) == 1


def test_parse_facts_handles_garbage():
    assert parse_facts("no json here") == []
    assert parse_facts("") == []


def test_capture_session_uses_chat():
    class _Chat:
        name = "fake"

        def chat(self, messages):
            return '[{"subject":"s","predicate":"p","object":"o"}]'

    facts = capture_session(_Chat(), "some transcript")
    assert len(facts) == 1 and facts[0].subject == "s"


def test_format_memory():
    assert format_memory([]) == ""
    out = format_memory([{"subject": "A", "predicate": "uses", "object": "B", "session_id": "s1"}])
    assert "A uses B" in out and "s1" in out


# -- Kuzu-gated: remember / recall ---------------------------------------------

class _MemEmbedder:
    VOCAB = ["python", "project", "version", "database", "kuzu"]
    name = "fake:mem"

    def _vec(self, text):
        low = text.lower()
        v = np.array([float(low.count(w)) for w in self.VOCAB], dtype=np.float32)
        return v if v.any() else v + 1e-6

    def embed_documents(self, texts):
        return np.vstack([self._vec(t) for t in texts])

    def embed_query(self, text):
        return self._vec(text)


def _build_graph(tmp_path):
    from openwiki.graph import GraphBuilder
    from openwiki.search import SemanticIndex
    from openwiki.wiki import Wiki, WikiPage

    pages = [WikiPage(slug="000-a", title="A", level=1, order=0, pdf_page_start=1,
                      pdf_page_end=1, text="python project database")]
    wiki = Wiki(title="T", pages=pages, source="x.pdf", split_level=1)
    index = SemanticIndex.build(wiki, _MemEmbedder(), size_words=50, overlap_words=10)
    GraphBuilder(tmp_path / "graph").build(wiki, index)
    return tmp_path / "graph"


def test_remember_then_recall_across_two_sessions(tmp_path):
    import pytest
    pytest.importorskip("kuzu")
    from openwiki.graph import GraphStore

    store = GraphStore(_build_graph(tmp_path), writable=True)
    try:
        # session 1 establishes facts
        facts = [MemoryFact("the project", "uses", "Python 3.13"),
                 MemoryFact("the database", "is", "Kuzu")]
        res = store.remember("s1", facts, _MemEmbedder())
        assert res["added"] == 2 and res["duplicates"] == 0
        assert store.has_memory()

        # session 2 asks something that depends on a session-1 fact
        hits = store.recall("what python version does the project use", _MemEmbedder(), k=3)
        assert hits, "expected memory to surface"
        assert "Python" in hits[0]["object"]          # the python fact ranks top
        assert hits[0]["session_id"] == "s1"          # recalled from the earlier session
        assert hits[0]["cos"] > hits[-1]["cos"]        # the database fact ranks lower
    finally:
        store.close()


def test_remember_dedups_across_sessions(tmp_path):
    import pytest
    pytest.importorskip("kuzu")
    from openwiki.graph import GraphStore

    store = GraphStore(_build_graph(tmp_path), writable=True)
    try:
        store.remember("s1", [MemoryFact("the project", "uses", "Python 3.13")], _MemEmbedder())
        res = store.remember("s2", [MemoryFact("The Project", "USES", "python 3.13")], _MemEmbedder())
        assert res["added"] == 0 and res["duplicates"] == 1       # normalized dup, not re-added
        n = store._rows("MATCH (a:Assertion) RETURN count(a);")[0][0]
        assert n == 1
    finally:
        store.close()


def test_recall_empty_without_memory(tmp_path):
    import pytest
    pytest.importorskip("kuzu")
    from openwiki.graph import GraphStore

    store = GraphStore(_build_graph(tmp_path))   # read-only, nothing remembered
    try:
        assert store.recall("anything", _MemEmbedder()) == []
        assert store.has_memory() is False
    finally:
        store.close()


def test_remember_requires_writable(tmp_path):
    import pytest
    pytest.importorskip("kuzu")
    from openwiki.graph import GraphStore

    store = GraphStore(_build_graph(tmp_path))   # read-only
    try:
        with pytest.raises(RuntimeError):
            store.remember("s", [MemoryFact("a", "b", "c")], _MemEmbedder())
    finally:
        store.close()
