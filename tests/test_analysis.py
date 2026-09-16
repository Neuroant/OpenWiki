"""Graph↔semantic coupling analysis (world-model analysis, P1).

Pure/offline: a fake index (chunks + a normalized embedding matrix) and a fake graph
(the ``coupling_edges``/``community_members`` surface the analyzer needs), so no Ollama
and no Kuzu. The scenario is two tight semantic clusters {p0,p1} and {p2,p3} that are
orthogonal to each other, with a SIMILAR_TO edge *within* a cluster and a REFERENCES
edge *across* clusters — so we can assert the analyzer separates "semantic" from
"structural" reach.
"""

import numpy as np

from openwiki.analysis.coupling import (
    analyze_coupling, edge_semantic_profile, graph_reach, neighbor_overlap, page_vectors,
)


class _Chunk:
    def __init__(self, page_slug):
        self.page_slug = page_slug


class _FakeIndex:
    """Minimal SemanticIndex stand-in: one chunk per page, a 2-D embedding each."""
    def __init__(self, slugs, vectors):
        self.chunks = [_Chunk(s) for s in slugs]
        self.embeddings = np.asarray(vectors, dtype=np.float32)


class _FakeGraph:
    def __init__(self, edges, communities):
        self._edges = edges
        self._communities = communities

    def coupling_edges(self):
        # mirror GraphStore.coupling_edges: every kind present (possibly empty)
        base = {k: [] for k in ("similar", "references", "shared_entity",
                                "relation", "child_of", "next")}
        base.update(self._edges)
        return base

    def community_members(self):
        return self._communities


def _scenario():
    # p0,p1 point along +x; p2,p3 along +y — two orthogonal clusters.
    slugs = ["p0", "p1", "p2", "p3"]
    vecs = [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 1.0]]
    index = _FakeIndex(slugs, vecs)
    graph = _FakeGraph(
        edges={
            "similar": [("p0", "p1"), ("p2", "p3")],   # within-cluster, cosine ~1
            "references": [("p0", "p2")],               # across-cluster, cosine ~0
        },
        communities={0: ["p0", "p1"], 1: ["p2", "p3"]},
    )
    return index, graph


def test_page_vectors_are_normalized_and_sorted():
    index, _ = _scenario()
    slugs, vecs = page_vectors(index)
    assert slugs == ["p0", "p1", "p2", "p3"]
    assert np.allclose(np.linalg.norm(vecs, axis=1), 1.0)


def test_edge_profile_separates_similar_from_reference():
    index, graph = _scenario()
    slugs, vecs = page_vectors(index)
    idx = {s: i for i, s in enumerate(slugs)}
    from openwiki.analysis.coupling import _null_cosines
    prof = edge_semantic_profile(graph.coupling_edges(), idx, vecs, _null_cosines(vecs))
    # within-cluster SIMILAR_TO endpoints are identical → cosine ~1
    assert prof["similar"]["mean"] > 0.9
    # across-cluster REFERENCE endpoints are orthogonal → cosine ~0, well below similar
    assert prof["references"]["mean"] < 0.2
    assert prof["similar"]["mean"] > prof["references"]["mean"]


def test_graph_reach_flags_the_non_semantic_reference():
    index, graph = _scenario()
    slugs, vecs = page_vectors(index)
    idx = {s: i for i, s in enumerate(slugs)}
    from openwiki.analysis.coupling import _null_cosines
    reach = graph_reach(graph.coupling_edges(), idx, vecs, _null_cosines(vecs))
    # the single reference links two orthogonal pages → below the random-pair median
    assert reach["pairs"] == 1
    assert reach["non_semantic_fraction"] == 1.0


def test_neighbor_overlap_higher_for_similar_than_reference():
    index, graph = _scenario()
    slugs, vecs = page_vectors(index)
    ov = neighbor_overlap(graph.coupling_edges(), slugs, vecs, k=1)
    # a page's top-1 embedding neighbor is its cluster twin, which is also its SIMILAR_TO
    # neighbor → high overlap; the REFERENCE neighbor is nobody's nearest → low overlap
    assert ov["similar"] >= ov["references"]


def test_analyze_coupling_shape_and_reach():
    index, graph = _scenario()
    res = analyze_coupling(index, graph, k=1)
    assert res["pages"] == 4
    assert res["edge_counts"]["similar"] == 2
    assert res["graph_reach"]["non_semantic_fraction"] == 1.0
    assert "community_coherence" in res            # present whether or not sklearn is installed
    assert "available" in res["community_coherence"]


def test_edges_restricted_to_pages_in_the_index():
    # an edge to a page absent from the index is dropped, not crashed on
    index = _FakeIndex(["p0", "p1"], [[1.0, 0.0], [0.9, 0.1]])
    graph = _FakeGraph(edges={"references": [("p0", "ghost")]}, communities={})
    res = analyze_coupling(index, graph, k=1)
    assert res["edge_counts"]["references"] == 0
    assert res["graph_reach"]["pairs"] == 0
