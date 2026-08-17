"""Web-page / HTML ingestion for OpenWiki.

Turns an HTML source — an ``http(s)`` **URL** (fetched via stdlib ``urllib``) or a
local ``.html`` / ``.htm`` file — into the same :class:`ParsedDocument` IR, reusing
the Markdown parser's heading→page model: each ``<h1>`` … ``<h6>`` starts a section
page, so ``WikiBuilder`` splits/groups it like any outline. Stdlib only
(``html.parser`` + ``urllib``) — no BeautifulSoup/requests, matching the project's
minimal-install ethos.

Boilerplate tags (``script`` / ``style`` / ``head`` / ``nav`` / ``footer`` /
``aside`` / ``form``) are dropped; the document title comes from ``<title>`` (else
the first ``<h1>``, else the URL/file stem).
"""

from __future__ import annotations

import logging
import re
from html.parser import HTMLParser as _HTMLParser
from pathlib import Path
from typing import Optional, Union
from urllib.request import Request, urlopen

from .markdown_parser import sections_to_document
from .models import ParsedDocument
from .sources import is_url, source_stem

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]
SUFFIXES = (".html", ".htm")

_USER_AGENT = "OpenWiki/1.0 (+https://github.com/Neuroant/OpenWiki)"
# Tags whose content is boilerplate / not article text.
_SKIP_TAGS = {"script", "style", "head", "noscript", "template", "svg",
              "nav", "footer", "aside", "form"}
_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
# Block-level tags: insert a newline so text doesn't run together.
_BLOCK = {"p", "div", "section", "article", "header", "li", "ul", "ol", "tr", "table",
          "blockquote", "pre", "figure", "figcaption", *(_HEADINGS)}
_INLINE_WS = re.compile(r"[ \t\r\f\v]+")
_EXTRA_BLANKS = re.compile(r"\n\s*\n\s*\n+")


def _clean(text: str) -> str:
    text = _INLINE_WS.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return _EXTRA_BLANKS.sub("\n\n", text).strip()


class _Extractor(_HTMLParser):
    """Collect ``(level, title | None, text)`` sections from an HTML stream."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: list[tuple[int, Optional[str], str]] = []
        self.doc_title = ""
        self._skip = 0
        self._in_title = False
        self._heading = 0                      # heading level currently open (0 = none)
        self._level, self._title, self._buf = 0, None, []

    def flush(self) -> None:
        text = _clean("".join(self._buf))
        title = self._title.strip() if self._title is not None else None
        if title is not None or text:
            self.sections.append((self._level or 1, title or None, text))

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "title":
            self._in_title = True
        elif tag in _HEADINGS:
            self.flush()
            self._level, self._title, self._buf, self._heading = _HEADINGS[tag], "", [], _HEADINGS[tag]
        elif tag in _BLOCK:
            self._buf.append("\n")

    def handle_startendtag(self, tag, attrs):
        if not self._skip and tag in ("br", "hr"):
            self._buf.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag == "title":
            self._in_title = False
        elif tag in _HEADINGS:
            self._heading = 0
            self._buf.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.doc_title += data
            return
        if self._heading and self._title is not None:
            self._title += data
        self._buf.append(data)


class WebParser:
    """Parse an HTML URL or local ``.html`` file into a :class:`ParsedDocument`."""

    def __init__(self, timeout: float = 30.0, user_agent: str = _USER_AGENT) -> None:
        self.timeout = timeout
        self.user_agent = user_agent

    def parse(self, source: PathLike, max_pages: Optional[int] = None) -> ParsedDocument:
        html, src = self._load(source)
        extractor = _Extractor()
        extractor.feed(html)
        extractor.flush()

        sections = [s for s in extractor.sections if s[1] is not None or s[2]]
        if max_pages is not None:
            sections = sections[:max_pages]
        logger.info("Done: %d section page(s) from %s", len(sections), src)
        return sections_to_document(
            sections, src, "html",
            title_fallback=extractor.doc_title.strip() or source_stem(source))

    def _load(self, source: PathLike) -> tuple[str, str]:
        if is_url(source):
            logger.info("Fetching %s", source)
            request = Request(str(source), headers={"User-Agent": self.user_agent})
            with urlopen(request, timeout=self.timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace"), str(source)
        path = Path(source)
        if not path.is_file():
            raise FileNotFoundError(f"source not found: {source}")
        return path.read_text(encoding="utf-8", errors="replace"), str(path)
