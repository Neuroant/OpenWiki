"""Synthesize a document outline from heading patterns when a PDF has no bookmarks.

Many PDFs (lecture notes, scanned books) carry no bookmark tree, so the wiki would
collapse each document into a single page. This detects **numbered section headings**
— typically the running header at the top of each page (e.g. ``10.1 Vererbung und
Polymorphie``) — and emits one flat ``OutlineItem`` per distinct section at the page
it first appears, so ``WikiBuilder`` can split the document into section pages.

Heuristic and text-only (depends only on ``models``); returns ``[]`` when it can't
find a confident set of headings, so callers fall back to the original outline.
"""

from __future__ import annotations

import re
from collections import Counter

from .models import OutlineItem, ParsedDocument

# "10.1  Vererbung und Polymorphie" (title may carry a trailing printed page number)
_HEADING = re.compile(r"^(\d{1,3}(?:\.\d{1,3})*)\s+([^\W\d][^\n]{1,70}?)\s*$")
_TRAILING_NUM = re.compile(r"\s+\d{1,4}$")


def synthesize_outline(doc: ParsedDocument, header_lines: int = 4,
                       min_sections: int = 3) -> list[OutlineItem]:
    """Numbered section headings → a flat outline of section pages (``[]`` if unsure).

    Only the top ``header_lines`` of each page are scanned (where running headers
    live), which keeps table-of-contents body listings from polluting the result.
    """
    seen: dict = {}   # (number, title_lower) -> (number, title, first page)
    for page in doc.pages:
        top = [ln.strip() for ln in page.text.splitlines() if ln.strip()][:header_lines]
        for line in top:
            match = _HEADING.match(line)
            if not match:
                continue
            number = match.group(1)
            title = _TRAILING_NUM.sub("", match.group(2).strip()).strip()
            if len(title) < 3 or title[-1] in ".,:;":
                continue
            key = (number, title.lower())
            if key not in seen:
                seen[key] = (number, title, page.number)
    if not seen:
        return []

    depths = Counter(number.count(".") + 1 for number, _, _ in seen.values())
    # Section granularity = the shallowest depth that has several distinct headings
    # (skips a single all-spanning chapter running header).
    section_depth = next((d for d in sorted(depths) if depths[d] >= 2), min(depths))
    sections = [v for v in seen.values() if v[0].count(".") + 1 == section_depth]
    if len(sections) < min_sections:
        return []
    sections.sort(key=lambda v: v[2])   # by first-appearance page
    return [OutlineItem(level=1, title=title, page=page) for _, title, page in sections]
