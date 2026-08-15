"""Tests for Phase 4: multi-source merge + per-source cross-reference resolution."""

from __future__ import annotations

from openwiki.graph.references import extract_references_multi
from openwiki.merge import combine_documents
from openwiki.models import (
    DocumentMetadata, ImageRef, OutlineItem, Page, ParsedDocument, TableData,
)
from openwiki.wiki import WikiBuilder


def _doc(name, texts, chapters=()):
    return ParsedDocument(
        metadata=DocumentMetadata(source_path=name, page_count=len(texts), title=name),
        outline=[OutlineItem(level=lvl, title=title, page=page) for (lvl, title, page) in chapters],
        pages=[Page(number=i + 1, text=t) for i, t in enumerate(texts)],
    )


def test_single_source_is_passthrough():
    d = _doc("A", ["x"], [(1, "Ch", 1)])
    assert combine_documents([d], ["A"]) is d


def test_merge_offsets_pages_and_synthetic_nodes():
    a = _doc("A", ["a1", "a2"], [(1, "A-Ch1", 1), (1, "A-Ch2", 2)])
    b = _doc("B", ["b1", "b2"], [(1, "B-Ch1", 1), (1, "B-Ch2", 2)])
    merged = combine_documents([a, b], ["A", "B"], title="Corpus")

    assert merged.metadata.page_count == 4
    assert [p.number for p in merged.pages] == [1, 2, 3, 4]
    assert merged.pages[2].text == "b1"          # B's first page lands at merged 3

    entries = {(o.level, o.title, o.page) for o in merged.outline}
    assert (1, "A", 1) in entries and (1, "B", 3) in entries   # synthetic source nodes
    assert (2, "A-Ch1", 1) in entries and (2, "B-Ch2", 4) in entries  # originals pushed deeper + offset


def test_merge_shifts_table_and_image_page_numbers():
    a = _doc("A", ["a1", "a2"])
    b = _doc("B", ["b1"])
    b.pages[0].tables = [TableData(page_number=1, rows=[["h"]])]
    b.pages[0].images = [ImageRef(page_number=1, xref=9, path="x", width=2, height=2, ext="png")]
    merged = combine_documents([a, b], ["A", "B"])
    assert merged.pages[2].tables[0].page_number == 3
    assert merged.pages[2].images[0].page_number == 3


def test_references_resolve_within_source_not_across():
    a = _doc("A", ["a1", "a2"], [(1, "A-Ch1", 1), (1, "A-Ch2", 2)])
    b = _doc("B", ["b1", "siehe Seite 1"], [(1, "B-Ch1", 1), (1, "B-Ch2", 2)])
    merged = combine_documents([a, b], ["A", "B"])
    wiki = WikiBuilder(split_level=2).build(merged)

    metas = [
        {"start": 0, "count": 2, "printed_offset": 0},
        {"start": 2, "count": 2, "printed_offset": 0},
    ]
    edges = extract_references_multi(merged, wiki, metas)

    by_start = {p.pdf_page_start: p.slug for p in wiki.pages}
    a1, b1, b2 = by_start[1], by_start[3], by_start[4]
    assert (b2, b1) in edges         # "Seite 1" in B resolves to B's own page 1
    assert (b2, a1) not in edges     # NOT to A's page 1 (a naive single offset would)
