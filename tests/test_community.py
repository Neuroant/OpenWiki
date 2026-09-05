"""Tests for the consolidation layer: community detection + summaries + global search.

The detection + prompt tests are pure (no Kuzu); the graph round-trip is gated on
Kuzu inside the test so the pure tests still run without it.
"""

from __future__ import annotations

import numpy as np

from openwiki.graph.community import (
    answer_global, build_global_messages, build_summary_messages,
    detect_communities, parse_summary, summarize_community,
)

# two triangles joined by a single weak bridge — a clean two-community graph
_BARBELL = [
    ("a", "b", 1.0), ("b", "c", 1.0), ("a", "c", 1.0),
    ("d", "e", 1.0), ("e", "f", 1.0), ("d", "f", 1.0),
    ("c", "d", 0.05),
]


class _FakeChat:
    name = "fake"

    def __init__(self, reply="Zusammenfassung."):
        self.reply = reply
        self.seen = []

    def chat(self, messages):
        self.seen.append(messages)
        return self.reply


# -- community detection (pure) ------------------------------------------------

def test_detect_two_clusters():
    comm = detect_communities(_BARBELL)
    assert comm["a"] == comm["b"] == comm["c"]        # one triangle
    assert comm["d"] == comm["e"] == comm["f"]        # the other
    assert comm["a"] != comm["d"]                     # kept apart by the weak bridge
    assert set(comm.values()) == {0, 1}               # contiguous ids


def test_detect_ids_ordered_by_size():
    # a 4-clique + a 2-node component → the larger cluster gets id 0
    edges = [("a", "b", 1.0), ("a", "c", 1.0), ("a", "d", 1.0),
             ("b", "c", 1.0), ("b", "d", 1.0), ("c", "d", 1.0), ("y", "z", 1.0)]
    comm = detect_communities(edges)
    assert comm["a"] == 0 and comm["y"] == 1


def test_detect_isolated_nodes_each_own_community():
    comm = detect_communities([], nodes=["x", "y", "z"])
    assert len(set(comm.values())) == 3


def test_detect_is_deterministic_regardless_of_edge_order():
    assert detect_communities(_BARBELL) == detect_communities(list(reversed(_BARBELL)))


# -- summaries + global answer (fake chat) -------------------------------------

def test_summarize_returns_model_label_and_summary():
    chat = _FakeChat("<think>weighing…</think>Thema: Binärbäume\n\nDie Seiten behandeln Bäume.")
    label, summary = summarize_community(
        chat, [("Baum", "ein baum text"), ("Liste", "eine liste")], fallback_label="Baum")
    assert label == "Binärbäume"                       # model's theme, not the hub title
    assert summary == "Die Seiten behandeln Bäume."    # <think> + Thema line stripped
    user = chat.seen[0][-1]["content"]
    assert "Baum" in user and "Liste" in user


def test_summarize_falls_back_to_hub_title_without_theme_line():
    chat = _FakeChat("Nur eine Zusammenfassung ohne Themenzeile.")
    label, summary = summarize_community(chat, [("A", "x")], fallback_label="Hub Titel")
    assert label == "Hub Titel"                        # fallback kicks in
    assert summary == "Nur eine Zusammenfassung ohne Themenzeile."


def test_parse_summary_variants():
    assert parse_summary("Thema: Sortierung\n\nEin Text.") == ("Sortierung", "Ein Text.")
    assert parse_summary("**Thema:** Bäume**\n\nText.")[0] == "Bäume"   # tolerates markdown
    label, summary = parse_summary("Nur Text ohne Label.")             # no theme line
    assert label == "" and summary == "Nur Text ohne Label."


def test_global_messages_number_communities():
    msgs = build_global_messages("Hauptthemen?",
                                 [("Datenstrukturen", "über Bäume"), ("Algorithmen", "über Sortierung")])
    user = msgs[-1]["content"]
    assert "[1] Datenstrukturen" in user and "[2] Algorithmen" in user
    assert "Hauptthemen?" in user


def test_answer_global_strips_think():
    chat = _FakeChat("<think>…</think>Überblick [1] und [2].")
    out = answer_global(chat, "Hauptthemen?", [("A", "x"), ("B", "y")])
    assert out == "Überblick [1] und [2]."


def test_build_summary_messages_shape():
    msgs = build_summary_messages([("T", "snippet")])
    assert msgs[0]["role"] == "system" and msgs[1]["role"] == "user"
    assert "T" in msgs[1]["content"]


# -- graph round-trip (gated on Kuzu) ------------------------------------------

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


def test_community_layer_roundtrip_in_graph(tmp_path):
    import pytest
    pytest.importorskip("kuzu")
    from openwiki.graph import GraphBuilder, GraphStore
    from openwiki.search import SemanticIndex
    from openwiki.wiki import Wiki, WikiPage

    pages = [
        WikiPage(slug="000-a", title="Alpha", level=1, order=0, pdf_page_start=1,
                 pdf_page_end=1, text="alpha nautilus alpha"),
        WikiPage(slug="001-b", title="Beta", level=1, order=1, pdf_page_start=2,
                 pdf_page_end=2, text="beta nautilus beta"),
        WikiPage(slug="002-c", title="Gamma", level=1, order=2, pdf_page_start=3,
                 pdf_page_end=3, text="gamma nautilus gamma"),
    ]
    wiki = Wiki(title="T", pages=pages, source="x.pdf", split_level=2)
    index = SemanticIndex.build(wiki, _FakeEmbedder(), size_words=50, overlap_words=10)
    GraphBuilder(tmp_path / "graph", similar_k=3).build(wiki, index)

    store = GraphStore(tmp_path / "graph", writable=True)
    try:
        assert store.has_communities() is False           # empty until consolidated
        pg = store.page_graph()
        assert set(pg["pages"]) == {"000-a", "001-b", "002-c"}
        assert pg["edges"]                                 # shared "nautilus" → SIMILAR_TO edges

        assignment = detect_communities(pg["edges"], list(pg["pages"]))
        cids = set(assignment.values())
        res = store.upsert_communities(
            assignment, {c: f"summary {c}" for c in cids}, {c: f"label {c}" for c in cids})
        assert res["pages"] == 3

        assert store.has_communities() is True
        comms = store.communities()
        assert sum(c["size"] for c in comms) == 3          # every page assigned
        assert all("summary" in c and "label" in c for c in comms)
    finally:
        store.close()


def test_graph_page_nodes_carry_community(tmp_path):
    import pytest
    pytest.importorskip("kuzu")
    from openwiki.graph import GraphBuilder, GraphStore
    from openwiki.search import SemanticIndex
    from openwiki.wiki import Wiki, WikiPage

    pages = [WikiPage(slug=f"00{i}-p", title=t, level=1, order=i, pdf_page_start=i + 1,
                      pdf_page_end=i + 1, text=txt)
             for i, (t, txt) in enumerate(
                 [("Alpha", "alpha nautilus"), ("Beta", "beta nautilus"), ("Gamma", "gamma nautilus")])]
    wiki = Wiki(title="T", pages=pages, source="x.pdf", split_level=2)
    index = SemanticIndex.build(wiki, _FakeEmbedder(), size_words=50, overlap_words=10)
    GraphBuilder(tmp_path / "graph", similar_k=3).build(wiki, index)

    store = GraphStore(tmp_path / "graph", writable=True)
    try:
        assert store.explore("000-p")["nodes"][0]["community"] is None   # none before consolidation
        pg = store.page_graph()
        assignment = detect_communities(pg["edges"], list(pg["pages"]))
        cids = set(assignment.values())
        store.upsert_communities(assignment, {c: "s" for c in cids}, {c: "l" for c in cids})
        store._page_comm = None                                          # drop the lazy cache

        root = store.explore("000-p")["nodes"][0]
        assert root["root"] and root["community"] == assignment["000-p"]  # colored by its community
    finally:
        store.close()


def test_webapp_surfaces_communities(tmp_path):
    import pytest
    pytest.importorskip("kuzu")
    from openwiki.graph import GraphBuilder, GraphStore
    from openwiki.search import SemanticIndex
    from openwiki.web.server import WikiWebApp
    from openwiki.wiki import Wiki, WikiPage

    pages = [WikiPage(slug=f"00{i}-p", title=t, level=1, order=i, pdf_page_start=i + 1,
                      pdf_page_end=i + 1, text=txt)
             for i, (t, txt) in enumerate(
                 [("Alpha", "alpha nautilus"), ("Beta", "beta nautilus"), ("Gamma", "gamma nautilus")])]
    wiki = Wiki(title="T", pages=pages, source="x.pdf", split_level=2)
    index = SemanticIndex.build(wiki, _FakeEmbedder(), size_words=50, overlap_words=10)
    GraphBuilder(tmp_path / "graph", similar_k=3).build(wiki, index)

    store = GraphStore(tmp_path / "graph", writable=True)
    try:
        pg = store.page_graph()
        assignment = detect_communities(pg["edges"], list(pg["pages"]))
        cids = set(assignment.values())
        store.upsert_communities(assignment, {c: f"summary {c}" for c in cids},
                                 {c: f"Thema {c}" for c in cids})
    finally:
        store.close()

    ro = GraphStore(tmp_path / "graph")           # read-only, as `serve` opens it
    try:
        app = WikiWebApp(tmp_path, graph=ro)      # wiki_dir irrelevant for this query
        comms = app.communities()
        assert comms and sum(c["size"] for c in comms) == 3
        assert all("label" in c and "summary" in c for c in comms)
    finally:
        ro.close()


def test_webapp_ask_global(tmp_path):
    import types

    import pytest
    pytest.importorskip("kuzu")
    from openwiki.graph import GraphBuilder, GraphStore
    from openwiki.search import SemanticIndex
    from openwiki.web.server import WikiWebApp
    from openwiki.wiki import Wiki, WikiPage

    pages = [WikiPage(slug=f"00{i}-p", title=t, level=1, order=i, pdf_page_start=i + 1,
                      pdf_page_end=i + 1, text=txt)
             for i, (t, txt) in enumerate(
                 [("Alpha", "alpha nautilus"), ("Beta", "beta nautilus"), ("Gamma", "gamma nautilus")])]
    wiki = Wiki(title="T", pages=pages, source="x.pdf", split_level=2)
    index = SemanticIndex.build(wiki, _FakeEmbedder(), size_words=50, overlap_words=10)
    GraphBuilder(tmp_path / "graph", similar_k=3).build(wiki, index)

    store = GraphStore(tmp_path / "graph", writable=True)
    try:
        pg = store.page_graph()
        assignment = detect_communities(pg["edges"], list(pg["pages"]))
        cids = set(assignment.values())
        store.upsert_communities(assignment, {c: f"summary {c}" for c in cids},
                                 {c: f"Thema {c}" for c in cids})
    finally:
        store.close()

    ro = GraphStore(tmp_path / "graph")
    try:
        # an agent-like object exposing .chat (as compare()/ask_global read it)
        agent = types.SimpleNamespace(chat=_FakeChat("Überblick [1]."))
        app = WikiWebApp(tmp_path, graph=ro, agent=agent)
        out = app.ask_global("Worum geht es?")
        assert out["cited"] == [1]                      # parsed the [1] marker
        assert out["communities"][0]["marker"] == 1     # 1-based, size-desc order
        assert "[1]" in out["answer"]
    finally:
        ro.close()


def test_upsert_communities_requires_writable(tmp_path):
    import pytest
    pytest.importorskip("kuzu")
    from openwiki.graph import GraphBuilder, GraphStore
    from openwiki.search import SemanticIndex
    from openwiki.wiki import Wiki, WikiPage

    pages = [WikiPage(slug="000-a", title="A", level=1, order=0, pdf_page_start=1,
                      pdf_page_end=1, text="alpha nautilus"),
             WikiPage(slug="001-b", title="B", level=1, order=1, pdf_page_start=2,
                      pdf_page_end=2, text="beta nautilus")]
    wiki = Wiki(title="T", pages=pages, source="x.pdf", split_level=2)
    index = SemanticIndex.build(wiki, _FakeEmbedder(), size_words=50, overlap_words=10)
    GraphBuilder(tmp_path / "graph").build(wiki, index)

    store = GraphStore(tmp_path / "graph")   # read-only
    try:
        with pytest.raises(RuntimeError):
            store.upsert_communities({"000-a": 0}, {0: "s"}, {0: "l"})
    finally:
        store.close()
