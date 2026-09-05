"""Tests for the usage-memory layer: pure decay math + reinforced-edge round-trips.

The decay math is pure (no Kuzu); the store/agent round-trips are gated on Kuzu.
"""

from __future__ import annotations

import numpy as np

from openwiki.graph.decay import (
    DAY_SECONDS, effective_weight, reinforced_weight,
)


# -- pure decay math -----------------------------------------------------------

def test_effective_weight_halves_after_one_half_life():
    now = 1_000_000
    hl = 10.0
    assert abs(effective_weight(4.0, now - int(hl * DAY_SECONDS), now, hl) - 2.0) < 1e-9


def test_effective_weight_two_half_lives_quarters():
    now = 2_000_000
    hl = 5.0
    assert abs(effective_weight(8.0, now - int(2 * hl * DAY_SECONDS), now, hl) - 2.0) < 1e-9


def test_effective_weight_fresh_unchanged():
    assert effective_weight(3.0, 1_000_000, 1_000_000) == 3.0


def test_effective_weight_edge_cases():
    assert effective_weight(0.0, 0, 100) == 0.0                    # no weight
    assert effective_weight(5.0, 0, 10 ** 9, half_life_days=0) == 5.0   # decay disabled
    assert effective_weight(2.0, 10 ** 9, 0) == 2.0                # future last_seen clamped


def test_reinforced_weight_bumps_and_caps():
    assert reinforced_weight(0.0, 1.0) == 1.0
    assert reinforced_weight(1.0, 0.5) == 1.5
    assert reinforced_weight(9.8, 1.0, cap=10.0) == 10.0          # capped
    assert reinforced_weight(-3.0, 1.0) == 1.0                    # negative floored to 0 first


# -- store round-trips (gated on Kuzu) -----------------------------------------

class _FakeEmbedder:
    VOCAB = ["alpha", "beta", "gamma", "nautilus"]
    name = "fake:bow"

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

    pages = [WikiPage(slug=f"00{i}-{c}", title=c.upper(), level=1, order=i, pdf_page_start=i + 1,
                      pdf_page_end=i + 1, text=f"{c} nautilus {c}")
             for i, c in enumerate(["a", "b", "c"])]
    wiki = Wiki(title="T", pages=pages, source="x.pdf", split_level=2)
    index = SemanticIndex.build(wiki, _FakeEmbedder(), size_words=50, overlap_words=10)
    GraphBuilder(tmp_path / "graph", similar_k=3).build(wiki, index)
    return tmp_path / "graph", index


def test_reinforce_shows_in_neighborhood_then_decays_away(tmp_path):
    import pytest
    pytest.importorskip("kuzu")
    from openwiki.graph import GraphStore

    gpath, _ = _build_graph(tmp_path)
    store = GraphStore(gpath, writable=True)
    try:
        store.reinforce("000-a", "002-c", boost=2.0)     # stamped "now"
        rein = [e for e in store.neighborhood("000-a")["edges"] if e["type"] == "reinforced"]
        assert any(e["target"] == "002-c" for e in rein)  # fresh edge is visible
        assert rein[0]["score"] > 0

        # age it 100 days with a 1-day half-life → effective weight ≈ 0 → pruned
        import time
        res = store.decay(now=int(time.time()) + 100 * DAY_SECONDS, half_life_days=1.0, floor=0.1)
        assert res["pruned"] >= 1
        assert not [e for e in store.neighborhood("000-a")["edges"] if e["type"] == "reinforced"]
    finally:
        store.close()


def test_reinforce_accumulates_weight(tmp_path):
    import pytest
    pytest.importorskip("kuzu")
    from openwiki.graph import GraphStore

    gpath, _ = _build_graph(tmp_path)
    store = GraphStore(gpath, writable=True)
    try:
        store.reinforce("000-a", "001-b", boost=1.0)
        r2 = store.reinforce("000-a", "001-b", boost=1.0)   # same edge again
        assert r2["weight"] == 2.0                          # bumped, not duplicated
        n = store._rows("MATCH (:Page {slug:'000-a'})-[r:REINFORCES]->(:Page) RETURN count(r);")[0][0]
        assert n == 1                                       # single edge
    finally:
        store.close()


def test_reinforce_and_decay_require_writable(tmp_path):
    import pytest
    pytest.importorskip("kuzu")
    from openwiki.graph import GraphStore

    gpath, _ = _build_graph(tmp_path)
    store = GraphStore(gpath)   # read-only
    try:
        with pytest.raises(RuntimeError):
            store.reinforce("000-a", "001-b")
        with pytest.raises(RuntimeError):
            store.decay()
    finally:
        store.close()


class _FakeChat:
    name = "fake:chat"

    def chat(self, messages):
        return "Antwort [1]."


def test_rag_expansion_reinforces_when_graph_writable(tmp_path):
    import pytest
    pytest.importorskip("kuzu")
    from openwiki.agent import RAGAgent
    from openwiki.graph import GraphStore
    from openwiki.search import SemanticIndex
    from openwiki.wiki import Wiki, WikiPage

    gpath, _ = _build_graph(tmp_path)
    index = SemanticIndex.build(
        Wiki(title="T", source="x.pdf", split_level=2, pages=[
            WikiPage(slug=f"00{i}-{c}", title=c.upper(), level=1, order=i, pdf_page_start=i + 1,
                     pdf_page_end=i + 1, text=f"{c} nautilus {c}") for i, c in enumerate(["a", "b", "c"])]),
        _FakeEmbedder(), size_words=50, overlap_words=10)

    store = GraphStore(gpath, writable=True)
    try:
        agent = RAGAgent(index, _FakeChat(), top_k=1, graph=store, expand_k=2)
        sources = agent.retrieve("alpha nautilus")           # expands from top seed 000-a
        assert any(s.kind == "related" for s in sources)     # expansion happened
        # the expansion strengthened a usage edge out of the top seed
        n = store._rows("MATCH (:Page {slug:'000-a'})-[r:REINFORCES]->(:Page) RETURN count(r);")[0][0]
        assert n >= 1
    finally:
        store.close()


def test_read_only_expansion_does_not_reinforce(tmp_path):
    import pytest
    pytest.importorskip("kuzu")
    from openwiki.agent import RAGAgent
    from openwiki.graph import GraphStore
    from openwiki.search import SemanticIndex
    from openwiki.wiki import Wiki, WikiPage

    gpath, _ = _build_graph(tmp_path)
    index = SemanticIndex.build(
        Wiki(title="T", source="x.pdf", split_level=2, pages=[
            WikiPage(slug=f"00{i}-{c}", title=c.upper(), level=1, order=i, pdf_page_start=i + 1,
                     pdf_page_end=i + 1, text=f"{c} nautilus {c}") for i, c in enumerate(["a", "b", "c"])]),
        _FakeEmbedder(), size_words=50, overlap_words=10)

    store = GraphStore(gpath)   # read-only → retrieval must not write
    try:
        RAGAgent(index, _FakeChat(), top_k=1, graph=store, expand_k=2).retrieve("alpha nautilus")
        n = store._rows("MATCH (:Page)-[r:REINFORCES]->(:Page) RETURN count(r);")[0][0]
        assert n == 0
    finally:
        store.close()
