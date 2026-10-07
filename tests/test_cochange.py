"""Git co-changes as a use signal for code corpora (v0.112, ``openwiki/graph/cochange.py``): pair counting with the
popularity normalization and the sweep / noise / top-k limits, a real throwaway git repository, the CO_CHANGED edges
in the graph (neighborhood, coupling, in-place refresh, the web panel) and the ``cochange`` command."""

from __future__ import annotations

import shutil
import subprocess

import numpy as np
import pytest

from openwiki.graph import cochange as cc


def test_pairs_count_normalize_and_limit():
    commits = [(10, ["a.py", "test_a.py"]), (20, ["a.py", "test_a.py", "VERSION"]), (30, ["b.py", "VERSION"]),
               (40, ["b.py", "VERSION"]), (50, ["c.py", "VERSION"]), (60, ["a.py", "VERSION"]),
               (70, [f"f{i}.py" for i in range(50)])]                                # a sweep: skipped
    pairs = {(a, b): (c, w, t) for a, b, c, w, t in cc.cochange_pairs(commits)}
    assert pairs[("a.py", "test_a.py")] == (2, round(2 / (3 * 2) ** 0.5, 4), 20)
    assert ("VERSION", "c.py") not in pairs                                         # seen once: noise
    assert pairs[("VERSION", "b.py")][0] == 2 and pairs[("VERSION", "b.py")][1] < pairs[("a.py", "test_a.py")][1]
    assert not any("f1.py" in p for p in pairs)
    only = cc.cochange_pairs(commits, files={"a.py", "test_a.py"})
    assert [(a, b) for a, b, *_ in only] == [("a.py", "test_a.py")]
    # 12 commits: VERSION changes in all of them — ubiquitous, so it pairs only with the other ubiquitous file
    hist = [(i, ["VERSION", "init.py"] + (["a.py", "test_a.py"] if i % 3 == 0 else ["b.py"] if i % 3 == 1 else []))
            for i in range(12)]                                   # a / test_a / b: 4 commits each — ordinary files
    kept = {(a, b) for a, b, *_ in cc.cochange_pairs(hist)}
    assert ("VERSION", "init.py") in kept and ("a.py", "test_a.py") in kept
    assert not any("VERSION" in p and "init.py" not in p for p in kept) and ("a.py", "init.py") not in kept
    many = [(i, ["hub.py", f"x{i % 9}.py", f"y{i % 9}.py"]) for i in range(9)] * 2
    assert len([p for p in cc.cochange_pairs(many, top_k=2) if "hub.py" in p[:2]]) == 0       # the hub is ubiquitous
    assert cc.cochange_pairs([]) == []


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": __import__("os").environ.get("PATH", ""),
                        "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")})


def _repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git not available")
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    _git(repo, "init", "-q")
    for n, files in enumerate([["pkg/app.js", "pkg/style.css"], ["pkg/app.js", "pkg/style.css", "README.md"],
                               ["pkg/core.py"], ["pkg/app.js", "pkg/style.css"]]):
        for f in files:
            (repo / f).write_text(f"{f} {n}\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", f"c{n}")
    return repo


def test_git_history_to_edges(tmp_path):
    repo = _repo(tmp_path)
    commits = cc.git_commits(repo)
    assert len(commits) == 4 and commits[0][1] == ["pkg/app.js", "pkg/style.css"]           # newest first
    pages = [("001-pkg-app-js", "pkg/app.js"), ("002-pkg-style-css", "pkg/style.css"), ("003-readme", "README.md"),
             ("004-pkg-core-py", "pkg/core.py"), ("000-repo", "repo")]
    edges = cc.cochange_edges(pages, repo)
    assert [(a, b, c) for a, b, c, _w, _t in edges] == [("001-pkg-app-js", "002-pkg-style-css", 3)]
    assert cc.git_commits(tmp_path / "nowhere") == [] and cc.cochange_edges(pages, tmp_path) == []


class _Emb:
    name = "fake:cochange"

    def _v(self, t):
        low = t.lower()
        return np.array([float("button" in low), float("color" in low), float("core" in low), 0.1], dtype=np.float32)

    def embed_documents(self, texts):
        return np.vstack([self._v(t) for t in texts])

    def embed_query(self, t):
        return self._v(t)


def test_cochange_edges_in_the_graph(tmp_path):
    pytest.importorskip("kuzu")
    from openwiki.graph import GraphBuilder, GraphStore
    from openwiki.search import SemanticIndex
    from openwiki.web.server import WikiWebApp
    from openwiki.wiki import Wiki, WikiPage, write_wiki
    pages = [WikiPage(slug=s, title=t, level=2, order=i, pdf_page_start=i + 1, pdf_page_end=i + 1, text=x)
             for i, (s, t, x) in enumerate([("001-app", "pkg/app.js", "button click handler"),
                                            ("002-style", "pkg/style.css", "color rules"),
                                            ("003-core", "pkg/core.py", "core logic")])]
    wiki = Wiki(title="repo", pages=pages, source="repo", split_level=2)
    write_wiki(wiki, tmp_path / "wiki")
    index = SemanticIndex.build(wiki, _Emb(), size_words=50, overlap_words=10)
    stats = GraphBuilder(tmp_path / "graph").build(wiki, index, cochanges=[("001-app", "003-core", 3, 1.0, 1700000000)])
    assert stats["cochange_edges"] == 1
    store = GraphStore(tmp_path / "graph")
    try:
        assert store.has_cochanges()
        nb = store.neighborhood("003-core")               # not adjacent: a neighbour in reading order shows as such
        edge = next(e for e in nb["edges"] if e["type"] == "co_changed")
        assert edge["target"] == "001-app" and edge["count"] == 3 and edge["score"] == 1.0
        assert next(n for n in nb["nodes"] if n["slug"] == "001-app")["rel"] == "co_changed"
        assert store.coupling_edges()["co_changed"] == [("001-app", "003-core")]
        related = WikiWebApp(tmp_path / "wiki", index=index, graph=store).related("001-app")
        assert any(g["key"] == "co_changed" and g["label"] == "Oft zusammen geändert" for g in related["groups"])
    finally:
        store.close()
    store = GraphStore(tmp_path / "graph", writable=True)
    try:
        assert store.replace_cochanges([("002-style", "003-core", 2, 0.5, 1700000100)]) == 1
        assert store.coupling_edges()["co_changed"] == [("002-style", "003-core")]        # replaced, not added
        nb = store.neighborhood("003-core")                 # adjacent in file order, and changed together
        assert next(n for n in nb["nodes"] if n["slug"] == "002-style")["rel"] == "co_changed"
    finally:
        store.close()


def test_the_cochange_command(tmp_path, capsys):
    pytest.importorskip("kuzu")
    from openwiki import cli
    from openwiki.graph import GraphBuilder, GraphStore
    from openwiki.search import SemanticIndex
    from openwiki.wiki import Wiki, WikiPage
    repo = _repo(tmp_path)
    pages = [WikiPage(slug=s, title=t, level=2, order=i, pdf_page_start=i + 1, pdf_page_end=i + 1, text=t)
             for i, (s, t) in enumerate([("001-app", "pkg/app.js"), ("002-style", "pkg/style.css")])]
    wiki = Wiki(title="repo", pages=pages, source=str(repo), split_level=2)
    GraphBuilder(tmp_path / "graph").build(wiki, SemanticIndex.build(wiki, _Emb(), size_words=50, overlap_words=10))
    assert cli.main(["cochange", "--graph", str(tmp_path / "graph"), "--repo", str(repo)]) == 0
    assert "1 co-change edge(s) between 2 page(s)" in capsys.readouterr().out
    store = GraphStore(tmp_path / "graph")
    try:
        assert store.coupling_edges()["co_changed"] == [("001-app", "002-style")]
    finally:
        store.close()
