"""2-D projection of the semantic space, for the Analyse tab's semantic map.

Pure-NumPy **PCA** is always available (the two leading principal axes via SVD). If
``umap-learn`` (the ``[analysis]`` extra) is installed, ``method="auto"``/``"umap"`` uses
it for a neighborhood-preserving layout; otherwise it falls back to PCA. Coordinates are
min-max scaled per axis into ``[0, 1]`` so the frontend can map them straight into the
SVG viewport.
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


def project_2d(vecs: np.ndarray, method: str = "auto", seed: int = 0):
    """Project ``vecs`` (n × dim) to ``(coords[n×2], used_method)``.

    ``method``: ``"pca"`` (pure), ``"umap"`` (requires the extra; falls back to PCA if
    absent), or ``"auto"`` (UMAP if available, else PCA).
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
    return _normalize(_pca_2d(vecs)).astype(np.float32), "pca"
