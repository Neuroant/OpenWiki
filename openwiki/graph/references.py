"""Extract the manual's "siehe Seite N" cross-references as Page->Page edges.

The catch: the manual cites its own **printed** page numbers ("auf Seite 47"),
but our `Page` nodes are keyed by **physical** PDF pages. Front matter shifts the
two by a constant offset, so we detect that offset first, then resolve each
reference to the wiki page whose physical span contains the target.

This module reads a `ParsedDocument` (for clean per-physical-page text) and a
`Wiki` (for the physical->slug mapping). It imports no Kuzu.
"""

from __future__ import annotations

import logging
import re
from collections import Counter

from ..models import ParsedDocument
from ..wiki import Wiki

logger = logging.getLogger(__name__)

_INT = re.compile(r"\b(\d{1,3})\b")
# "Seite 47", "Seiten 47", "auf Seite 145" — capitalized to match the manual's style.
_SEITE = re.compile(r"\bSeiten?\s+(\d{1,3})\b")


def detect_page_offset(doc: ParsedDocument, max_offset: int = 40) -> int:
    """Detect the constant ``physical - printed`` page-number offset.

    Every content page prints its own number ``p - k`` somewhere in the text, so
    the candidate ``k = physical - printed`` spikes at the true offset while noise
    numbers (parameter values, cross-refs) scatter. We take the mode.
    """
    n = doc.metadata.page_count or max((p.number for p in doc.pages), default=0)
    votes: Counter = Counter()
    for page in doc.pages:
        for tok in _INT.findall(page.text):
            printed = int(tok)
            if 1 <= printed <= n:
                k = page.number - printed
                if 0 <= k <= max_offset:
                    votes[k] += 1
    return votes.most_common(1)[0][0] if votes else 0


def _physical_to_slug(wiki: Wiki) -> dict:
    mapping: dict = {}
    for page in wiki.pages:
        for phys in range(page.pdf_page_start, page.pdf_page_end + 1):
            mapping[phys] = page.slug
    return mapping


def extract_references(doc: ParsedDocument, wiki: Wiki, offset=None) -> list:
    """Return sorted unique ``(src_slug, dst_slug)`` cross-reference edges."""
    if offset is None:
        offset = detect_page_offset(doc)
    phys_to_slug = _physical_to_slug(wiki)

    edges = set()
    for page in doc.pages:
        src = phys_to_slug.get(page.number)
        if not src:
            continue
        for match in _SEITE.finditer(page.text):
            printed = int(match.group(1))
            dst = phys_to_slug.get(printed + offset)
            if dst and dst != src:
                edges.add((src, dst))

    logger.info("References: offset=%d, %d edge(s)", offset, len(edges))
    return sorted(edges)


def extract_references_multi(doc: ParsedDocument, wiki: Wiki, sources_meta: list) -> list:
    """Cross-references for a **merged** corpus, resolving each within its source.

    ``sources_meta`` is one dict per source, in merge order, with:
      ``start`` — physical pages emitted *before* this source (0-based),
      ``count`` — this source's page count,
      ``printed_offset`` — this source's own ``physical - printed`` offset.

    A "Seite N" on a page belonging to source *i* targets that source's printed
    page N, i.e. merged physical page ``start_i + (N + printed_offset_i)`` — so a
    reference never leaks across sources.
    """
    phys_to_slug = _physical_to_slug(wiki)
    page_meta: dict = {}
    for meta in sources_meta:
        start = int(meta["start"])
        for local in range(1, int(meta["count"]) + 1):
            page_meta[start + local] = (start, int(meta["printed_offset"]))

    edges = set()
    for page in doc.pages:
        src = phys_to_slug.get(page.number)
        meta = page_meta.get(page.number)
        if not src or meta is None:
            continue
        start, offset = meta
        for match in _SEITE.finditer(page.text):
            printed = int(match.group(1))
            dst = phys_to_slug.get(start + printed + offset)
            if dst and dst != src:
                edges.add((src, dst))

    logger.info("References (multi): %d source(s), %d edge(s)", len(sources_meta), len(edges))
    return sorted(edges)
