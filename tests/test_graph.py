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
    extract_references_multi,
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


def _section_wiki(slugs_pages) -> Wiki:
    pages = [WikiPage(slug=s, title=s.split("-", 1)[-1].title(), level=1, order=i,
                      pdf_page_start=p, pdf_page_end=p, text="x")
             for i, (s, p) in enumerate(slugs_pages)]
    return Wiki(title="T", pages=pages, source="x.pdf", split_level=2)


def test_extract_references_resolves_section_and_chapter():
    # "Abschnitt N.M" / "Kapitel N" resolve via running headers, not page numbers.
    pages = [
        DocPage(number=1, text="Kapitel 1\nGrundlagen"),                  # declares chapter 1
        DocPage(number=2, text="1.2 Vertiefung\nVgl. Abschnitt 1.3 und Kapitel 1."),
        DocPage(number=3, text="1.3 Kernidee\nDer eigentliche Stoff."),   # declares section 1.3
    ]
    doc = ParsedDocument(metadata=DocumentMetadata(source_path="x.pdf", page_count=3), outline=[], pages=pages)
    wiki = _section_wiki([("000-intro", 1), ("001-vert", 2), ("002-kern", 3)])
    edges = extract_references(doc, wiki)
    assert ("001-vert", "002-kern") in edges   # "Abschnitt 1.3" -> section header on physical page 3
    assert ("001-vert", "000-intro") in edges  # "Kapitel 1"    -> chapter start on physical page 1
    assert all(src != dst for src, dst in edges)


def test_extract_references_multi_section_stays_within_source():
    # Each source has its own "1.1"; a "Abschnitt 1.1" must resolve within its source.
    pages = [
        DocPage(number=1, text="1.1 Alpha"),
        DocPage(number=2, text="1.2 Beta\nSiehe Abschnitt 1.1."),   # source 1 -> phys 1
        DocPage(number=3, text="1.1 Gamma"),
        DocPage(number=4, text="1.2 Delta\nSiehe Abschnitt 1.1."),  # source 2 -> phys 3
    ]
    doc = ParsedDocument(metadata=DocumentMetadata(source_path="m", page_count=4), outline=[], pages=pages)
    wiki = _section_wiki([("s1-alpha", 1), ("s1-beta", 2), ("s2-gamma", 3), ("s2-delta", 4)])
    metas = [{"start": 0, "count": 2, "printed_offset": 0},
             {"start": 2, "count": 2, "printed_offset": 0}]
    edges = extract_references_multi(doc, wiki, metas)
    assert ("s1-beta", "s1-alpha") in edges       # within source 1
    assert ("s2-delta", "s2-gamma") in edges      # within source 2
    assert ("s2-delta", "s1-alpha") not in edges  # did NOT leak across sources


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


def test_system_prompt_lists_types_and_excludes_noise():
    from openwiki.graph.entities import _system_prompt

    prompt = _system_prompt({"Algorithm": "named procedural methods"}).lower()
    assert "algorithm" in prompt and "named procedural methods" in prompt
    # the noise categories that inflated the Algorithm bucket must be excluded
    for term in ("author", "keyword", "identifier", "operator"):
        assert term in prompt


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


# -- explorable subgraph (web Graph tab) ------------------------------------

def _entity_key(store, name):
    return store._rows("MATCH (e:Entity {name:$n}) RETURN e.key;", {"n": name})[0][0]


def test_explore_includes_root_and_entities(entity_store):
    g = entity_store.explore("001-b")
    assert g["root"] == "001-b"
    assert any(n.get("root") for n in g["nodes"])
    kinds = {n["kind"] for n in g["nodes"]}
    assert kinds == {"page", "entity"}                         # both node types
    assert any(e["type"] == "mentions" for e in g["edges"])    # page->entity edge


def test_expand_page_has_entity_nodes(entity_store):
    sub = entity_store.expand_page("000-a")
    ent = [n for n in sub["nodes"] if n["kind"] == "entity"]
    assert any(n["label"] == "Arpeggiator" for n in ent)


def test_expand_entity_returns_mentioning_pages(entity_store):
    sub = entity_store.expand_entity(_entity_key(entity_store, "Arpeggiator"))
    assert {n["id"] for n in sub["nodes"]} == {"000-a", "001-b"}
    assert all(e["type"] == "mentions" for e in sub["edges"])


def test_explore_missing_page_raises(entity_store):
    with pytest.raises(KeyError):
        entity_store.explore("999-nope")


def test_webapp_graph_explore_and_expand(entity_store):
    from openwiki.web.server import WikiWebApp

    app = WikiWebApp(entity_store.db_path.parent, graph=entity_store)
    assert app.graph_explore("001-b")["root"] == "001-b"
    expanded = app.graph_expand("entity", _entity_key(entity_store, "Reverb"))
    assert {n["id"] for n in expanded["nodes"]} == {"001-b", "002-c"}


# -- incremental updates on agent edits -------------------------------------

@pytest.fixture
def writable_store(tmp_path):
    wiki = _wiki()
    index = SemanticIndex.build(wiki, FakeEmbedder(), size_words=50, overlap_words=10)
    GraphBuilder(tmp_path / "graph", similar_k=3).build(wiki, index)
    s = GraphStore(tmp_path / "graph", writable=True)
    yield s
    s.close()


def test_upsert_new_page_joins_graph(writable_store):
    res = writable_store.upsert_page(
        "900-new", "# Meine Notizen\n\nalpha nautilus alpha notes.", embedder=FakeEmbedder())
    assert res["title"] == "Meine Notizen" and res["chunks"] >= 1
    assert res["similar"] >= 1                                   # connected via SIMILAR_TO
    nb = writable_store.neighborhood("900-new")
    assert nb["center"] == "900-new"
    assert any(n["rel"] == "similar" for n in nb["nodes"])       # shows in the Graph tab
    # closest existing page for "alpha nautilus" is 000-a
    assert any(e["target"] == "000-a" for e in nb["edges"] if e["type"] == "similar")


def test_upsert_replaces_chunks_on_edit(writable_store):
    def n_chunks():
        return writable_store._rows("MATCH (c:Chunk {page_slug:'900-new'}) RETURN count(c);")[0][0]
    writable_store.upsert_page("900-new", "alpha " * 400 + "nautilus", embedder=FakeEmbedder())
    many = n_chunks()
    writable_store.upsert_page("900-new", "alpha nautilus", embedder=FakeEmbedder())  # shorter
    assert 1 <= n_chunks() < many                               # re-chunked, old chunks gone


def test_upsert_requires_writable(store):
    with pytest.raises(RuntimeError):
        store.upsert_page("x", "text", embedder=FakeEmbedder())


def test_wikitools_create_page_syncs_graph(tmp_path, writable_store):
    from openwiki.tools import WikiTools

    wdir = tmp_path / "w"
    (wdir / "pages").mkdir(parents=True)
    tools = WikiTools(wdir, graph=writable_store, embedder=FakeEmbedder())
    assert tools.create_page("901-note", "Notiz", "alpha nautilus body").startswith("OK")
    assert writable_store.neighborhood("901-note")["center"] == "901-note"
    assert any("synced 901-note" in e for e in tools.edits)


def test_wikitools_no_sync_on_readonly_graph(tmp_path, store):
    from openwiki.tools import WikiTools

    wdir = tmp_path / "w"
    (wdir / "pages").mkdir(parents=True)
    tools = WikiTools(wdir, graph=store, embedder=FakeEmbedder())  # read-only graph
    tools.create_page("902-x", "X", "body")
    assert not any("synced" in e for e in tools.edits)            # no write attempted
