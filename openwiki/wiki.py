"""Split a :class:`~openwiki.models.ParsedDocument` into linked wiki pages.

The document's table-of-contents outline is the backbone. Entries at or above a
chosen depth (``split_level``) each become a wiki page that owns the contiguous
block of PDF pages between its start and the next page-level entry; deeper
entries become an in-page "Contents" list.

Because extracted text can only be separated at *PDF-page* granularity, outline
entries that begin on the same PDF page are grouped into a single wiki page (the
first is the title, the rest are listed as contents). This module depends only
on :mod:`openwiki.models` — never on PyMuPDF.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .models import ImageRef, ParsedDocument, TableData

# German umlauts transliterate to digraphs so slugs stay readable (ü -> ue).
_TRANSLIT = {
    ord("ä"): "ae", ord("ö"): "oe", ord("ü"): "ue", ord("ß"): "ss",
    ord("Ä"): "ae", ord("Ö"): "oe", ord("Ü"): "ue",
}


def slugify(text: str, max_len: int = 60) -> str:
    """Turn a heading into a filesystem- and URL-safe ASCII slug."""
    text = text.strip().lower().translate(_TRANSLIT)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    if len(text) > max_len:
        text = text[:max_len].rstrip("-")
    return text or "section"


@dataclass
class WikiSubsection:
    """A deeper outline entry surfaced as an in-page contents item."""

    title: str
    level: int


@dataclass
class WikiPage:
    slug: str  # ordinal-prefixed and unique, e.g. "003-vorstellung-des-nautilus"
    title: str
    level: int
    order: int  # 0-based position in document order
    pdf_page_start: int  # inclusive, 1-based
    pdf_page_end: int  # inclusive, 1-based
    text: str = ""
    tables: list[TableData] = field(default_factory=list)
    images: list[ImageRef] = field(default_factory=list)
    subsections: list[WikiSubsection] = field(default_factory=list)
    parent_slug: Optional[str] = None
    child_slugs: list[str] = field(default_factory=list)

    @property
    def filename(self) -> str:
        return f"{self.slug}.md"


@dataclass
class Wiki:
    title: str
    pages: list[WikiPage]
    source: str = ""
    split_level: int = 2

    @property
    def root_pages(self) -> list[WikiPage]:
        return [p for p in self.pages if p.parent_slug is None]

    def by_slug(self, slug: str) -> WikiPage:
        return next(p for p in self.pages if p.slug == slug)


@dataclass
class _Cut:
    """An outline-derived boundary that starts a new wiki page."""

    page: int
    title: str
    level: int
    entry: object = None  # the originating OutlineItem (None for synthetic cuts)


class WikiBuilder:
    """Build a :class:`Wiki` from a :class:`ParsedDocument`."""

    def __init__(self, split_level: int = 2) -> None:
        if split_level < 1:
            raise ValueError("split_level must be >= 1")
        self.split_level = split_level

    def build(self, doc: ParsedDocument) -> Wiki:
        pages_by_num = {p.number: p for p in doc.pages}
        max_page = max(pages_by_num, default=0)

        cuts = self._compute_cuts(doc, max_page)
        pages = self._assemble_pages(doc, cuts, pages_by_num, max_page)
        self._link_tree(pages)

        title = doc.metadata.title or Path(doc.metadata.source_path).stem
        return Wiki(title=title, pages=pages, source=doc.metadata.source_path,
                    split_level=self.split_level)

    # -- internals ------------------------------------------------------

    def _compute_cuts(self, doc: ParsedDocument, max_page: int) -> list[_Cut]:
        cuts: list[_Cut] = []
        for entry in doc.outline:
            if entry.level > self.split_level or not (1 <= entry.page <= max_page):
                continue
            if cuts and cuts[-1].page == entry.page:
                continue  # same PDF page as the current cut -> folded in as contents
            cuts.append(_Cut(page=entry.page, title=entry.title, level=entry.level, entry=entry))

        # Everything before the first cut (covers, TOC page, ...) becomes a page.
        if max_page >= 1 and (not cuts or cuts[0].page > 1):
            cuts.insert(0, _Cut(page=1, title="Front Matter", level=1, entry=None))
        return cuts

    def _assemble_pages(self, doc, cuts, pages_by_num, max_page) -> list[WikiPage]:
        pages: list[WikiPage] = []
        for i, cut in enumerate(cuts):
            start = cut.page
            end = cuts[i + 1].page if i + 1 < len(cuts) else max_page + 1  # exclusive
            src = [pages_by_num[n] for n in range(start, end) if n in pages_by_num]

            text = "\n\n".join(p.text.strip() for p in src if p.text.strip())
            tables = [t for p in src for t in p.tables]
            images = [im for p in src for im in p.images]
            subsections = [
                WikiSubsection(title=e.title, level=e.level)
                for e in doc.outline
                if start <= e.page < end and e is not cut.entry
            ]

            pages.append(
                WikiPage(
                    slug=f"{i:03d}-{slugify(cut.title)}",
                    title=cut.title,
                    level=cut.level,
                    order=i,
                    pdf_page_start=start,
                    pdf_page_end=end - 1,
                    text=text,
                    tables=tables,
                    images=images,
                    subsections=subsections,
                )
            )
        return pages

    def _link_tree(self, pages: list[WikiPage]) -> None:
        stack: list[WikiPage] = []
        for page in pages:
            while stack and stack[-1].level >= page.level:
                stack.pop()
            if stack:
                page.parent_slug = stack[-1].slug
                stack[-1].child_slugs.append(page.slug)
            stack.append(page)


# ---------------------------------------------------------------------------
# Rendering / output
# ---------------------------------------------------------------------------

def write_wiki(wiki: Wiki, out_dir, include_tables: bool = True) -> dict:
    """Write ``index.md``, ``wiki.json``, and one Markdown file per page."""
    out_dir = Path(out_dir)
    pages_dir = out_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    # Clear a prior build's pages so slugs that were renamed or dropped (e.g. after
    # a different --split-level or outline change) don't linger as orphan files —
    # wiki.json/index/graph are rewritten wholesale, so pages/ must be too.
    for stale in pages_dir.glob("*.md"):
        stale.unlink()

    for i, page in enumerate(wiki.pages):
        (pages_dir / page.filename).write_text(
            _render_page(wiki, page, i, include_tables), encoding="utf-8"
        )

    (out_dir / "index.md").write_text(_render_index(wiki), encoding="utf-8")
    manifest = _manifest(wiki)
    (out_dir / "wiki.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def _breadcrumbs(wiki: Wiki, page: WikiPage) -> list[str]:
    chain: list[str] = []
    cur = page.parent_slug
    while cur:
        parent = wiki.by_slug(cur)
        chain.append(f"[{parent.title}]({parent.filename})")
        cur = parent.parent_slug
    chain.reverse()
    chain.append(page.title)  # current page, unlinked
    return chain


def _render_page(wiki: Wiki, page: WikiPage, index: int, include_tables: bool) -> str:
    out = [f"# {page.title}\n"]
    out.append(" › ".join(["[🏠 Home](../index.md)"] + _breadcrumbs(wiki, page)))
    out.append("")
    out.append(f"*PDF pages {page.pdf_page_start}–{page.pdf_page_end}*")
    out.append("")

    if page.child_slugs:
        out.append("**Subpages**\n")
        for slug in page.child_slugs:
            child = wiki.by_slug(slug)
            out.append(f"- [{child.title}]({child.filename})")
        out.append("")

    if page.subsections:
        out.append("**Contents**\n")
        for sub in page.subsections:
            indent = "  " * max(0, sub.level - page.level - 1)
            out.append(f"{indent}- {sub.title}")
        out.append("")

    out.append("---\n")
    if page.text.strip():
        out.append(page.text.strip())
        out.append("")

    if include_tables and page.tables:
        for j, table in enumerate(page.tables, start=1):
            out.append(f"\n**Table {j}** *(PDF p. {table.page_number})*\n")
            out.append(table.to_markdown())
            out.append("")

    for img in page.images:
        out.append(f"\n![p{img.page_number} #{img.xref}](../../images/{Path(img.path).name})")

    out.append("\n---\n")
    nav: list[str] = []
    if index > 0:
        prev = wiki.pages[index - 1]
        nav.append(f"← [{prev.title}]({prev.filename})")
    if page.parent_slug:
        parent = wiki.by_slug(page.parent_slug)
        nav.append(f"↑ [{parent.title}]({parent.filename})")
    if index < len(wiki.pages) - 1:
        nxt = wiki.pages[index + 1]
        nav.append(f"[{nxt.title}]({nxt.filename}) →")
    out.append(" · ".join(nav) if nav else "[🏠 Home](../index.md)")
    out.append("")
    return "\n".join(out)


def _render_index(wiki: Wiki) -> str:
    out = [
        f"# {wiki.title}\n",
        "*Generated wiki — one page per outline section.*\n",
        f"- Source: `{wiki.source}`",
        f"- Pages: {len(wiki.pages)}",
        f"- Split level: {wiki.split_level}",
        "",
        "## Contents",
        "",
    ]

    def emit(page: WikiPage, depth: int) -> None:
        out.append(f"{'  ' * depth}- [{page.title}](pages/{page.filename})")
        for slug in page.child_slugs:
            emit(wiki.by_slug(slug), depth + 1)

    for root in wiki.root_pages:
        emit(root, 0)
    return "\n".join(out) + "\n"


def _manifest(wiki: Wiki) -> dict:
    return {
        "title": wiki.title,
        "source": wiki.source,
        "split_level": wiki.split_level,
        "page_count": len(wiki.pages),
        "pages": [
            {
                "slug": p.slug,
                "title": p.title,
                "level": p.level,
                "file": f"pages/{p.filename}",
                "pdf_page_start": p.pdf_page_start,
                "pdf_page_end": p.pdf_page_end,
                "parent": p.parent_slug,
                "children": p.child_slugs,
                "n_tables": len(p.tables),
                "n_images": len(p.images),
                "subsections": [{"title": s.title, "level": s.level} for s in p.subsections],
            }
            for p in wiki.pages
        ],
    }
