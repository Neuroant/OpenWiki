"""Source dispatch — pick a parser by file type (or URL), producing the shared IR.

The pipeline is parser-agnostic: every parser returns a :class:`ParsedDocument`,
so :func:`parse_source` is the one place that maps a source to a parser. Heavy
parsers are imported **lazily** (``PDFParser`` needs PyMuPDF; ``WebParser`` needs
nothing beyond stdlib), so a text/Markdown-only setup pulls in no extra deps.

A *source* is a local file (``.pdf`` / ``.md`` / ``.markdown`` / ``.txt`` /
``.html`` / ``.htm``) or an ``http(s)`` **URL** (fetched by the web parser).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Union
from urllib.parse import urlparse

from .markdown_parser import SUFFIXES as MARKDOWN_SUFFIXES
from .models import ParsedDocument

PathLike = Union[str, Path]

PDF_SUFFIXES = (".pdf",)
HTML_SUFFIXES = (".html", ".htm")
SUPPORTED_SUFFIXES = MARKDOWN_SUFFIXES + HTML_SUFFIXES + PDF_SUFFIXES


def is_url(source: PathLike) -> bool:
    return str(source).lower().startswith(("http://", "https://"))


def _suffix(source: PathLike) -> str:
    return Path(str(source)).suffix.lower()


def source_type(source: PathLike) -> str:
    """Short type tag: ``web`` / ``code`` / ``pdf`` / ``markdown`` / ``text`` / ``unknown``."""
    if is_url(source):
        return "web"
    if Path(str(source)).is_dir():
        return "code"
    ext = _suffix(source)
    if ext in HTML_SUFFIXES:
        return "web"
    if ext == ".pdf":
        return "pdf"
    if ext == ".txt":
        return "text"
    if ext in MARKDOWN_SUFFIXES:
        return "markdown"
    return "unknown"


def is_supported(source: PathLike) -> bool:
    return is_url(source) or Path(str(source)).is_dir() or _suffix(source) in SUPPORTED_SUFFIXES


def source_exists(source: PathLike) -> bool:
    """Whether a source is available: a URL is assumed reachable; a file/dir must exist."""
    return is_url(source) or Path(str(source)).exists()


def source_stem(source: PathLike) -> str:
    """A filesystem-safe stem for naming outputs, for a file, a directory, or a URL
    (``https://en.wikipedia.org/wiki/Graph_theory`` → ``Graph_theory``)."""
    text = str(source)
    if is_url(text):
        parsed = urlparse(text)
        parts = [p for p in parsed.path.split("/") if p]
        stem = parts[-1] if parts else (parsed.netloc or "page")
        stem = re.sub(r"\.(html?|php|aspx?)$", "", stem, flags=re.IGNORECASE)
        return re.sub(r"[^\w.-]+", "-", stem).strip("-") or "page"
    path = Path(text)
    if path.is_dir():
        return path.resolve().name or "repo"
    return path.stem


def parse_source(source: PathLike, *, extract_tables: bool = True, extract_images: bool = False,
                 image_dir: Optional[PathLike] = None,
                 max_pages: Optional[int] = None) -> ParsedDocument:
    """Parse a source (file or URL) into a :class:`ParsedDocument`, by type."""
    if is_url(source) or _suffix(source) in HTML_SUFFIXES:
        from .html_parser import WebParser
        return WebParser().parse(source, max_pages=max_pages)
    if Path(str(source)).is_dir():
        from .code_parser import CodeParser
        return CodeParser().parse(source, max_pages=max_pages)
    ext = _suffix(source)
    if ext in MARKDOWN_SUFFIXES:
        from .markdown_parser import MarkdownParser
        return MarkdownParser().parse(source, max_pages=max_pages)
    if ext in PDF_SUFFIXES:
        from .pdf_parser import PDFParser
        return PDFParser(extract_tables=extract_tables, extract_images=extract_images,
                         image_dir=image_dir).parse(source, max_pages=max_pages)
    raise ValueError(
        f"unsupported source type '{ext or '(none)'}': {source}. "
        f"Supported: {', '.join(SUPPORTED_SUFFIXES)}, or an http(s) URL."
    )
