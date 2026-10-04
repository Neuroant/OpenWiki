"""Hybrid recall (v0.103) — BM25 over the remembered facts fused into the dense recall score.

The scorer is pure (``lexical.terms`` / ``fact_scores``); the store round trip is Kuzu-gated.
"""

from __future__ import annotations

import numpy as np
import pytest

from openwiki.graph.memory import MemoryFact
from openwiki.lexical import fact_scores, stem, terms


def test_terms_drop_stopwords_and_stem():
    assert terms("When did Melanie paint the sunrises?") == terms("Melanie painted a sunrise") == \
        ["melani", "paint", "sunris"]
    assert terms("Wann hat sie die Lautstärke geregelt?") == ["hat", "die", "lautstärk", "geregelt"]
    families = [("painted", "painting", "paints", "paint"), ("hiking", "hikes", "hiked", "hike"),
                ("running", "runs", "run"), ("activities", "activity"), ("glasses", "glass"), ("cakes", "cake")]
    for family in families:
        assert len({stem(w) for w in family}) == 1, family                # the forms of one word meet
    assert [stem(w) for w in ("class", "status", "analysis", "has", "thing")] == \
        ["class", "status", "analysis", "has", "thing"]


def test_fact_scores_are_normalized_to_the_best_match():
    texts = ["Melanie painted a sunrise in 2022", "Caroline attended a support group", "Melanie runs every day"]
    s = fact_scores("When did Melanie paint a sunrise?", texts)
    assert s[0] == 1.0 and s[1] == 0.0 and 0.0 < s[2] < 1.0
    assert fact_scores("what did they do?", texts).tolist() == [0.0, 0.0, 0.0]     # stopwords only → no signal
    assert fact_scores("anything", []).size == 0


class _Emb:
    """Dense space that cannot tell the two pets apart — only the names differ."""
    name = "fake:hybrid"

    def _vec(self, text):
        low = text.lower()
        v = np.array([float("pet" in low or "cat" in low or "dog" in low), float("adopt" in low), 0.1],
                     dtype=np.float32)
        return v

    def embed_documents(self, texts):
        return np.vstack([self._vec(t) for t in texts])

    def embed_query(self, text):
        return self._vec(text)


def _store(tmp_path):
    pytest.importorskip("kuzu")
    from openwiki.graph import GraphBuilder, GraphStore
    from openwiki.search import SemanticIndex
    from openwiki.wiki import Wiki, WikiPage

    pages = [WikiPage(slug="000-a", title="A", level=1, order=0, pdf_page_start=1, pdf_page_end=1,
                      text="pets adopt")]
    wiki = Wiki(title="T", pages=pages, source="x.pdf", split_level=1)
    GraphBuilder(tmp_path / "graph").build(wiki, SemanticIndex.build(wiki, _Emb(), size_words=50, overlap_words=10))
    return GraphStore(tmp_path / "graph", writable=True)


def test_hybrid_recall_picks_by_keyword_but_keeps_dense_order(tmp_path):
    store, emb = _store(tmp_path), _Emb()
    q = "What is the name of the cat Ann adopted?"
    try:
        store.remember("s1", [MemoryFact("Ann", "adopted a dog named", "Rex"),
                              MemoryFact("Ann", "adopted a cat named", "Tom"),
                              MemoryFact("Ann", "likes", "hiking")], emb, now=1000)
        dense = store.recall(q, emb, k=3, now=2000)
        assert "lexical" not in dense[0]                                  # off by default: dense only
        tied = [h for h in dense if h["object"] in ("Rex", "Tom")]
        assert tied[0]["score"] == tied[1]["score"]                       # dense alone can't separate them
        assert [h["object"] for h in store.recall(q, emb, k=1, now=2000, lexical=0.2)] == ["Tom"]   # BM25 picks
        hybrid = store.recall(q, emb, k=3, now=2000, lexical=0.2)
        assert [h["id"] for h in hybrid] == [h["id"] for h in dense]      # the same set keeps the dense order
        assert {h["object"]: h["lexical"] for h in hybrid}["Tom"] == 1.0  # "cat" is the one distinctive term
        ctx = store.context_for(q, emb, k=1, lexical=0.2)
        assert "Tom" in ctx and "Rex" not in ctx
    finally:
        store.close()


def test_hybrid_recall_never_promotes_from_outside_the_dense_pool(tmp_path):
    store, emb = _store(tmp_path), _Emb()
    try:                                      # four pet facts outrank a keyword-only match (dense rank 5)
        store.remember("s1", [MemoryFact(f"owner {n}", "adopted", "a pet") for n in range(4)]
                       + [MemoryFact("Bob", "owns", "a zebra")], emb, now=1000)
        assert store.recall("pet zebra", emb, k=5, now=2000)[-1]["object"] == "a zebra"
        hits = store.recall("pet zebra", emb, k=1, now=2000, lexical=5.0)        # pool = 2 × k = 2
        assert hits[0]["object"] == "a pet"                               # out of the pool: never promoted
        hits = store.recall("pet zebra", emb, k=3, now=2000, lexical=5.0)        # pool = 6: now it may enter
        assert [h["object"] for h in hits] == ["a pet", "a pet", "a zebra"]      # … shown in dense order
        assert hits[0]["lexical"] == 0.0                                  # "pet" is in 4 of 5 facts: no signal
    finally:
        store.close()


def test_the_project_setting_reaches_the_inject_hook(tmp_path, monkeypatch, capsys):
    from openwiki import cli
    from openwiki.project import Project

    root = tmp_path / "brain"
    root.mkdir()
    manifest = root / "openwiki.toml"
    manifest.write_text('[project]\nname = "brain"\n\n[memory]\nenabled = true\n', encoding="utf-8")
    assert Project.load(root).lexical_weight == 0.2                       # on by default
    for value, expected in (("0", 0.0), ("0.35", 0.35), ('"x"', 0.2), ("-1", 0.0)):
        manifest.write_text(f'[project]\nname = "brain"\n\n[memory]\nenabled = true\nlexical_weight = {value}\n',
                            encoding="utf-8")
        assert Project.load(root).lexical_weight == expected
    project = Project.load(root)                                          # lexical_weight = -1 → 0 (off)
    project.graph_path.parent.mkdir(parents=True, exist_ok=True)
    project.graph_path.write_text("", encoding="utf-8")
    seen = []

    class _Graph:
        def context_for(self, prompt, embedder, **kw):
            seen.append(kw)
            return "CTX"

        def close(self):
            pass
    monkeypatch.setattr(cli, "_hook_embedder", lambda p: object())
    monkeypatch.setattr(cli, "_open_reader", lambda *a, **kw: _Graph())
    cli._hook_inject(project, {"prompt": "which port does the server use?"})
    assert seen[-1]["lexical"] == 0.0 and "CTX" in capsys.readouterr().out
    manifest.write_text('[project]\nname = "brain"\n\n[memory]\nenabled = true\n', encoding="utf-8")
    cli._hook_inject(Project.load(root), {"prompt": "which port does the server use?"})
    assert seen[-1]["lexical"] == 0.2
