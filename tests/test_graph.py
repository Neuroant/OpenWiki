"""Tests for the Kuzu graph layer (skipped cleanly if kuzu isn't installed).

Uses a deterministic ``FakeEmbedder`` so no Ollama/network is needed; builds a
small graph in a temp dir and asserts structure, similarity edges, neighborhood
queries, and the hybrid vector->graph query.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("kuzu")  # skip the whole module if Kuzu is unavailable

import json

from openwiki.graph import (
    GraphBuilder, GraphStore, detect_page_offset, extract_entities, extract_references,
)
from openwiki.models import DocumentMetadata, ParsedDocument
from openwiki.models import Page as DocPage
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


# -- cross-reference edges --------------------------------------------------

def _ref_doc() -> ParsedDocument:
    # offset 2: physical page p prints number (p - 2); front matter on 1-2.
    pages = [
        DocPage(number=1, text="Deckblatt"),
        DocPage(number=2, text="Inhalt"),
        DocPage(number=3, text="1 Einleitung. Siehe Seite 4 fuer Details. nautilus"),
        DocPage(number=4, text="2 Fortsetzung. nautilus"),
        DocPage(number=5, text="3 Kapitel B. nautilus beta"),
        DocPage(number=6, text="4 Kapitel C. nautilus gamma"),
    ]
    return ParsedDocument(
        metadata=DocumentMetadata(source_path="x.pdf", page_count=6), outline=[], pages=pages)


def _ref_wiki() -> Wiki:
    pages = [
        WikiPage(slug="000-a", title="A", level=1, order=0, pdf_page_start=3,
                 pdf_page_end=4, text="alpha nautilus"),
        WikiPage(slug="001-b", title="B", level=1, order=1, pdf_page_start=5,
                 pdf_page_end=5, text="beta nautilus"),
        WikiPage(slug="002-c", title="C", level=1, order=2, pdf_page_start=6,
                 pdf_page_end=6, text="gamma nautilus"),
    ]
    return Wiki(title="T", pages=pages, source="x.pdf", split_level=2)


def test_detect_page_offset():
    assert detect_page_offset(_ref_doc()) == 2


def test_extract_references_resolves_printed_to_physical():
    edges = extract_references(_ref_doc(), _ref_wiki())
    assert ("000-a", "002-c") in edges         # "Seite 4" (printed) -> physical 6 -> C
    assert all(src != dst for src, dst in edges)  # no self-references


def test_extract_references_wrong_offset_does_not_resolve():
    # printed 4 + offset 0 = physical 4, which is page A itself -> excluded
    assert ("000-a", "002-c") not in extract_references(_ref_doc(), _ref_wiki(), offset=0)


def test_graph_references_and_referenced_by(tmp_path):
    wiki = _ref_wiki()
    index = SemanticIndex.build(wiki, FakeEmbedder(), size_words=50, overlap_words=10)
    refs = extract_references(_ref_doc(), wiki)
    GraphBuilder(tmp_path / "graph").build(wiki, index, references=refs)
    store = GraphStore(tmp_path / "graph")
    try:
        assert store.stats()["references"] >= 1
        out = store.neighborhood("000-a")
        assert any(n["rel"] == "references" and n["slug"] == "002-c" for n in out["nodes"])
        incoming = store.neighborhood("002-c")
        assert any(n["rel"] == "referenced_by" and n["slug"] == "000-a" for n in incoming["nodes"])
    finally:
        store.close()


def test_build_is_idempotent(tmp_path):
    wiki = _wiki()
    index = SemanticIndex.build(wiki, FakeEmbedder(), size_words=50, overlap_words=10)
    builder = GraphBuilder(tmp_path / "graph", similar_k=3)
    builder.build(wiki, index)
    stats = builder.build(wiki, index)  # rebuild over existing db
    assert stats["pages"] == 3
    GraphStore(tmp_path / "graph").close()


# -- find_path --------------------------------------------------------------

def test_find_path_between_pages(store):
    path = store.find_path("000-a", "002-c")
    assert path is not None
    assert path["nodes"][0] == "000-a" and path["nodes"][-1] == "002-c"
    assert path["hops"] >= 1
    assert len(path["rels"]) == path["hops"]


def test_find_path_same_page(store):
    assert store.find_path("000-a", "000-a")["hops"] == 0


def test_find_path_missing_page(store):
    with pytest.raises(KeyError):
        store.find_path("000-a", "999-nope")


# -- graph-aware agent tools ------------------------------------------------

def test_wikitools_graph_neighbors(tmp_path, store):
    from openwiki.tools import WikiTools

    out = WikiTools(tmp_path, graph=store).graph_neighbors("000-a")
    assert "001-b" in out and "child" in out


def test_wikitools_find_path(tmp_path, store):
    from openwiki.tools import WikiTools

    out = WikiTools(tmp_path, graph=store).find_path("000-a", "002-c")
    assert out.startswith("Path") and "002-c" in out


def test_wikitools_graph_tools_require_graph(tmp_path):
    from openwiki.tools import WikiTools

    tools = WikiTools(tmp_path)  # no graph
    assert "unavailable" in tools.graph_neighbors("000-a")
    names = {t["function"]["name"] for t in tools.schemas()}
    assert "graph_neighbors" not in names and "find_path" not in names


def test_wikitools_graph_tools_advertised_with_graph(tmp_path, store):
    from openwiki.tools import WikiTools

    names = {t["function"]["name"] for t in WikiTools(tmp_path, graph=store).schemas()}
    assert {"graph_neighbors", "find_path"} <= names


def test_webapp_serves_neighborhood(store):
    from openwiki.web.server import WikiWebApp

    app = WikiWebApp(store.db_path.parent, graph=store)  # wiki_dir irrelevant here
    result = app.graph_neighborhood("000-a")
    assert result["center"] == "000-a"
    assert any(n["rel"] == "child" for n in result["nodes"])


# -- graph-augmented RAG (edge expansion) -----------------------------------

class _FakeChat:
    name = "fake:chat"

    def chat(self, messages):
        return "Antwort [1]."


def test_rag_expands_along_graph_edges(store):
    from openwiki.agent import RAGAgent

    # Rebuild the same wiki/index the store fixture was built from.
    index = SemanticIndex.build(_wiki(), FakeEmbedder(), size_words=50, overlap_words=10)
    agent = RAGAgent(index, _FakeChat(), top_k=1, graph=store, expand_k=2)

    sources = agent.retrieve("alpha nautilus")   # top seed = 000-a
    kinds = [s.kind for s in sources]
    assert "seed" in kinds and "related" in kinds        # expansion happened
    # 000-a's similar neighbor (002-c) is pulled in; its child (001-b) is not a
    # semantic/reference edge, so it must not appear as a related source.
    related = [s.page_slug for s in sources if s.kind == "related"]
    assert "002-c" in related
    assert [s.marker for s in sources] == list(range(1, len(sources) + 1))  # contiguous


def test_rag_expand_k_zero_disables(store):
    from openwiki.agent import RAGAgent

    index = SemanticIndex.build(_wiki(), FakeEmbedder(), size_words=50, overlap_words=10)
    agent = RAGAgent(index, _FakeChat(), top_k=1, graph=store, expand_k=0)
    assert all(s.kind == "seed" for s in agent.retrieve("alpha nautilus"))


# -- entity extraction + MENTIONS -------------------------------------------

class _EntityChat:
    """Deterministic 'extractor': returns known entities found in the page text."""

    name = "fake:entities"
    KEYWORDS = {"arpeggiator": ("Arpeggiator", "Feature"), "reverb": ("Reverb", "Effect")}

    def chat(self, messages):
        text = messages[-1]["content"].lower()
        found = [{"name": n, "type": t} for k, (n, t) in self.KEYWORDS.items() if k in text]
        return json.dumps(found)


def _entity_wiki() -> Wiki:
    pages = [
        WikiPage(slug="000-a", title="A", level=1, order=0, pdf_page_start=1,
                 pdf_page_end=1, text="der Arpeggiator ist nuetzlich. nautilus"),
        WikiPage(slug="001-b", title="B", level=1, order=1, pdf_page_start=2,
                 pdf_page_end=2, text="Arpeggiator und Reverb zusammen. nautilus"),
        WikiPage(slug="002-c", title="C", level=1, order=2, pdf_page_start=3,
                 pdf_page_end=3, text="Reverb Hall Programm. nautilus"),
    ]
    return Wiki(title="T", pages=pages, source="x.pdf", split_level=2)


def test_extract_entities_resolves_and_links():
    entities = extract_entities(_entity_wiki(), _EntityChat())
    by_name = {e.name: e for e in entities}
    assert set(by_name) == {"Arpeggiator", "Reverb"}
    assert set(by_name["Arpeggiator"].pages) == {"000-a", "001-b"}  # merged across pages
    assert by_name["Reverb"].type == "Effect"


@pytest.fixture
def entity_store(tmp_path):
    wiki = _entity_wiki()
    index = SemanticIndex.build(wiki, FakeEmbedder(), size_words=50, overlap_words=10)
    entities = extract_entities(wiki, _EntityChat())
    GraphBuilder(tmp_path / "graph").build(wiki, index, entities=entities)
    s = GraphStore(tmp_path / "graph")
    yield s
    s.close()


def test_entity_graph_stats(entity_store):
    assert entity_store.has_entities()
    assert entity_store.stats()["entities"] == 2
    assert entity_store.stats()["mentions"] >= 3


def test_entities_for_page(entity_store):
    names = {e["name"] for e in entity_store.entities_for_page("001-b")}
    assert names == {"Arpeggiator", "Reverb"}


def test_pages_for_entity_substring(entity_store):
    hits = entity_store.pages_for_entity("arp")  # case-insensitive substring
    slugs = {h["slug"] for h in hits}
    assert slugs == {"000-a", "001-b"}


def test_neighborhood_shared_entity(entity_store):
    # A and B share "Arpeggiator". B is also A's NEXT page, so its node dedups to
    # the structural rel — but the shared_entity edge is still emitted.
    edges = entity_store.neighborhood("000-a")["edges"]
    shared = [e["target"] for e in edges if e["type"] == "shared_entity"]
    assert "001-b" in shared


def test_store_without_entities_is_empty(store):
    assert not store.has_entities()
    assert store.stats()["entities"] == 0


def test_wikitools_find_entity(tmp_path, entity_store):
    from openwiki.tools import WikiTools

    tools = WikiTools(tmp_path, graph=entity_store)
    assert "find_entity" in {t["function"]["name"] for t in tools.schemas()}
    out = tools.find_entity("Arpeggiator")
    assert "000-a" in out and "001-b" in out


def test_find_entity_not_advertised_without_entities(tmp_path, store):
    from openwiki.tools import WikiTools

    names = {t["function"]["name"] for t in WikiTools(tmp_path, graph=store).schemas()}
    assert "find_entity" not in names           # gated on entities existing
    assert "graph_neighbors" in names           # other graph tools still present
