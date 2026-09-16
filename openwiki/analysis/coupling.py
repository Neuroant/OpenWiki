"""Graph↔semantic coupling — the first world-model analysis (P1).

OpenWiki is unusual in holding **two representations of the same corpus**: a discrete
symbolic graph (Pages linked by SIMILAR_TO / REFERENCES / shared-entity / typed
relations) and a continuous semantic manifold (the chunk embeddings). This module
measures how those two agree.

The intuition (and the reason it matters): if the graph's edges only ever connect
pages the embedder already puts next to each other, the graph is *redundant* with
similarity — it adds nothing retrieval couldn't get from cosine. Where the graph
connects pages the embedder puts *far apart*, it encodes real, non-semantic
structure — and that is exactly the reach GraphRAG's answer-quality win came from
(``docs/RAG-vs-GraphRAG.md``). So we turn "how much does the graph add?" into numbers:

- **edge_profile** — per edge type, the distribution of endpoint cosine similarity,
  against a random-pair *null*. SIMILAR_TO sits near the top by construction; the gap
  down to REFERENCES / shared_entity / relation is that edge type's non-semantic reach.
- **neighbor_overlap** — per page, Jaccard(graph neighbors, embedding k-NN), by edge
  type. Low overlap ⇒ the graph points where the embeddings don't.
- **graph_reach** — the headline scalar: the fraction of the graph's *non-similarity*
  connections whose endpoints are semantically no closer than a random pair — i.e.
  links the embedder would never surface.
- **community_coherence** — do the Louvain communities also form embedding clusters?
  (silhouette + ARI vs k-means). Needs scikit-learn (the ``[analysis]`` extra);
  absent, it degrades to ``{"available": False}``.

Everything here is pure/read-only and unit-testable with a fake index + fake graph.
"""

from __future__ import annotations

import numpy as np

# The graph's *non-similarity* structure — the edges whose reach beyond the embedding
# geometry is the interesting signal (SIMILAR_TO is cosine-derived, so it's the anchor,
# not part of the "reach"; CHILD_OF/NEXT are structural navigation, reported separately).
SEMANTIC_REACH_KINDS = ("references", "shared_entity", "relation")


def page_vectors(index) -> "tuple[list[str], np.ndarray]":
    """Per-page semantic vector = the L2-normalized mean of the page's chunk embeddings.

    Returns ``(slugs, matrix)`` with ``matrix[i]`` the unit vector for ``slugs[i]``
    (slugs sorted for determinism). Pages are the unit of the graph, so we collapse
    the chunk-level index to page level to compare against page↔page edges.
    """
    by_page: "dict[str, list[int]]" = {}
    for i, chunk in enumerate(index.chunks):
        by_page.setdefault(chunk.page_slug, []).append(i)
    slugs = sorted(by_page)
    if not slugs:
        return [], np.zeros((0, 0), dtype=np.float32)
    mat = np.vstack([np.asarray(index.embeddings)[by_page[s]].mean(axis=0) for s in slugs])
    mat = mat.astype(np.float32)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return slugs, mat / norms


def _pct(a: np.ndarray, p: float) -> float:
    return float(np.percentile(a, p)) if len(a) else 0.0


def _pair_cosines(pairs, idx: "dict[str, int]", vecs: np.ndarray) -> np.ndarray:
    """Cosine similarity for each ``(a, b)`` slug pair both present in the index."""
    out = []
    for a, b in pairs:
        ia, ib = idx.get(a), idx.get(b)
        if ia is not None and ib is not None:
            out.append(float(vecs[ia] @ vecs[ib]))
    return np.asarray(out, dtype=np.float32)


def _null_cosines(vecs: np.ndarray, n: int = 4000, seed: int = 0) -> np.ndarray:
    """A baseline: cosine of ``n`` random (distinct) page pairs — the distribution any
    edge type is measured *against*. An edge type is only meaningfully "semantic" if its
    endpoints are closer than this."""
    m = len(vecs)
    if m < 2:
        return np.zeros(0, dtype=np.float32)
    rng = np.random.default_rng(seed)
    a = rng.integers(0, m, size=n)
    b = rng.integers(0, m, size=n)
    mask = a != b
    a, b = a[mask], b[mask]
    return (vecs[a] * vecs[b]).sum(axis=1).astype(np.float32)


def edge_semantic_profile(edges_by_kind, idx, vecs, null) -> dict:
    """Per edge type: the endpoint-cosine distribution + its *lift* over the null mean."""
    null_mean = float(null.mean()) if len(null) else 0.0
    prof: dict = {"_null": {
        "mean": round(null_mean, 3),
        "median": round(float(np.median(null)), 3) if len(null) else 0.0,
        "n": int(len(null)),
    }}
    for kind, pairs in edges_by_kind.items():
        cos = _pair_cosines(pairs, idx, vecs)
        if not len(cos):
            prof[kind] = {"n": 0}
            continue
        prof[kind] = {
            "n": int(len(cos)),
            "mean": round(float(cos.mean()), 3),
            "median": round(float(np.median(cos)), 3),
            "p25": round(_pct(cos, 25), 3),
            "p75": round(_pct(cos, 75), 3),
            "lift": round(float(cos.mean()) - null_mean, 3),
        }
    return prof


def _knn(slugs, vecs, k: int) -> "dict[str, set]":
    """Top-``k`` embedding neighbors per page (excluding self)."""
    if len(slugs) < 2:
        return {s: set() for s in slugs}
    sims = vecs @ vecs.T
    np.fill_diagonal(sims, -np.inf)
    kk = min(k, len(slugs) - 1)
    return {slugs[i]: {slugs[j] for j in np.argsort(-sims[i])[:kk]} for i in range(len(slugs))}


def neighbor_overlap(edges_by_kind, slugs, vecs, k: int = 8) -> dict:
    """Mean Jaccard(graph neighbors, embedding k-NN) per edge type. High ⇒ the graph
    is redundant with similarity; low ⇒ it reaches elsewhere."""
    knn = _knn(slugs, vecs, k)
    result: dict = {}
    for kind, pairs in edges_by_kind.items():
        neigh: "dict[str, set]" = {}
        for a, b in pairs:
            neigh.setdefault(a, set()).add(b)
            neigh.setdefault(b, set()).add(a)
        js = []
        for s, gn in neigh.items():
            kn = knn.get(s)
            if kn is None:
                continue
            union = gn | kn
            js.append(len(gn & kn) / len(union) if union else 0.0)
        result[kind] = round(float(np.mean(js)), 3) if js else None
    return result


def graph_reach(edges_by_kind, idx, vecs, null) -> dict:
    """The headline scalar. Over the union of the graph's *non-similarity* connections
    (references ∪ shared_entity ∪ relation), the fraction whose endpoint cosine is at or
    below the *median* random pair — connections similarity alone would never surface.
    High ⇒ the graph encodes structure the embedding space misses (its real value)."""
    null_med = float(np.median(null)) if len(null) else 0.0
    seen: set = set()
    uniq = []
    for kind in SEMANTIC_REACH_KINDS:
        for a, b in edges_by_kind.get(kind, []):
            key = (a, b) if a < b else (b, a)
            if key not in seen:
                seen.add(key)
                uniq.append((a, b))
    cos = _pair_cosines(uniq, idx, vecs)
    if not len(cos):
        return {"pairs": 0, "non_semantic_fraction": None, "null_median": round(null_med, 3)}
    return {
        "pairs": int(len(cos)),
        "non_semantic_fraction": round(float((cos <= null_med).mean()), 3),
        "median_cosine": round(float(np.median(cos)), 3),
        "null_median": round(null_med, 3),
    }


def community_coherence(slugs, vecs, community_of) -> dict:
    """Do the graph's Louvain communities coincide with embedding-space clusters?
    Silhouette of the community labels under cosine distance + ARI against k-means on the
    same vectors. Needs scikit-learn (the ``[analysis]`` extra); absent ⇒ not available."""
    labels = np.asarray([community_of.get(s, -1) for s in slugs])
    have = labels >= 0
    if have.sum() < 3 or len(set(labels[have].tolist())) < 2:
        return {"available": False, "reason": "need ≥2 communities over ≥3 pages"}
    try:
        from sklearn.cluster import KMeans
        from sklearn.metrics import adjusted_rand_score, silhouette_score
    except Exception:
        return {"available": False, "reason": "install .[analysis] (scikit-learn) for silhouette/ARI"}
    v = vecs[have]
    lab = labels[have]
    ncl = len(set(lab.tolist()))
    sil = float(silhouette_score(v, lab, metric="cosine"))
    km = KMeans(n_clusters=ncl, n_init=10, random_state=0).fit_predict(v)
    ari = float(adjusted_rand_score(lab, km))
    return {"available": True, "silhouette": round(sil, 3), "ari": round(ari, 3),
            "communities": int(ncl), "pages": int(have.sum())}


def analyze_coupling(index, graph, k: int = 8) -> dict:
    """Run the full graph↔semantic coupling analysis over a loaded ``SemanticIndex`` +
    ``GraphStore`` (read-only). Returns a fingerprint dict (JSON-serializable)."""
    slugs, vecs = page_vectors(index)
    idx = {s: i for i, s in enumerate(slugs)}
    present = set(slugs)
    edges = {
        kind: [(a, b) for a, b in pairs if a in present and b in present]
        for kind, pairs in graph.coupling_edges().items()
    }
    null = _null_cosines(vecs)
    community_of = {s: cid for cid, members in graph.community_members().items() for s in members}
    return {
        "pages": len(slugs),
        "k": k,
        "edge_counts": {kind: len(pairs) for kind, pairs in edges.items()},
        "edge_profile": edge_semantic_profile(edges, idx, vecs, null),
        "neighbor_overlap": neighbor_overlap(edges, slugs, vecs, k=k),
        "graph_reach": graph_reach(edges, idx, vecs, null),
        "community_coherence": community_coherence(slugs, vecs, community_of),
    }
