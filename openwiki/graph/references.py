"""Extract in-text cross-references as Page->Page edges. Two citation styles:

1. **Page numbers** — "auf Seite 47". The catch: the text cites its own *printed*
   page numbers, but our `Page` nodes are keyed by *physical* PDF pages. Front
   matter shifts the two by a constant offset, so we detect that offset first, then
   resolve each reference to the wiki page whose physical span contains the target.
2. **Section/chapter numbers** — "Abschnitt 1.6", "Kapitel 2" (common in lecture
   notes / textbooks). These cite a *section number*, which we resolve via a map
   built from the running headers (``N.M Title`` / ``Kapitel N`` at the top of each
   page) → the physical page that first declares that number → its slug.

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

# Section/chapter cross-references, common in structured texts (lecture notes,
# textbooks): "Abschnitt 1.6", "Abschn. 1.3", "Kapitel 2", "Kap. 7". These cite a
# *section number*, which we resolve via the running headers (below), not a page.
_SECTION_REF = re.compile(r"\b(?:Kapiteln?|Kap\.|Abschnitte?|Abschn\.)\s+(\d{1,3}(?:\.\d{1,3})*)")
# A running header that *declares* a section: "1.6 Semi-Thue-Systeme", "1 Grundbegriffe".
_HEADING = re.compile(r"^(\d{1,3}(?:\.\d{1,3})*)\s+[^\W\d][^\n]{1,70}?\s*$")
_KAPITEL_HEAD = re.compile(r"^Kapitel\s+(\d{1,3})\b")


def _section_page_map(pages, header_lines: int = 3) -> dict:
    """Map a section/chapter *number* → the physical page that first declares it,
    read from the running headers at the top of each page (``N.M Title`` or
    ``Kapitel N``). First appearance wins, so a number resolves to a section's start."""
    secmap: dict = {}
    for page in pages:
        top = [ln.strip() for ln in page.text.splitlines() if ln.strip()][:header_lines]
        for line in top:
            kap = _KAPITEL_HEAD.match(line)
            if kap:
                secmap.setdefault(kap.group(1), page.number)
                continue
            head = _HEADING.match(line)
            if head:
                secmap.setdefault(head.group(1), page.number)
    return secmap


def _section_edges(pages, phys_to_slug: dict) -> set:
    """Section/chapter cross-reference edges within ``pages`` (one source window):
    resolve each ``Abschnitt/Kapitel N.M`` against that window's running-header map."""
    secmap = _section_page_map(pages)
    window = {p.number for p in pages}
    edges = set()
    for page in pages:
        src = phys_to_slug.get(page.number)
        if not src:
            continue
        for match in _SECTION_REF.finditer(page.text):
            dst_phys = secmap.get(match.group(1))
            if dst_phys is None or dst_phys not in window:
                continue
            dst = phys_to_slug.get(dst_phys)
            if dst and dst != src:
                edges.add((src, dst))
    return edges


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

    page_refs = len(edges)
    edges |= _section_edges(doc.pages, phys_to_slug)
    logger.info("References: offset=%d, %d page-ref + %d section-ref = %d edge(s)",
                offset, page_refs, len(edges) - page_refs, len(edges))
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
    page_refs = len(edges)

    # Section/chapter refs, resolved within each source's own page window (so a
    # "Kapitel 2" never leaks to another source that also has a chapter 2).
    for meta in sources_meta:
        start, count = int(meta["start"]), int(meta["count"])
        window = [p for p in doc.pages if start < p.number <= start + count]
        edges |= _section_edges(window, phys_to_slug)

    logger.info("References (multi): %d source(s), %d page-ref + %d section-ref = %d edge(s)",
                len(sources_meta), page_refs, len(edges) - page_refs, len(edges))
    return sorted(edges)
