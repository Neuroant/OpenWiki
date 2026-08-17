"""Markdown / plain-text ingestion for OpenWiki.

Turns a ``.md`` / ``.markdown`` / ``.txt`` file into the same :class:`ParsedDocument`
IR that :mod:`openwiki.pdf_parser` produces, so the whole downstream pipeline
(wiki → index → graph) works unchanged. The trick is pagination: a PDF has physical
pages, Markdown has none, so we treat **each heading section as a "page"** — every
ATX heading (``#`` … ``######``) starts a new page whose text runs to the next
heading, and becomes an :class:`OutlineItem` at that page. ``WikiBuilder`` then
splits/gr oups exactly as it does for a PDF's bookmark outline (headings deeper than
``--split-level`` fold into their parent page as subsections).

Stdlib only — no third-party dependency (unlike the PDF path's PyMuPDF). Plain text
with no headings yields a single page (like a bookmark-less PDF); the running-header
outline synthesis does not apply here since Markdown structure is explicit.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional, Union

from .models import DocumentMetadata, OutlineItem, Page, ParsedDocument

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]

# ATX heading: 1–6 '#', a space, the title, optional trailing '#'s.
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
SUFFIXES = (".md", ".markdown", ".txt")


class MarkdownParser:
    """Parse a Markdown/plain-text file into a :class:`ParsedDocument`."""

    def parse(self, path: PathLike, max_pages: Optional[int] = None) -> ParsedDocument:
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"source not found: {path}")

        logger.info("Reading %s", path)
        raw = path.read_text(encoding="utf-8")
        sections = self._split_sections(raw)
        if max_pages is not None:
            sections = sections[:max_pages]

        pages = [Page(number=i + 1, text=text) for i, (_, _, text) in enumerate(sections)]
        outline = [OutlineItem(level=level, title=title, page=i + 1)
                   for i, (level, title, _) in enumerate(sections) if title is not None]
        doc_title = next((t for lvl, t, _ in sections if t and lvl == 1), "") or path.stem

        metadata = DocumentMetadata(source_path=str(path), page_count=len(pages),
                                    title=doc_title, format="markdown")
        logger.info("Done: %d section page(s)", len(pages))
        return ParsedDocument(metadata=metadata, outline=outline, pages=pages)

    # -- internals ------------------------------------------------------

    def _split_sections(self, raw: str):
        """``raw`` → ``[(level, title | None, section_text), ...]``, one per heading.

        The text before the first heading (if any) is a titleless preamble section
        (rendered as front matter). Headings inside fenced code blocks are ignored.
        Each section's text keeps its own heading line, so sub-headings that fold
        into a parent page still render inline (as a PDF page's text would)."""
        sections: list[tuple[int, Optional[str], str]] = []
        state = {"level": 0, "title": None, "buf": []}
        in_fence = False

        def flush():
            text = "\n".join(state["buf"]).strip()
            if state["title"] is not None or text:
                sections.append((state["level"] or 1, state["title"], text))

        for line in raw.splitlines():
            if _FENCE.match(line):
                in_fence = not in_fence
                state["buf"].append(line)
                continue
            heading = None if in_fence else _HEADING.match(line)
            if heading:
                flush()
                state.update(level=len(heading.group(1)), title=heading.group(2).strip(), buf=[line])
            else:
                state["buf"].append(line)
        flush()

        return sections
