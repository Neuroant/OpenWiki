"""PDF ingestion for OpenWiki.

Wraps PyMuPDF (``fitz``) to turn a PDF into a :class:`ParsedDocument`: metadata,
the table-of-contents outline, per-page text, tables, and optionally images.

This is the *only* module that imports ``fitz`` — keep PyMuPDF calls contained
here so the rest of OpenWiki depends solely on :mod:`openwiki.models`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Union

# PyMuPDF renamed its import package to ``pymupdf``; the old ``import fitz`` still
# works but prints a deprecation warning. Prefer the new name (aliased to ``fitz`` so
# the rest of this module is unchanged), and fall back for older PyMuPDF versions.
try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz  # PyMuPDF < 1.24.3
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "PyMuPDF is required for PDF parsing. Install it with `pip install PyMuPDF`."
        ) from exc

from .models import (
    DocumentMetadata,
    ImageRef,
    OutlineItem,
    Page,
    ParsedDocument,
    TableData,
)

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]


class PDFParser:
    """Extract structured content from a PDF file.

    Parameters mirror the extraction concerns so callers can trade completeness
    for speed. Table detection is heuristic and by far the slowest step; disable
    it (or cap pages) for fast iteration.
    """

    def __init__(
        self,
        extract_text: bool = True,
        extract_tables: bool = True,
        extract_images: bool = False,
        image_dir: Optional[PathLike] = None,
    ) -> None:
        self.extract_text = extract_text
        self.extract_tables = extract_tables
        self.extract_images = extract_images
        self.image_dir = Path(image_dir) if image_dir else None

    def parse(self, pdf_path: PathLike, max_pages: Optional[int] = None) -> ParsedDocument:
        """Parse ``pdf_path`` into a :class:`ParsedDocument`.

        ``max_pages`` limits parsing to the first N pages (handy for tests and
        quick smoke runs).
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.is_file():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        logger.info("Opening %s", pdf_path)
        with fitz.open(str(pdf_path)) as doc:
            metadata = self._read_metadata(doc, pdf_path)
            outline = self._read_outline(doc)

            if self.extract_images and self.image_dir:
                self.image_dir.mkdir(parents=True, exist_ok=True)

            n = doc.page_count if max_pages is None else min(max_pages, doc.page_count)
            seen_images: set[int] = set()
            pages: list[Page] = []
            for index in range(n):
                page = doc.load_page(index)
                pages.append(self._read_page(doc, page, index + 1, seen_images))
                if (index + 1) % 25 == 0:
                    logger.info("  parsed %d/%d pages", index + 1, n)

        logger.info("Done: %d page(s)", len(pages))
        return ParsedDocument(metadata=metadata, outline=outline, pages=pages)

    # -- per-concern extraction ----------------------------------------

    def _read_metadata(self, doc, pdf_path: Path) -> DocumentMetadata:
        meta = doc.metadata or {}
        return DocumentMetadata(
            source_path=str(pdf_path),
            page_count=doc.page_count,
            title=meta.get("title") or "",
            author=meta.get("author") or "",
            subject=meta.get("subject") or "",
            keywords=meta.get("keywords") or "",
            creator=meta.get("creator") or "",
            producer=meta.get("producer") or "",
            format=meta.get("format") or "",
        )

    def _read_outline(self, doc) -> list[OutlineItem]:
        items: list[OutlineItem] = []
        for level, title, page in doc.get_toc(simple=True):
            items.append(OutlineItem(level=int(level), title=title.strip(), page=int(page)))
        return items

    def _read_page(self, doc, page, number: int, seen_images: set[int]) -> Page:
        result = Page(number=number)
        if self.extract_text:
            result.text = page.get_text("text")
        if self.extract_tables:
            result.tables = self._read_tables(page, number)
        if self.extract_images and self.image_dir:
            result.images = self._read_images(doc, page, number, seen_images)
        return result

    def _read_tables(self, page, number: int) -> list[TableData]:
        tables: list[TableData] = []
        try:
            finder = page.find_tables()
        except Exception as exc:  # PyMuPDF can raise on unusual page content
            logger.warning("Table detection failed on page %d: %s", number, exc)
            return tables
        for tab in finder.tables:
            rows = tab.extract()
            if rows:
                tables.append(TableData(page_number=number, rows=rows, bbox=tuple(tab.bbox)))
        return tables

    def _read_images(self, doc, page, number: int, seen_images: set[int]) -> list[ImageRef]:
        assert self.image_dir is not None
        images: list[ImageRef] = []
        for img in page.get_images(full=True):
            xref = img[0]
            if xref in seen_images:  # the same image can appear on many pages
                continue
            seen_images.add(xref)
            try:
                extracted = doc.extract_image(xref)
            except Exception as exc:
                logger.warning("Image %d extraction failed: %s", xref, exc)
                continue
            ext = extracted.get("ext", "png")
            out_path = self.image_dir / f"image_p{number}_{xref}.{ext}"
            out_path.write_bytes(extracted["image"])
            images.append(
                ImageRef(
                    page_number=number,
                    xref=xref,
                    path=str(out_path),
                    width=extracted.get("width", 0),
                    height=extracted.get("height", 0),
                    ext=ext,
                )
            )
        return images
