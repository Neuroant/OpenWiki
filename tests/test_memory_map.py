"""Direction K (M6): the memory map — facts projected in 2-D (t-SNE keeps neighbourhoods, PCA the fallback does
not), how much of the neighbourhood structure a layout keeps, and the web app's cached map."""

from __future__ import annotations

import json
import sys

import numpy as np
import pytest

from openwiki.analysis.projection import neighbourhood_kept, project_2d
from openwiki.memory_browse import filter_facts, map_points


def test_neighbourhood_kept_scores_a_faithful_layout_high_and_noise_low():
    # points on an arc, unevenly spaced (no distance ties): cosine order = angle order
    angles = np.cumsum(np.random.default_rng(1).uniform(0.01, 0.04, 60))
    vecs = np.stack([np.cos(angles), np.sin(angles)], axis=1)
    faithful = np.stack([angles, np.zeros_like(angles)], axis=1)
    assert neighbourhood_kept(vecs, faithful, k=5) == 1.0
    noise = np.random.default_rng(0).random((60, 2))
    assert neighbourhood_kept(vecs, noise, k=5) < 0.4
    assert neighbourhood_kept(vecs[:1], faithful[:1]) == 0.0  # too few points to say


def _clusters(n=20, dim=16, seed=0):
    rng = np.random.default_rng(seed)
    centers = rng.normal(size=(3, dim)) * 4
    vecs = np.vstack([c + rng.normal(size=(n, dim)) for c in centers])
    return vecs, np.repeat(np.arange(3), n)


def test_tsne_keeps_clusters_apart():
    pytest.importorskip("sklearn")
    vecs, label = _clusters()
    coords, used = project_2d(vecs, "tsne")
    assert used == "tsne" and coords.shape == (60, 2) and coords.min() >= 0 and coords.max() <= 1
    d = ((coords[:, None] - coords[None]) ** 2).sum(-1)
    np.fill_diagonal(d, np.inf)
    nearest = np.argsort(d, axis=1)[:, :5]
    assert (label[nearest] == label[:, None]).mean() > 0.9   # a point's 2-D neighbours are of its cluster


def test_tsne_falls_back_to_pca(monkeypatch):
    vecs, _ = _clusters()
    assert project_2d(vecs[:5], "tsne")[1] == "pca"          # too few points for neighbourhoods
    monkeypatch.setitem(sys.modules, "sklearn.manifold", None)   # scikit-learn missing
    coords, used = project_2d(vecs, "tsne")
    assert used == "pca" and coords.shape == (60, 2)
    assert project_2d(vecs, "auto")[1] in ("pca", "umap")   # "auto" is unchanged (the Analyse tab's map)


def test_map_points_and_the_browsers_ids():
    rows = [{"id": "a", "subject": "OpenWiki", "predicate": "current release is", "object": "v0.1.0",
             "status": "current", "source": "assistant"},
            {"id": "b", "subject": "the database", "predicate": "is", "object": "Kuzu", "status": "past"},
            {"id": "c", "subject": "no", "predicate": "embedding", "object": "here", "status": "current"}]
    pts = map_points(rows, {"a": (0.123456, 1.0), "b": (0.0, 0.5)}, {"a": 7})
    assert [p["id"] for p in pts] == ["a", "b"]                # no position, no point
    assert pts[0] == {"id": "a", "x": 0.1235, "y": 1.0, "theme": 7, "status": "current", "kind": "version",
                      "source": "assistant", "subject": "OpenWiki", "predicate": "current release is",
                      "object": "v0.1.0"}
    page = filter_facts(rows, status="", limit=1, with_ids=True)
    assert len(page["facts"]) == 1 and sorted(page["ids"]) == ["a", "b", "c"]   # every match, not the page
    assert "ids" not in filter_facts(rows)


def test_web_memory_map_is_cached_per_set_of_facts(tmp_path, monkeypatch):
    from test_memory import _MemEmbedder
    from test_memory_browse import _memory_app
    from openwiki.analysis import projection
    from openwiki.graph.memory import MemoryFact

    app, store = _memory_app(tmp_path)
    calls = []
    real = projection.project_2d
    monkeypatch.setattr(projection, "project_2d", lambda v, m="auto", seed=0: calls.append(m) or real(v, m, seed))
    try:
        m = app.memory_map()
        assert m["method"] == "pca" and len(m["points"]) == 3     # three facts: too few for t-SNE
        assert calls == ["tsne"]                                  # auto asks for t-SNE
        assert {p["object"]: p["status"] for p in m["points"]} == {"Kuzu": "past", "Postgres": "current",
                                                                  "Python": "current"}
        assert all(0.0 <= p["x"] <= 1.0 and 0.0 <= p["y"] <= 1.0 for p in m["points"])
        app.memory_map()
        assert calls == ["tsne"]                                  # the same facts: the cached layout
        store.remember("2026-09-07", [MemoryFact("the wiki", "has", "51 pages")], _MemEmbedder())
        assert len(app.memory_map()["points"]) == 4 and calls == ["tsne", "tsne"]   # a new fact: laid out again
        assert app.memory_map("pca")["method"] == "pca" and calls[-1] == "pca"
    finally:
        store.close()


def test_http_memory_map_and_facts_with_ids(tmp_path):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    from test_memory_browse import _memory_app
    from openwiki.web.server import make_handler

    app, store = _memory_app(tmp_path)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def get(path):
        with urllib.request.urlopen(base + path, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    try:
        m = get("/api/memory/map")
        assert len(m["points"]) == 3 and "kept" in m
        facts = get("/api/memory/facts?status=&limit=1&ids=1")
        assert len(facts["facts"]) == 1 and len(facts["ids"]) == 3
        assert "ids" not in get("/api/memory/facts?limit=1")
    finally:
        httpd.shutdown()
        httpd.server_close()
        store.close()
