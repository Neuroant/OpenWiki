"""Tests for inline citation links (#1, ADR-28): the citation phrases behind each
cross-reference ("Abschnitt 1.3", "Seite 4") are extracted, stored on ``REFERENCES``,
served by ``/api/related``, and refreshable in place on an older graph."""

from __future__ import annotations

import json

import numpy as np
import pytest

pytest.importorskip("kuzu")

from openwiki.graph import GraphBuilder, GraphStore, extract_references, extract_references_multi
from openwiki.models import DocumentMetadata, OutlineItem, ParsedDocument
from openwiki.models import Page as DocPage
from openwiki.search import SemanticIndex
from openwiki.wiki import Wiki, WikiBuilder, WikiPage


class _Emb:
    VOCAB = ["grundlagen", "vertiefung", "kern", "stoff"]
    name = "fake:cite"

    def _vec(self, text):
        low = text.lower()
        v = np.array([float(low.count(w)) for w in self.VOCAB], dtype=np.float32)
        return v + 1e-3

    def embed_documents(self, texts):
        return np.vstack([self._vec(t) for t in texts])

    def embed_query(self, text):
        return self._vec(text)


def _doc() -> ParsedDocument:
    pages = [
        DocPage(number=1, text="Kapitel 1\nGrundlagen"),
        # (the wrapped phrase sits below the 3-line running-header zone — a line starting "1.3 …"
        # up there would itself be read as the section-1.3 header)
        DocPage(number=2, text="1.2 Vertiefung\nEinleitung.\nMehr Text.\nVgl. Abschnitt\n"
                               "1.3 und Kapitel 1. Nochmals Abschnitt 1.3."),
        DocPage(number=3, text="1.3 Kernidee\nDer eigentliche Stoff. Zurück zu Abschn. 1.2."),
    ]
    outline = [OutlineItem(level=1, title="Grundlagen", page=1),
               OutlineItem(level=1, title="Vertiefung", page=2),
               OutlineItem(level=1, title="Kernidee", page=3)]
    return ParsedDocument(metadata=DocumentMetadata(source_path="x.pdf", page_count=3),
                          outline=outline, pages=pages)


def _slugs(wiki):
    return {p.title: p.slug for p in wiki.pages}


def test_extract_references_with_labels():
    doc = _doc()
    wiki = WikiBuilder(split_level=1).build(doc)
    s = _slugs(wiki)
    plain = extract_references(doc, wiki)
    labeled = extract_references(doc, wiki, labels=True)
    assert [(a, b) for a, b, _ in labeled] == plain               # same edges, back-compatible
    by_edge = {(a, b): labels for a, b, labels in labeled}
    # a wrapped phrase is whitespace-normalized; a repeated one is recorded once
    assert by_edge[(s["Vertiefung"], s["Kernidee"])] == ["Abschnitt 1.3"]
    assert by_edge[(s["Vertiefung"], s["Grundlagen"])] == ["Kapitel 1"]
    assert by_edge[(s["Kernidee"], s["Vertiefung"])] == ["Abschn. 1.2"]


def test_extract_references_multi_with_labels():
    pages = [DocPage(number=1, text="1.1 Alpha"), DocPage(number=2, text="1.2 Beta\nSiehe Abschnitt 1.1."),
             DocPage(number=3, text="1.1 Gamma"), DocPage(number=4, text="1.2 Delta\nSiehe Abschnitt 1.1.")]
    doc = ParsedDocument(metadata=DocumentMetadata(source_path="m", page_count=4), outline=[], pages=pages)
    wiki = Wiki(title="T", split_level=2, source="m", pages=[
        WikiPage(slug=s, title=s, level=1, order=i, pdf_page_start=p, pdf_page_end=p, text="x")
        for i, (s, p) in enumerate([("s1-a", 1), ("s1-b", 2), ("s2-g", 3), ("s2-d", 4)])])
    metas = [{"start": 0, "count": 2, "printed_offset": 0}, {"start": 2, "count": 2, "printed_offset": 0}]
    got = extract_references_multi(doc, wiki, metas, labels=True)
    assert ("s1-b", "s1-a", ["Abschnitt 1.1"]) in got and ("s2-d", "s2-g", ["Abschnitt 1.1"]) in got


def _build(tmp_path, labels=True):
    doc = _doc()
    wiki = WikiBuilder(split_level=1).build(doc)
    index = SemanticIndex.build(wiki, _Emb(), size_words=50, overlap_words=10)
    refs = extract_references(doc, wiki, labels=labels)
    GraphBuilder(tmp_path / "graph").build(wiki, index, references=refs)
    return doc, wiki, tmp_path / "graph"


def test_graph_citations_roundtrip(tmp_path):
    _, wiki, gpath = _build(tmp_path)
    s = _slugs(wiki)
    store = GraphStore(gpath)
    try:
        cites = store.citations(s["Vertiefung"])
        assert cites == [{"label": "Abschnitt 1.3", "slug": s["Kernidee"], "title": "Kernidee"},
                         {"label": "Kapitel 1", "slug": s["Grundlagen"], "title": "Grundlagen"}]
        assert store.citations(s["Grundlagen"]) == []             # cites nothing
    finally:
        store.close()


def test_unlabeled_edges_yield_no_citations(tmp_path):
    _, wiki, gpath = _build(tmp_path, labels=False)               # pairs only (older callers)
    store = GraphStore(gpath)
    try:
        assert store.citations(_slugs(wiki)["Vertiefung"]) == []
    finally:
        store.close()


def _drop_labels(gpath):
    """Simulate a graph built before citation labels existed."""
    store = GraphStore(gpath, writable=True)
    try:
        store._exec("ALTER TABLE REFERENCES DROP labels;")
    finally:
        store.close()


def test_refresh_references_upgrades_an_older_graph_in_place(tmp_path):
    doc, wiki, gpath = _build(tmp_path, labels=False)
    _drop_labels(gpath)
    s = _slugs(wiki)
    store = GraphStore(gpath, writable=True)
    try:
        assert store.citations(s["Vertiefung"]) == []            # no labels column → graceful
        n_pages = store._rows("MATCH (p:Page) RETURN count(p);")[0][0]
        written = store.refresh_references(extract_references(doc, wiki, labels=True))
        assert written == 3
        assert [c["label"] for c in store.citations(s["Vertiefung"])] == ["Abschnitt 1.3", "Kapitel 1"]
        assert store._rows("MATCH (p:Page) RETURN count(p);")[0][0] == n_pages   # nothing else rebuilt
        assert store._rows("MATCH ()-[r:REFERENCES]->() RETURN count(r);")[0][0] == 3   # replaced, not added
    finally:
        store.close()


def test_cli_references_command_refreshes_in_place(tmp_path):
    from openwiki.cli import main

    doc, wiki, gpath = _build(tmp_path, labels=False)
    _drop_labels(gpath)
    parsed = tmp_path / "doc.json"
    parsed.write_text(json.dumps(doc.to_dict(), ensure_ascii=False), encoding="utf-8")
    assert main(["references", str(parsed), "--graph", str(gpath), "--split-level", "1"]) == 0
    store = GraphStore(gpath)
    try:
        assert store.citations(_slugs(wiki)["Kernidee"])[0]["label"] == "Abschn. 1.2"
    finally:
        store.close()


def test_web_related_carries_citations_and_cited_as(tmp_path):
    from openwiki.web.server import WikiWebApp
    from openwiki.wiki import write_wiki

    _, wiki, gpath = _build(tmp_path)
    write_wiki(wiki, tmp_path / "wiki")
    s = _slugs(wiki)
    store = GraphStore(gpath)
    try:
        out = WikiWebApp(tmp_path / "wiki", graph=store).related(s["Vertiefung"])
        assert {c["label"] for c in out["citations"]} == {"Abschnitt 1.3", "Kapitel 1"}
        refs = {p["slug"]: p for g in out["groups"] if g["key"] == "references" for p in g["pages"]}
        if s["Kernidee"] in refs:                                 # unless shown as a structural neighbour
            assert refs[s["Kernidee"]]["cited_as"] == ["Abschnitt 1.3"]
    finally:
        store.close()
