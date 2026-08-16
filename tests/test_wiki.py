"""Tests for splitting a parsed document into wiki pages.

These build small, hand-made :class:`ParsedDocument` fixtures so the splitting
logic is asserted deterministically (no dependency on the sample PDF).
"""

from __future__ import annotations

import json

import pytest

from openwiki import PDFParser, ParsedDocument, WikiBuilder, slugify, write_wiki
from openwiki.models import DocumentMetadata, OutlineItem, Page, TableData


def _doc(outline, pages, *, title="Doc", n_pages=None) -> ParsedDocument:
    n_pages = n_pages if n_pages is not None else len(pages)
    return ParsedDocument(
        metadata=DocumentMetadata(source_path="x.pdf", page_count=n_pages, title=title),
        outline=[OutlineItem(*o) for o in outline],
        pages=[Page(number=i + 1, text=t) for i, t in enumerate(pages)],
    )


# -- slugify ----------------------------------------------------------------

@pytest.mark.parametrize(
    "text, expected",
    [
        ("Über die Modi", "ueber-die-modi"),
        ("Quick Layer/Split", "quick-layer-split"),
        ("„Amp“-Sektion", "amp-sektion"),
        ("   ", "section"),
    ],
)
def test_slugify(text, expected):
    assert slugify(text) == expected


# -- splitting --------------------------------------------------------------

@pytest.fixture
def sample_wiki():
    doc = _doc(
        outline=[
            (1, "Intro", 1),
            (2, "Setup", 1),      # same page as Intro -> folded into its page
            (2, "Usage", 2),
            (3, "Details", 2),    # deeper than split_level -> a subsection of Usage
            (1, "Reference", 3),
        ],
        pages=["intro text", "usage text", "reference text"],
    )
    return WikiBuilder(split_level=2).build(doc)


def test_page_count_and_titles(sample_wiki):
    assert [p.title for p in sample_wiki.pages] == ["Intro", "Usage", "Reference"]


def test_page_ranges_and_text(sample_wiki):
    intro, usage, reference = sample_wiki.pages
    assert (intro.pdf_page_start, intro.pdf_page_end) == (1, 1)
    assert (usage.pdf_page_start, usage.pdf_page_end) == (2, 2)
    assert reference.text == "reference text"


def test_same_page_entry_folds_into_contents(sample_wiki):
    intro = sample_wiki.pages[0]
    assert [s.title for s in intro.subsections] == ["Setup"]


def test_deeper_entry_becomes_subsection(sample_wiki):
    usage = sample_wiki.pages[1]
    assert [s.title for s in usage.subsections] == ["Details"]


def test_tree_links(sample_wiki):
    intro, usage, reference = sample_wiki.pages
    assert usage.parent_slug == intro.slug
    assert intro.child_slugs == [usage.slug]
    assert reference.parent_slug is None
    assert [p.title for p in sample_wiki.root_pages] == ["Intro", "Reference"]


def test_slugs_are_unique(sample_wiki):
    slugs = [p.slug for p in sample_wiki.pages]
    assert len(slugs) == len(set(slugs))


def test_front_matter_synthesized_when_content_precedes_outline():
    doc = _doc(outline=[(1, "Chapter", 3)], pages=["cover", "toc", "chapter text"])
    wiki = WikiBuilder(split_level=1).build(doc)
    assert wiki.pages[0].title == "Front Matter"
    assert wiki.pages[0].pdf_page_start == 1
    assert wiki.pages[0].pdf_page_end == 2  # pages before the first outline entry


def test_split_level_one_is_coarser():
    outline = [(1, "A", 1), (2, "A.1", 1), (1, "B", 2), (2, "B.1", 2)]
    doc = _doc(outline=outline, pages=["a", "b"])
    assert [p.title for p in WikiBuilder(split_level=1).build(doc).pages] == ["A", "B"]


def test_invalid_split_level():
    with pytest.raises(ValueError):
        WikiBuilder(split_level=0)


# -- writing + round-trip ---------------------------------------------------

def test_write_wiki(tmp_path, sample_wiki):
    write_wiki(sample_wiki, tmp_path)
    assert (tmp_path / "index.md").is_file()
    page_files = list((tmp_path / "pages").glob("*.md"))
    assert len(page_files) == len(sample_wiki.pages)

    manifest = json.loads((tmp_path / "wiki.json").read_text(encoding="utf-8"))
    assert manifest["page_count"] == len(sample_wiki.pages)

    index = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert "[Intro]" in index and "[Usage]" in index


def test_write_wiki_clears_stale_pages(tmp_path, sample_wiki):
    """A rebuild must not leave orphan .md files from a prior build (different slugs)."""
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir(parents=True)
    orphan = pages_dir / "999-orphan-from-an-old-build.md"
    orphan.write_text("stale", encoding="utf-8")

    write_wiki(sample_wiki, tmp_path)

    assert not orphan.exists()  # the stale page is gone
    slugs = {f.stem for f in pages_dir.glob("*.md")}
    assert slugs == {p.slug for p in sample_wiki.pages}  # exactly the current pages remain


def test_parsed_document_round_trip():
    doc = _doc(outline=[(1, "A", 1)], pages=["hello"])
    doc.pages[0].tables.append(TableData(page_number=1, rows=[["a", "b"]], bbox=(0, 0, 1, 1)))
    restored = ParsedDocument.from_dict(doc.to_dict())
    assert restored.metadata.title == "Doc"
    assert restored.outline[0].title == "A"
    assert restored.pages[0].tables[0].rows == [["a", "b"]]
    assert restored.pages[0].tables[0].bbox == (0, 0, 1, 1)


def test_build_from_real_pdf_smoke():
    """End-to-end on the sample PDF if it's available (first pages only)."""
    from pathlib import Path

    pdf = Path(__file__).resolve().parent.parent / "301357_NAUTILUS_OG_G1.pdf"
    if not pdf.is_file():
        pytest.skip("sample PDF not present")
    doc = PDFParser(extract_tables=False).parse(pdf, max_pages=16)
    wiki = WikiBuilder(split_level=2).build(doc)
    assert len(wiki.pages) >= 2
    assert all(p.slug for p in wiki.pages)
