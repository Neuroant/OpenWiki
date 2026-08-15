"""Combine several parsed documents into one corpus (projects Phase 4).

A project may declare multiple ``[[sources]]``; they merge into a **single** wiki +
index + graph. Merging is pure IR manipulation (depends only on ``models``):

- pages are concatenated with a running **page offset** (so page numbers stay unique
  and monotonic across sources), and table/image ``page_number``s shift with them;
- each source is wrapped under a **synthetic top-level outline node** (its name) and
  its own outline is pushed one level deeper, so the wiki gets a clean per-source
  section and slugs from different sources don't collide.

A single source is returned unchanged, so single-source projects behave exactly as
before. Cross-reference resolution across the merge is handled in
``graph.references.extract_references_multi`` (references stay within a source).
"""

from __future__ import annotations

from typing import Sequence

from .models import (
    DocumentMetadata, ImageRef, OutlineItem, Page, ParsedDocument, TableData,
)


def combine_documents(
    docs: Sequence[ParsedDocument],
    names: Sequence[str],
    title: str = "",
) -> ParsedDocument:
    """Merge ``docs`` into one :class:`ParsedDocument`. One doc ⇒ returned as-is."""
    if not docs:
        raise ValueError("combine_documents: no documents")
    if len(docs) == 1:
        return docs[0]
    if len(names) != len(docs):
        raise ValueError("combine_documents: names must match docs")

    pages: list[Page] = []
    outline: list[OutlineItem] = []
    offset = 0  # physical pages emitted so far

    for doc, name in zip(docs, names):
        # Synthetic top-level node: this source becomes one wiki section.
        outline.append(OutlineItem(level=1, title=name, page=offset + 1))
        for item in doc.outline:
            page = item.page + offset if item.page and item.page > 0 else -1
            outline.append(OutlineItem(level=item.level + 1, title=item.title, page=page))

        for pg in doc.pages:
            tables = [
                TableData(page_number=t.page_number + offset, rows=t.rows, bbox=t.bbox)
                for t in pg.tables
            ]
            images = [
                ImageRef(page_number=im.page_number + offset, xref=im.xref, path=im.path,
                         width=im.width, height=im.height, ext=im.ext)
                for im in pg.images
            ]
            pages.append(Page(number=pg.number + offset, text=pg.text,
                              tables=tables, images=images))
        offset += len(doc.pages)

    meta = DocumentMetadata(
        source_path=" + ".join(names),
        page_count=offset,
        title=title or "OpenWiki corpus",
    )
    return ParsedDocument(metadata=meta, outline=outline, pages=pages)
