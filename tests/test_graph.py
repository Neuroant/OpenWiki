"""Tests for the Kuzu graph layer (skipped cleanly if kuzu isn't installed).

Uses a deterministic ``FakeEmbedder`` so no Ollama/network is needed; builds a
small graph in a temp dir and asserts structure, similarity edges, neighborhood
queries, and the hybrid vector->graph query.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("kuzu")  # skip the whole module if Kuzu is unavailable

from openwiki.graph import GraphBuilder, GraphStore
from openwiki.search import SemanticIndex
from openwiki.wiki import Wiki, WikiPage


class FakeEmbedder:
    # a shared token ("nautilus") guarantees positive cosine -> SIMILAR_TO edges
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


def _wiki() -> Wiki:
    pages = [
        WikiPage(slug="000-a", title="Alpha", level=1, order=0, pdf_page_start=1,
                 pdf_page_end=1, text="alpha nautilus alpha", child_slugs=["001-b"]),
        WikiPage(slug="001-b", title="Beta", level=2, order=1, pdf_page_start=2,
                 pdf_page_end=2, text="beta nautilus beta", parent_slug="000-a"),
        WikiPage(slug="002-c", title="Gamma", level=1, order=2, pdf_page_start=3,
                 pdf_page_end=3, text="gamma nautilus gamma"),
    ]
    return Wiki(title="T", pages=pages, source="x.pdf", split_level=2)


@pytest.fixture
def store(tmp_path):
    wiki = _wiki()
    index = SemanticIndex.build(wiki, FakeEmbedder(), size_words=50, overlap_words=10)
    stats = GraphBuilder(tmp_path / "graph", similar_k=3).build(wiki, index)
    assert stats["pages"] == 3 and stats["chunks"] >= 3
    s = GraphStore(tmp_path / "graph")
    yield s
    s.close()


def test_stats(store):
    st = store.stats()
    assert st["pages"] == 3
    assert st["chunks"] >= 3
    assert st["child_of"] == 1          # b -> a
    assert st["similar_to"] >= 1        # shared token forces at least one edge


def test_neighborhood_structural(store):
    n = store.neighborhood("000-a")
    assert n["center"] == "000-a"
    rels = {node["slug"]: node["rel"] for node in n["nodes"]}
    assert rels["000-a"] == "center"
    assert rels["001-b"] == "child"     # a has child b
    # a is first in reading order, so its NEXT is b
    assert any(e["type"] == "next" and e["target"] == "001-b" for e in n["edges"])


def test_neighborhood_parent(store):
    n = store.neighborhood("001-b")
    assert any(node["rel"] == "parent" and node["slug"] == "000-a" for node in n["nodes"])


def test_neighborhood_missing_raises(store):
    with pytest.raises(KeyError):
        store.neighborhood("999-nope")


def test_similar_excludes_structural_neighbors(store):
    # a's child (b) must never appear as a SIMILAR_TO edge
    n = store.neighborhood("000-a")
    sim_targets = [e["target"] for e in n["edges"] if e["type"] == "similar"]
    assert "001-b" not in sim_targets


def test_hybrid_search(store):
    q = FakeEmbedder().embed_query("gamma nautilus")
    q = q / (np.linalg.norm(q) or 1.0)
    hits = store.hybrid_search(q.tolist(), k=3)
    assert hits
    assert hits[0]["page_slug"] == "002-c"          # gamma chunk is closest
    assert {"chunk_id", "text", "page_slug", "distance"} <= set(hits[0])


def test_build_is_idempotent(tmp_path):
    wiki = _wiki()
    index = SemanticIndex.build(wiki, FakeEmbedder(), size_words=50, overlap_words=10)
    builder = GraphBuilder(tmp_path / "graph", similar_k=3)
    builder.build(wiki, index)
    stats = builder.build(wiki, index)  # rebuild over existing db
    assert stats["pages"] == 3
    GraphStore(tmp_path / "graph").close()


def test_webapp_serves_neighborhood(store):
    from openwiki.web.server import WikiWebApp

    app = WikiWebApp(store.db_path.parent, graph=store)  # wiki_dir irrelevant here
    result = app.graph_neighborhood("000-a")
    assert result["center"] == "000-a"
    assert any(n["rel"] == "child" for n in result["nodes"])
