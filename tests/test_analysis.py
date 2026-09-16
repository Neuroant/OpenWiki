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
    def __init__(self, edges, communities, shared=None, entities=None, orphans=None):
        self._edges = edges
        self._communities = communities
        self._shared = shared or []          # [(a, b, n)]
        self._entities = entities or []       # [{"name", "type"}]
        self._orphans = orphans or []

    def coupling_edges(self):
        # mirror GraphStore.coupling_edges: every kind present (possibly empty)
        base = {k: [] for k in ("similar", "references", "shared_entity",
                                "relation", "child_of", "next")}
        base.update(self._edges)
        return base

    def community_members(self):
        return self._communities

    def shared_entity_pairs(self):
        return self._shared

    def all_entities(self):
        return self._entities

    def has_entities(self):
        return bool(self._entities)

    def health(self):
        return {"orphans": self._orphans}


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


def _gaps_scenario():
    slugs = ["p0", "p1", "p2", "p3"]
    vecs = [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 1.0]]
    index = _FakeIndex(slugs, vecs)
    graph = _FakeGraph(
        edges={},                                   # no references / structural links
        communities={},
        shared=[("p0", "p2", 3), ("p0", "p1", 1)],  # p0/p2 co-mention 3 entities, uncited
        entities=[{"name": "Signal", "type": "K"}, {"name": "Signale", "type": "K"},
                  {"name": "Effekt", "type": "K"}],
        orphans=[{"slug": "p3", "title": "Lonely"}],
    )
    return index, graph


def test_link_candidates_rank_shared_entities_without_reference():
    from openwiki.analysis.gaps import link_candidates
    index, graph = _gaps_scenario()
    cands = link_candidates(index, graph, top=5)
    assert cands, "expected missing-cross-reference candidates"
    top = cands[0]
    assert {top["a"], top["b"]} == {"p0", "p2"}      # 3 shared > 1 shared ranks first
    assert top["shared_entities"] == 3


def test_link_candidates_exclude_existing_references():
    from openwiki.analysis.gaps import link_candidates
    index, graph = _gaps_scenario()
    graph._edges = {"references": [("p0", "p2")]}     # now p0↔p2 is cited
    cands = link_candidates(index, graph, top=5)
    assert all({c["a"], c["b"]} != {"p0", "p2"} for c in cands)


def test_redundant_pages_finds_near_duplicates():
    from openwiki.analysis.gaps import redundant_pages
    index, graph = _gaps_scenario()
    red = redundant_pages(index, top=5, min_cos=0.9)
    assert any(r["cosine"] >= 0.9 for r in red)       # p0≡p1 and p2≡p3 are identical


def test_entity_merge_candidates_catch_name_variants():
    from openwiki.analysis.gaps import entity_merge_candidates
    _, graph = _gaps_scenario()
    cands = entity_merge_candidates(graph, min_sim=0.8)
    assert any({c["a"], c["b"]} == {"Signal", "Signale"} for c in cands)


def test_entity_merge_skips_numbered_siblings():
    from openwiki.analysis.gaps import entity_merge_candidates
    graph = _FakeGraph(edges={}, communities={}, entities=[
        {"name": "Effect Control 1", "type": "P"}, {"name": "Effect Control 2", "type": "P"}])
    # distinct numbered parameters must NOT be proposed as a merge
    assert entity_merge_candidates(graph, min_sim=0.8) == []


def test_analyze_gaps_shape():
    from openwiki.analysis.gaps import analyze_gaps
    index, graph = _gaps_scenario()
    res = analyze_gaps(index, graph, top=5)
    assert set(res) == {"link_candidates", "redundant_pages",
                        "isolated_pages", "entity_merge_candidates"}
    assert "semantic_outliers" in res["isolated_pages"]
    assert res["isolated_pages"]["structural_orphans"] == [{"slug": "p3", "title": "Lonely"}]


def _fp(pages, reach, sil, ref_overlap):
    return {
        "pages": pages,
        "edge_profile": {"_null": {"mean": 0.74},
                         "references": {"n": 10, "mean": 0.80, "lift": 0.06}},
        "neighbor_overlap": {"references": ref_overlap},
        "graph_reach": {"non_semantic_fraction": reach},
        "community_coherence": {"available": True, "silhouette": sil, "ari": 0.40},
    }


def test_is_coupling_fingerprint():
    from openwiki.analysis import is_coupling_fingerprint
    assert is_coupling_fingerprint(_fp(50, 0.36, 0.09, 0.24))
    assert not is_coupling_fingerprint({"link_candidates": []})   # a gaps report
    assert not is_coupling_fingerprint("nope")


def test_diff_fingerprints_deltas():
    from openwiki.analysis import diff_fingerprints
    rows = diff_fingerprints(_fp(50, 0.36, 0.09, 0.24), _fp(60, 0.40, 0.15, 0.30))
    d = {r["metric"]: r for r in rows}
    assert d["pages"]["delta"] == 10
    assert d["graph_reach"]["delta"] == 0.04
    assert d["references.overlap"]["delta"] == 0.06
    assert d["coherence.silhouette"]["delta"] == 0.06


def test_notable_differences_skips_counts():
    from openwiki.analysis import diff_fingerprints
    from openwiki.analysis.compare import notable_differences
    rows = diff_fingerprints(_fp(50, 0.36, 0.09, 0.24), _fp(500, 0.37, 0.10, 0.25))
    nd = notable_differences(rows, top=3)
    assert all(r["metric"] != "pages" and not r["metric"].endswith(".n") for r in nd)


def test_self_diff_is_all_zero():
    from openwiki.analysis import diff_fingerprints
    from openwiki.analysis.compare import notable_differences
    a = _fp(50, 0.36, 0.09, 0.24)
    rows = diff_fingerprints(a, a)
    assert all(r["delta"] in (0, 0.0) for r in rows if r["delta"] is not None)
    assert notable_differences(rows) == []      # identical fingerprints → nothing notable


_NOW = 1_000_000_000
_DAY = 86400


class _FakeMemGraph:
    def __init__(self, facts, themes=None, assignment=None):
        self._facts = facts
        self._themes = themes or []
        self._assignment = assignment or {}

    def has_memory(self):
        return bool(self._facts)

    def memory_overview(self):
        current = [f for f in self._facts if not f["superseded"]]
        return {"sessions": len({f["session_id"] for f in self._facts}),
                "assertions": len(current), "superseded": len(self._facts) - len(current),
                "themes": len(self._themes)}

    def list_assertions(self, limit=1_000_000, include_superseded=True):
        fs = self._facts if include_superseded else [f for f in self._facts if not f["superseded"]]
        return fs[:limit]

    def memory_concepts(self):
        return self._themes

    def concept_assignment(self):
        return self._assignment


def _mf(aid, subj, pred, obj, sess, created, conf=1.0, seen=None, sup=False):
    return {"id": aid, "subject": subj, "predicate": pred, "object": obj, "session_id": sess,
            "created_at": created, "confidence": conf,
            "last_seen": seen if seen is not None else created, "superseded": sup}


def _mem_scenario():
    facts = [
        _mf("a1", "port", "is", "8137", "s1", _NOW, conf=2.0, seen=_NOW),          # hot, re-affirmed
        _mf("a2", "model", "is", "bge-m3", "s1", _NOW, seen=_NOW),                 # hot
        _mf("a3", "chunk", "size", "180", "s2", _NOW - 40 * _DAY, seen=_NOW - 40 * _DAY),  # warm
        _mf("a4", "old", "was", "thing", "s2", _NOW - 120 * _DAY, seen=_NOW - 120 * _DAY),  # cold
        _mf("a5", "port", "is", "9000", "s3", _NOW - 10 * _DAY, sup=True),         # superseded
    ]
    themes = [{"id": 0, "label": "config", "summary": "", "size": 2}]
    assignment = {"a1": 0, "a2": 0}
    return _FakeMemGraph(facts, themes, assignment)


def test_analyze_memory_unavailable_when_empty():
    from openwiki.analysis import analyze_memory
    assert analyze_memory(_FakeMemGraph([]))["available"] is False


def test_analyze_memory_counts_and_revision():
    from openwiki.analysis import analyze_memory
    res = analyze_memory(_mem_scenario(), now=_NOW)
    assert res["counts"] == {"sessions": 3, "current": 4, "superseded": 1, "themes": 1}
    assert res["revision"]["revision_rate"] == 0.2          # 1 of 5 superseded


def test_analyze_memory_consolidation_coverage():
    from openwiki.analysis import analyze_memory
    con = analyze_memory(_mem_scenario(), now=_NOW)["consolidation"]
    assert con["consolidated_facts"] == 2 and con["coverage"] == 0.5


def test_analyze_memory_temperature_buckets():
    from openwiki.analysis import analyze_memory
    t = analyze_memory(_mem_scenario(), now=_NOW, half_life=30)["temperature"]
    assert t["hot"] == 2 and t["warm"] == 1 and t["cold"] == 1   # a1/a2 hot, a3 warm, a4 cold
    assert t["reaffirmed_fraction"] == 0.25                       # only a1 has confidence > 1
    assert t["mean_confidence"] == 1.25                           # (2+1+1+1)/4


def test_analyze_memory_growth_is_oldest_first():
    from openwiki.analysis import analyze_memory
    growth = analyze_memory(_mem_scenario(), now=_NOW)["growth"]
    assert sum(g["facts"] for g in growth) == 5                   # counts all facts incl. superseded
    assert growth[0]["session_id"] == "s2"                        # s2 has the oldest fact (−120d)


def test_project_2d_pca_is_normalized_and_shaped():
    from openwiki.analysis.projection import project_2d
    rng = np.random.default_rng(0)
    vecs = rng.normal(size=(20, 8)).astype(np.float32)
    coords, method = project_2d(vecs, method="pca")
    assert method == "pca"
    assert coords.shape == (20, 2)
    # min-max normalized into [0, 1] on each axis
    assert coords.min() >= -1e-6 and coords.max() <= 1 + 1e-6
    assert np.allclose(coords.min(axis=0), 0.0, atol=1e-6)
    assert np.allclose(coords.max(axis=0), 1.0, atol=1e-6)


def test_project_2d_auto_falls_back_to_pca_without_umap():
    from openwiki.analysis.projection import project_2d
    vecs = np.eye(5, dtype=np.float32)
    coords, method = project_2d(vecs, method="auto")   # umap not installed in the test env
    assert method in ("pca", "umap")                   # either is acceptable; shape must hold
    assert coords.shape == (5, 2)


def test_project_2d_handles_tiny_inputs():
    from openwiki.analysis.projection import project_2d
    coords, method = project_2d(np.zeros((0, 4), dtype=np.float32))
    assert coords.shape == (0, 2) and method == "none"
    coords, method = project_2d(np.ones((2, 4), dtype=np.float32))
    assert coords.shape == (2, 2) and method == "trivial"


def test_edges_restricted_to_pages_in_the_index():
    # an edge to a page absent from the index is dropped, not crashed on
    index = _FakeIndex(["p0", "p1"], [[1.0, 0.0], [0.9, 0.1]])
    graph = _FakeGraph(edges={"references": [("p0", "ghost")]}, communities={})
    res = analyze_coupling(index, graph, k=1)
    assert res["edge_counts"]["references"] == 0
    assert res["graph_reach"]["pairs"] == 0
