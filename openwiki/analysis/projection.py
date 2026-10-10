"""2-D projection of the semantic space, for the Analyse tab's semantic map and the Memory tab's map.

Pure-NumPy **PCA** is always available (the two leading principal axes via SVD). If
``umap-learn`` is installed, ``method="auto"``/``"umap"`` uses it for a neighborhood-preserving
layout; otherwise it falls back to PCA. ``method="tsne"`` uses scikit-learn's t-SNE (the
``[analysis]`` extra), falling back to PCA without it — the memory map's choice: on the dev memory
(1,794 facts) PCA kept 4 % of a fact's 10 nearest neighbours, t-SNE 51 % (``neighbourhood_kept``).
Coordinates are min-max scaled per axis into ``[0, 1]`` so the frontend can map them straight into
the SVG viewport.
"""

from __future__ import annotations

import numpy as np


def _normalize(coords: np.ndarray) -> np.ndarray:
    """Per-axis min-max into [0, 1] (a degenerate axis collapses to 0.5)."""
    if not len(coords):
        return coords
    lo = coords.min(axis=0, keepdims=True)
    hi = coords.max(axis=0, keepdims=True)
    span = hi - lo
    span[span == 0] = 1.0
    out = (coords - lo) / span
    # center a collapsed axis instead of pinning it to 0
    flat = (hi - lo)[0] == 0
    out[:, flat] = 0.5
    return out


def _pca_2d(vecs: np.ndarray) -> np.ndarray:
    x = vecs - vecs.mean(axis=0, keepdims=True)
    # right singular vectors are the principal axes; project onto the first two
    _, _, vt = np.linalg.svd(x, full_matrices=False)
    return x @ vt[:2].T


TSNE_MIN_POINTS = 6        # below this t-SNE's neighbourhoods mean little → PCA


def project_2d(vecs: np.ndarray, method: str = "auto", seed: int = 0):
    """Project ``vecs`` (n × dim) to ``(coords[n×2], used_method)``.

    ``method``: ``"pca"`` (pure), ``"umap"`` (requires ``umap-learn``; falls back to PCA if
    absent), ``"tsne"`` (scikit-learn's t-SNE, cosine metric — the ``[analysis]`` extra; falls back
    to PCA if absent or with fewer than ``TSNE_MIN_POINTS`` points), or ``"auto"`` (UMAP if
    available, else PCA).
    """
    vecs = np.asarray(vecs, dtype=np.float32)
    n = len(vecs)
    if n == 0:
        return np.zeros((0, 2), dtype=np.float32), "none"
    if n < 3:
        # too few points for either method to be meaningful — lay them on a line
        coords = np.zeros((n, 2), dtype=np.float32)
        coords[:, 0] = np.linspace(0.0, 1.0, n)
        coords[:, 1] = 0.5
        return coords, "trivial"
    if method in ("auto", "umap"):
        try:
            import umap  # type: ignore
            reducer = umap.UMAP(n_components=2, random_state=seed,
                                n_neighbors=min(15, n - 1), metric="cosine")
            return _normalize(reducer.fit_transform(vecs)).astype(np.float32), "umap"
        except Exception:
            if method == "umap":
                pass  # requested but unavailable → fall through to PCA
    if method == "tsne" and n >= TSNE_MIN_POINTS:
        try:
            from sklearn.manifold import TSNE  # type: ignore
            tsne = TSNE(n_components=2, metric="cosine", init="pca", random_state=seed,
                        perplexity=float(min(30.0, (n - 1) / 3.0)))
            return _normalize(tsne.fit_transform(vecs)).astype(np.float32), "tsne"
        except Exception:
            pass      # scikit-learn missing (or failing) → PCA
    return _normalize(_pca_2d(vecs)).astype(np.float32), "pca"


def neighbourhood_kept(vecs: np.ndarray, coords: np.ndarray, k: int = 10) -> float:
    """How much of the neighbourhood structure a 2-D layout keeps: the mean share of each point's ``k`` nearest
    neighbours by cosine in the full space that are also among its ``k`` nearest in ``coords`` (random coordinates
    score about ``k / n``). ``0.0`` with too few points to say."""
    vecs = np.asarray(vecs, dtype=np.float32)
    coords = np.asarray(coords, dtype=np.float32)
    n = len(vecs)
    k = min(k, n - 1)
    if k < 1:
        return 0.0
    unit = vecs / (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-12)
    sim = unit @ unit.T
    np.fill_diagonal(sim, -np.inf)
    dist = ((coords[:, None, :] - coords[None, :, :]) ** 2).sum(-1)
    np.fill_diagonal(dist, np.inf)
    high = np.argpartition(-sim, k - 1, axis=1)[:, :k]
    low = np.argpartition(dist, k - 1, axis=1)[:, :k]
    return round(float(np.mean([len(set(a) & set(b)) / k for a, b in zip(high, low)])), 3)
