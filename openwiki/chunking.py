"""Chunk wiki page text into overlapping, retrieval-sized pieces.

Chunks are cut from :attr:`WikiPage.text` (the clean extracted text), never from
the rendered Markdown, so navigation/breadcrumbs never pollute the index. Each
chunk keeps provenance (page slug, title, PDF page range) so a search hit can
point back to a specific wiki page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .wiki import Wiki, WikiPage

_WHITESPACE = re.compile(r"\s+")
_BLANK_LINE = re.compile(r"\n\s*\n")


@dataclass
class Chunk:
    id: str  # "<page-slug>#<chunk_index>"
    page_slug: str
    page_title: str
    pdf_page_start: int
    pdf_page_end: int
    chunk_index: int
    text: str


def normalize_text(text: str) -> str:
    """Collapse PDF line-wrapping: single newlines -> spaces, blank lines kept."""
    paragraphs = (_WHITESPACE.sub(" ", p).strip() for p in _BLANK_LINE.split(text))
    return "\n".join(p for p in paragraphs if p)


def chunk_text(text: str, size_words: int = 180, overlap_words: int = 30) -> list[str]:
    """Split text into word windows of ``size_words`` with ``overlap_words`` overlap."""
    words = text.split()
    if not words:
        return []
    size = max(1, size_words)
    overlap = overlap_words if 0 <= overlap_words < size else size // 3
    step = max(1, size - overlap)

    chunks: list[str] = []
    start = 0
    while start < len(words):
        chunks.append(" ".join(words[start : start + size]))
        if start + size >= len(words):
            break
        start += step
    return chunks


def chunk_page(page: WikiPage, size_words: int = 180, overlap_words: int = 30) -> list[Chunk]:
    text = normalize_text(page.text)
    return [
        Chunk(
            id=f"{page.slug}#{i}",
            page_slug=page.slug,
            page_title=page.title,
            pdf_page_start=page.pdf_page_start,
            pdf_page_end=page.pdf_page_end,
            chunk_index=i,
            text=piece,
        )
        for i, piece in enumerate(chunk_text(text, size_words, overlap_words))
    ]


def chunk_wiki(wiki: Wiki, size_words: int = 180, overlap_words: int = 30) -> list[Chunk]:
    chunks: list[Chunk] = []
    for page in wiki.pages:
        chunks.extend(chunk_page(page, size_words, overlap_words))
    return chunks
