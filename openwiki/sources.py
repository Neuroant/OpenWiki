"""Source dispatch — pick a parser by file type, producing the shared IR.

The pipeline is parser-agnostic: every parser returns a :class:`ParsedDocument`,
so :func:`parse_source` is the one place that maps a file extension to a parser.
:class:`~openwiki.pdf_parser.PDFParser` (PyMuPDF) is imported lazily, so a
Markdown-only setup doesn't need PyMuPDF installed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from .markdown_parser import SUFFIXES as MARKDOWN_SUFFIXES
from .models import ParsedDocument

PathLike = Union[str, Path]

PDF_SUFFIXES = (".pdf",)
SUPPORTED_SUFFIXES = MARKDOWN_SUFFIXES + PDF_SUFFIXES


def source_type(path: PathLike) -> str:
    """Short type tag for a source path: ``pdf`` / ``markdown`` / ``text`` / ``unknown``."""
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        return "pdf"
    if ext == ".txt":
        return "text"
    if ext in MARKDOWN_SUFFIXES:
        return "markdown"
    return "unknown"


def is_supported(path: PathLike) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_SUFFIXES


def parse_source(path: PathLike, *, extract_tables: bool = True, extract_images: bool = False,
                 image_dir: Optional[PathLike] = None,
                 max_pages: Optional[int] = None) -> ParsedDocument:
    """Parse a source file into a :class:`ParsedDocument`, dispatched by extension."""
    ext = Path(path).suffix.lower()
    if ext in MARKDOWN_SUFFIXES:
        from .markdown_parser import MarkdownParser
        return MarkdownParser().parse(path, max_pages=max_pages)
    if ext in PDF_SUFFIXES:
        from .pdf_parser import PDFParser
        return PDFParser(extract_tables=extract_tables, extract_images=extract_images,
                         image_dir=image_dir).parse(path, max_pages=max_pages)
    raise ValueError(
        f"unsupported source type '{ext or '(none)'}': {path}. "
        f"Supported: {', '.join(SUPPORTED_SUFFIXES)}"
    )
